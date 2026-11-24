#!/usr/bin/env python3
"""
binocular_controller.py
=======================
Asynchronous dual camera controller with a single shared pupil-detection
pipeline per eye.

Architecture:
  * SingleCameraStream  (x2) : background capture threads, 640x480 @ 30 FPS,
                               every frame stamped with a sequence number.
  * Detection worker    (x2) : runs the active pupil detector exactly once per
                               new camera frame and publishes an EyeSample.
  * Consumers (launcher, eye view, calibration overlay, gaze engine) only read
    the latest EyeSample. They never run detectors themselves, so the cost of
    detection is paid once no matter how many windows are open, and no frame
    is ever processed twice.
"""

import os
import sys
import time
import json
import threading
from collections import deque
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, Any, List

# Silence OpenCV logging noise on Windows
os.environ['OPENCV_LOG_LEVEL'] = 'FATAL'
os.environ['OPENCV_VIDEOIO_DEBUG'] = '0'

import cv2
import numpy as np

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from data.pupil_algorithm import get_detector, extract_pupil_ellipse, to_canonical_name, FRAME_W, FRAME_H

SETTINGS_FILE = os.path.join(_THIS_DIR, "binocular_settings.json")

DEFAULTS = {
    "camera_left_id": 0,
    "camera_right_id": 1,
    "pupil_algorithm": "ArcFit",
    "flip_left_h": False,
    "flip_left_v": True,
    "flip_right_h": True,
    "flip_right_v": False,
    "boundary_left_enabled": True,
    "boundary_left_x": 320,
    "boundary_left_y": 240,
    "boundary_left_r": 110,
    "boundary_left_squeeze_h": 100,
    "boundary_left_squeeze_v": 100,
    "boundary_right_enabled": True,
    "boundary_right_x": 320,
    "boundary_right_y": 240,
    "boundary_right_r": 110,
    "boundary_right_squeeze_h": 100,
    "boundary_right_squeeze_v": 100,
    "mouse_mode": "smoother",
    "mouse_alpha": 0.15,
    "mouse_gain": 1.0,
    "mouse_dpi": 96.0,
    "mouse_grace_ms": 240,
    "mouse_spacebar_click": True,
    # edge reach (gaze_engine._apply_edge_reach): on = cursor stretched toward the screen edges so they are easy to
    # reach; off = cursor exactly where the calibration says (more accurate). App: Tracking > Cursor Tuning > Edge Reach
    "mouse_edge_reach": True,
    "record_calibration": False,
    "show_gaze_pointer": True,         # cyan live-gaze pointer in full calibration, Level 2 fine-tune, re-centre
    "calibration_background": "dark",  # "dark" | "light" background of the three calibration screens (test)
    "auto_recentre": False,            # learn the headset slip from button selections in the app (tracker_service)
    "calibration_end_shift": False,    # add the shift measured at the end of the calibration (app switch, 2026-11-25)
    "pupil_smoothing": 3,              # ArcFit centre/circle smoothing 0..10 (3 = tuned default)
    # single-eye mode: a disabled eye's camera is closed and its sample stays "not connected", so every
    # consumer (calibration, gaze engine, re-centre) falls back to the other eye. Never both disabled.
    "eye_left_enabled": True,
    "eye_right_enabled": True,
    # EMG muscle sensor click (emg_sensor.py; EMG View / app Settings). Same keys as emg_sensor.DEFAULTS.
    "emg_enabled": False,              # a squeeze presses Space (off = test mode in the EMG View)
    "emg_port": "auto",                # "auto" = find the Arduino, or "COM3" etc.
    "emg_baud": 115200,
    "emg_threshold": 150,              # effort must rise above this ...
    "emg_release": 70,                 # ... and fall below this before the next click
    "emg_rest": 40,                    # resting effort when threshold/release were set (Baseline tracking)
    "emg_smooth_ms": 150,              # effort = average of the raw values over this time
    "emg_hold_ms": 50,                 # minimum time above the threshold (twitches ignored)
    "emg_lockout_ms": 300,             # minimum time between clicks
    "emg_baseline": True,              # follow the resting level slowly
    "emg_long_enabled": False,         # long squeeze = gaze mouse on/off (normal click then fires at the end)
    "emg_long_ms": 1000,
}

SQUEEZE_MIN, SQUEEZE_MAX = 40, 160
RADIUS_MIN, RADIUS_MAX = 15, 240


def load_settings() -> Dict[str, Any]:
    cfg = dict(DEFAULTS)
    if os.path.isfile(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            cfg.update({k: saved[k] for k in DEFAULTS if k in saved})
        except Exception:
            pass
    return cfg


def save_settings(cfg: Dict[str, Any]) -> None:
    try:
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, SETTINGS_FILE)
    except Exception:
        pass


# =============================================================================
# Shared per-eye detection result
# =============================================================================
@dataclass
class EyeSample:
    """Latest camera frame + pupil detection for one eye. Treat as read-only."""
    seq: int = -1                    # camera frame sequence number (-1 = nothing yet)
    ts: float = 0.0                  # capture timestamp (time.time())
    connected: bool = False
    frame: Optional[np.ndarray] = None   # 640x480 BGR frame the detection ran on
    pupil: Optional[Tuple[float, float]] = None
    ellipse: Optional[Tuple] = None      # ((cx, cy), (w, h), angle)
    conf: float = 0.0
    latency_ms: float = 0.0
    cam_fps: float = 0.0
    det_fps: float = 0.0


# =============================================================================
# Asynchronous Single Camera Stream (640x480 @ 30 FPS)
# =============================================================================
class SingleCameraStream:
    """Background thread for non-blocking camera capture with frame sequencing."""

    def __init__(self, camera_id: int, flip_h: bool = False, flip_v: bool = False, name: str = "Eye"):
        self.camera_id = int(camera_id)
        self.flip_h = bool(flip_h)
        self.flip_v = bool(flip_v)
        self.name = name

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._target_id = self.camera_id
        self._reopen_requested = False

        self.is_connected = False
        self.latest_frame: Optional[np.ndarray] = None
        self.seq = 0
        self.ts = 0.0
        self.fps = 0.0
        self._fps_hist = deque(maxlen=30)
        self._last_t = 0.0

        self._thread = threading.Thread(target=self._worker, daemon=True, name=f"Stream-{name}")
        self._thread.start()

    def set_camera_id(self, new_id: int):
        """Asynchronously requests camera index switch."""
        with self._lock:
            if self._target_id != new_id or not self.is_connected:
                self._target_id = new_id
                self._reopen_requested = True

    def set_flip(self, flip_h: bool, flip_v: bool):
        """Instantaneous in-memory flip update."""
        with self._lock:
            self.flip_h = bool(flip_h)
            self.flip_v = bool(flip_v)

    def get_stats(self) -> Tuple[bool, float, int]:
        """Returns (connected, fps, seq) without copying any frame."""
        with self._lock:
            return self.is_connected and self.latest_frame is not None, self.fps, self.seq

    def get_frame(self) -> Tuple[bool, Optional[np.ndarray], float]:
        """Returns (connected_bool, bgr_frame_copy, current_fps)."""
        with self._lock:
            if not self.is_connected or self.latest_frame is None:
                return False, None, 0.0
            return True, self.latest_frame.copy(), self.fps

    def get_frame_if_newer(self, last_seq: int):
        """
        Returns (frame_copy, fps, seq, ts) only if a frame newer than last_seq
        exists, otherwise None. Used by the detection worker so each camera
        frame is processed exactly once.
        """
        with self._lock:
            if not self.is_connected or self.latest_frame is None or self.seq == last_seq:
                return None
            return self.latest_frame.copy(), self.fps, self.seq, self.ts

    def stop(self, join_timeout: float = 1.0):
        """Signals worker thread to terminate and waits briefly for the device release."""
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=join_timeout)

    def _open_device(self, dev_id: int):
        """Opens camera at 640x480 via DirectShow (CAP_DSHOW), falling back to any backend."""
        for backend in (cv2.CAP_DSHOW, cv2.CAP_ANY):
            cap = None
            try:
                cap = cv2.VideoCapture(dev_id, backend)
                if cap is not None and cap.isOpened():
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_W)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    return cap
            except Exception:
                pass
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
        return None

    def _worker(self):
        cap = None
        current_dev = -1
        last_failed_attempt = 0.0

        while not self._stop_event.is_set():
            with self._lock:
                target = self._target_id
                reopen = self._reopen_requested
                self._reopen_requested = False

            need_open = reopen or (current_dev != target) or (cap is None and (time.time() - last_failed_attempt > 2.0))

            if need_open:
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
                    cap = None
                    time.sleep(0.05)

                with self._lock:
                    self.is_connected = False
                    self.latest_frame = None
                self._fps_hist.clear()
                self._last_t = 0.0

                current_dev = target
                cap = self._open_device(current_dev)
                if cap is None:
                    last_failed_attempt = time.time()
                    time.sleep(0.2)
                    continue
                last_failed_attempt = 0.0

            if cap is None:
                time.sleep(0.1)
                continue

            try:
                ret, raw = cap.read()
            except Exception:
                ret, raw = False, None

            if not ret or raw is None:
                with self._lock:
                    self.is_connected = False
                time.sleep(0.01)
                continue

            now = time.time()
            if self._last_t > 0:
                dt = now - self._last_t
                if dt > 0:
                    self._fps_hist.append(1.0 / dt)
            self._last_t = now
            cur_fps = float(np.mean(self._fps_hist)) if self._fps_hist else 0.0

            # Enforce the 640x480 working resolution regardless of what the driver delivered
            if raw.shape[1] != FRAME_W or raw.shape[0] != FRAME_H:
                raw = cv2.resize(raw, (FRAME_W, FRAME_H), interpolation=cv2.INTER_AREA)

            with self._lock:
                fh = self.flip_h
                fv = self.flip_v

            if fh and fv:
                proc = cv2.flip(raw, -1)
            elif fh:
                proc = cv2.flip(raw, 1)
            elif fv:
                proc = cv2.flip(raw, 0)
            else:
                proc = raw

            with self._lock:
                self.latest_frame = proc
                self.seq += 1
                self.ts = now
                self.fps = cur_fps
                self.is_connected = True

        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass


# =============================================================================
# Binocular System Controller
# =============================================================================
class BinocularController:
    """Coordinates dual camera streams, shared pupil detection, and settings."""

    EYES = ("left", "right")

    def __init__(self):
        self.cfg = load_settings()
        from binocular_calibrator import BinocularCalibrator     # end-shift switch applies to every calibrator
        BinocularCalibrator.END_SHIFT_ON = bool(self.cfg.get("calibration_end_shift", False))
        self._running = False
        self._alg_listeners = []
        self.stream_left: Optional[SingleCameraStream] = None
        self.stream_right: Optional[SingleCameraStream] = None

        self._det_lock = threading.Lock()
        self._detectors: Dict[str, Any] = {"left": None, "right": None}
        self._reset_requested: Dict[str, bool] = {"left": False, "right": False}

        self._sample_lock = threading.Lock()
        self._samples: Dict[str, EyeSample] = {"left": EyeSample(), "right": EyeSample()}
        self._det_threads: List[threading.Thread] = []
        self.recorder = None                 # active recorder.Recorder or None

    # ------------------------------------------------------------------ lifecycle
    def start(self):
        """Initializes camera streams and detection workers."""
        if self._running:
            return
        if not self.is_eye_enabled("left") and not self.is_eye_enabled("right"):
            self.cfg["eye_left_enabled"] = self.cfg["eye_right_enabled"] = True   # never start blind
        for eye in self.EYES:
            if self.is_eye_enabled(eye):
                self._open_stream(eye)

        self._build_detectors(self.get_pupil_algorithm())
        self._running = True
        for eye in self.EYES:
            t = threading.Thread(target=self._detect_loop, args=(eye,), daemon=True, name=f"Detect-{eye}")
            t.start()
            self._det_threads.append(t)

    def stop(self):
        """Stops detection workers and both camera streams."""
        self.stop_recording()
        self._running = False
        for t in self._det_threads:
            if t.is_alive():
                t.join(timeout=0.5)
        self._det_threads = []
        if self.stream_left:
            self.stream_left.stop()
            self.stream_left = None
        if self.stream_right:
            self.stream_right.stop()
            self.stream_right = None

    def _stream(self, eye: str) -> Optional[SingleCameraStream]:
        return self.stream_left if eye == "left" else self.stream_right

    def _open_stream(self, eye: str):
        s = SingleCameraStream(
            int(self.cfg.get(f"camera_{eye}_id", 0 if eye == "left" else 1)),
            flip_h=self.cfg.get(f"flip_{eye}_h", eye == "right"),
            flip_v=self.cfg.get(f"flip_{eye}_v", eye == "left"),
            name="LeftEye" if eye == "left" else "RightEye",
        )
        if eye == "left":
            self.stream_left = s
        else:
            self.stream_right = s

    # ------------------------------------------------------------------ single-eye mode
    def is_eye_enabled(self, eye: str) -> bool:
        return bool(self.cfg.get(f"eye_{eye}_enabled", True))

    def set_eye_enabled(self, eye: str, enabled: bool) -> bool:
        """Enables/disables one eye. Returns False (and changes nothing) if it would disable both eyes."""
        eye = "left" if eye.lower() == "left" else "right"
        other = "right" if eye == "left" else "left"
        enabled = bool(enabled)
        if enabled == self.is_eye_enabled(eye):
            return True
        if not enabled and not self.is_eye_enabled(other):
            return False
        self.cfg[f"eye_{eye}_enabled"] = enabled
        if enabled:
            # the other eye may have taken this eye's camera index while it was disabled
            if self.is_eye_enabled(other) and self.cfg.get(f"camera_{eye}_id") == self.cfg.get(f"camera_{other}_id"):
                used = self.cfg.get(f"camera_{other}_id")
                self.cfg[f"camera_{eye}_id"] = next(c for c in (0, 1, 2, 3) if c != used)
            with self._det_lock:
                self._reset_requested[eye] = True
            if self._running:
                self._open_stream(eye)
        else:
            stream = self._stream(eye)
            if eye == "left":
                self.stream_left = None          # detection loop sees no stream from now on
            else:
                self.stream_right = None
            if stream is not None:
                stream.stop()                    # releases the camera
            with self._sample_lock:
                self._samples[eye] = EyeSample(connected=False)
        save_settings(self.cfg)
        return True

    # ------------------------------------------------------------------ detection
    def _build_detectors(self, alg_name: str):
        with self._det_lock:
            self._detectors["left"] = self._new_detector(alg_name)
            self._detectors["right"] = self._new_detector(alg_name)

    def _new_detector(self, alg_name: str):
        det = get_detector(alg_name)
        if hasattr(det, "set_smoothing"):
            det.set_smoothing(self.cfg.get("pupil_smoothing", 3))
        return det

    def set_pupil_smoothing(self, level: float):
        """Pupil smoothing 0..10 for both eyes (ArcFit only), saved to settings."""
        level = max(0.0, min(10.0, float(level)))
        self.cfg["pupil_smoothing"] = level
        save_settings(self.cfg)
        with self._det_lock:
            for det in self._detectors.values():
                if hasattr(det, "set_smoothing"):
                    det.set_smoothing(level)

    def _detect_loop(self, eye: str):
        last_seq = -1
        det_hist: deque = deque(maxlen=30)
        last_det_t = 0.0
        was_connected = False

        while self._running:
            stream = self._stream(eye)
            if stream is None:
                time.sleep(0.05)
                continue

            got = stream.get_frame_if_newer(last_seq)
            if got is None:
                connected, _, _ = stream.get_stats()
                if not connected and was_connected:
                    was_connected = False
                    with self._sample_lock:
                        self._samples[eye] = EyeSample(seq=last_seq, ts=time.time(), connected=False)
                time.sleep(0.002)
                continue

            frame, cam_fps, seq, ts = got
            last_seq = seq
            was_connected = True

            with self._det_lock:
                det = self._detectors[eye]
                if self._reset_requested[eye]:
                    self._reset_requested[eye] = False
                    det = self._new_detector(self.get_pupil_algorithm())
                    self._detectors[eye] = det

            boundary = self.get_boundary(eye)
            try:
                rect, lat, conf = extract_pupil_ellipse(frame, det, boundary)
            except Exception as e:
                print(f"[Controller] {eye} detector error: {e}")
                rect, lat, conf = None, 0.0, 0.0

            now = time.perf_counter()
            if last_det_t > 0:
                dt = now - last_det_t
                if dt > 0:
                    det_hist.append(1.0 / dt)
            last_det_t = now
            det_fps = float(np.mean(det_hist)) if det_hist else 0.0

            sample = EyeSample(
                seq=seq, ts=ts, connected=True, frame=frame,
                pupil=(float(rect[0][0]), float(rect[0][1])) if rect is not None else None,
                ellipse=rect, conf=float(conf) if rect is not None else 0.0,
                latency_ms=float(lat), cam_fps=float(cam_fps), det_fps=det_fps,
            )
            with self._sample_lock:
                # checked under the lock: set_eye_enabled(False) writes "not connected" under the same lock after
                # clearing the flag, so a frame detected while the eye was being switched off cannot overwrite it
                if not self.is_eye_enabled(eye):
                    continue
                self._samples[eye] = sample
            rec = self.recorder
            if rec is not None and rec.active:
                rec.add_frame(eye, seq, ts, frame, sample.pupil, sample.conf, sample.latency_ms)

    # ------------------------------------------------------------------ recording
    def start_recording(self, tag: str, duration_s: Optional[float] = None, screen=None):
        """Starts saving raw frames of both eyes (see recorder.py). Returns the Recorder."""
        from recorder import Recorder
        self.stop_recording()
        meta = {
            "algorithm": self.get_pupil_algorithm(),
            "camera_left_id": self.cfg.get("camera_left_id"), "camera_right_id": self.cfg.get("camera_right_id"),
            "flips": {e: (self.cfg.get(f"flip_{e}_h"), self.cfg.get(f"flip_{e}_v")) for e in self.EYES},
            "cage": {e: self.get_boundary(e) for e in self.EYES},
            "enabled": {e: self.is_eye_enabled(e) for e in self.EYES},
            "screen": screen,
        }
        self.recorder = Recorder(tag, meta, duration_s)
        return self.recorder

    def stop_recording(self):
        if self.recorder is not None:
            self.recorder.stop()
        self.recorder = None

    def get_sample(self, eye: str) -> EyeSample:
        """Returns the latest EyeSample for 'left' or 'right' (read-only object)."""
        with self._sample_lock:
            return self._samples["left" if eye.lower() == "left" else "right"]

    def get_samples(self) -> Tuple[EyeSample, EyeSample]:
        """Returns (left_sample, right_sample) atomically."""
        with self._sample_lock:
            return self._samples["left"], self._samples["right"]

    # ------------------------------------------------------------------ cameras
    def set_camera(self, eye: str, camera_id: int):
        """Asynchronously updates camera index for specified eye."""
        camera_id = int(camera_id)
        eye = eye.lower()
        stream = self._stream(eye)
        key = f"camera_{eye}_id"
        if self.cfg.get(key) == camera_id and stream and stream.is_connected:
            return
        self.cfg[key] = camera_id
        if stream:
            stream.set_camera_id(camera_id)
        with self._det_lock:
            self._reset_requested[eye] = True
        save_settings(self.cfg)

    def set_flip(self, eye: str, flip_h: bool, flip_v: bool):
        """Immediately updates in-memory flips for specified eye."""
        eye = eye.lower()
        self.cfg[f"flip_{eye}_h"] = bool(flip_h)
        self.cfg[f"flip_{eye}_v"] = bool(flip_v)
        stream = self._stream(eye)
        if stream:
            stream.set_flip(flip_h, flip_v)
        save_settings(self.cfg)

    # ------------------------------------------------------------------ boundary
    def set_boundary(self, eye: str, x: int, y: int, r: int,
                     squeeze_h: Optional[int] = None, squeeze_v: Optional[int] = None,
                     enabled: Optional[bool] = None):
        """Updates and persists elliptical eyeball boundary parameters."""
        eye_key = "left" if eye.lower() == "left" else "right"
        self.cfg[f"boundary_{eye_key}_x"] = max(10, min(FRAME_W - 10, int(x)))
        self.cfg[f"boundary_{eye_key}_y"] = max(10, min(FRAME_H - 10, int(y)))
        self.cfg[f"boundary_{eye_key}_r"] = max(RADIUS_MIN, min(RADIUS_MAX, int(r)))
        if squeeze_h is not None:
            self.cfg[f"boundary_{eye_key}_squeeze_h"] = max(SQUEEZE_MIN, min(SQUEEZE_MAX, int(squeeze_h)))
        if squeeze_v is not None:
            self.cfg[f"boundary_{eye_key}_squeeze_v"] = max(SQUEEZE_MIN, min(SQUEEZE_MAX, int(squeeze_v)))
        if enabled is not None:
            self.cfg[f"boundary_{eye_key}_enabled"] = bool(enabled)
        save_settings(self.cfg)

    def get_boundary(self, eye: str) -> Dict[str, Any]:
        """Returns boundary dictionary (x, y, r, squeeze_h, squeeze_v, rx, ry, enabled)."""
        eye_key = "left" if eye.lower() == "left" else "right"
        x = self.cfg.get(f"boundary_{eye_key}_x", 320)
        y = self.cfg.get(f"boundary_{eye_key}_y", 240)
        r = self.cfg.get(f"boundary_{eye_key}_r", 110)
        sq_h = self.cfg.get(f"boundary_{eye_key}_squeeze_h", 100)
        sq_v = self.cfg.get(f"boundary_{eye_key}_squeeze_v", 100)
        rx = max(10, int(round(r * sq_h / 100.0)))
        ry = max(10, int(round(r * sq_v / 100.0)))
        return {
            "x": x, "y": y, "r": r,
            "squeeze_h": sq_h, "squeeze_v": sq_v,
            "rx": rx, "ry": ry,
            "enabled": self.cfg.get(f"boundary_{eye_key}_enabled", True),
        }

    # ------------------------------------------------------------------ algorithm
    def add_algorithm_listener(self, callback):
        """Registers a callback invoked with the canonical name on algorithm changes."""
        if callback not in self._alg_listeners:
            self._alg_listeners.append(callback)

    def remove_algorithm_listener(self, callback):
        if callback in self._alg_listeners:
            self._alg_listeners.remove(callback)

    def set_pupil_algorithm(self, alg_name: str):
        """Sets and persists active pupil detection algorithm, rebuilding both detectors."""
        canonical = to_canonical_name(alg_name)
        if self.cfg.get("pupil_algorithm") == canonical:
            return

        self.cfg["pupil_algorithm"] = canonical
        save_settings(self.cfg)
        if self._running:
            self._build_detectors(canonical)

        for listener in list(self._alg_listeners):
            try:
                listener(canonical)
            except Exception:
                pass

    def get_pupil_algorithm(self) -> str:
        return to_canonical_name(self.cfg.get("pupil_algorithm", "ArcFit"))   # old settings fall back to ArcFit

    # ------------------------------------------------------------------ mouse settings
    def get_mouse_settings(self) -> Dict[str, Any]:
        """Returns persisted mouse settings dict matching Eye Tracking Module."""
        return {
            "mouse_mode": self.cfg.get("mouse_mode", "smoother"),
            "mouse_alpha": float(self.cfg.get("mouse_alpha", 0.15)),
            "mouse_gain": float(self.cfg.get("mouse_gain", 1.0)),
            "mouse_dpi": float(self.cfg.get("mouse_dpi", 96.0)),
            "mouse_grace_ms": int(self.cfg.get("mouse_grace_ms", 240)),
            "mouse_spacebar_click": bool(self.cfg.get("mouse_spacebar_click", True)),
            "edge_expansion": bool(self.cfg.get("mouse_edge_reach", True)),
        }

    def set_mouse_settings(self, **kwargs):
        """Updates and persists mouse dynamics settings."""
        for k, v in kwargs.items():
            if k in DEFAULTS:
                self.cfg[k] = v
        save_settings(self.cfg)

    # ------------------------------------------------------------------ status
    def get_status(self) -> Dict[str, Any]:
        """Returns a status summary for UI polling (no frame copies)."""
        out: Dict[str, Any] = {
            "running": self._running,
            "pupil_algorithm": self.get_pupil_algorithm(),
        }
        for eye in self.EYES:
            stream = self._stream(eye)
            conn, fps, seq = stream.get_stats() if stream else (False, 0.0, 0)
            smp = self.get_sample(eye)
            out[eye] = {
                "enabled": self.is_eye_enabled(eye),
                "camera_id": self.cfg.get(f"camera_{eye}_id", 0),
                "connected": conn,
                "fps": fps,
                "seq": seq,
                "det_fps": smp.det_fps,
                "pupil": smp.pupil,
                "conf": smp.conf,
                "latency_ms": smp.latency_ms,
                "flip_h": self.cfg.get(f"flip_{eye}_h", False),
                "flip_v": self.cfg.get(f"flip_{eye}_v", False),
                "boundary": self.get_boundary(eye),
            }
        return out
