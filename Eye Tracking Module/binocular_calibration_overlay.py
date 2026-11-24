#!/usr/bin/env python3
"""
binocular_calibration_overlay.py
================================
Interactive fullscreen calibration overlay for the binocular tracker.

Stages:
  1. CALIB   : 9 static points (approach ring -> 360° collect arc -> flash).
               A preliminary model is fitted after point 9.
  2. PURSUIT : 8 smooth-pursuit paths, 1.3 s settle break before each one,
               live cyan gaze pointer, progressive re-fit after every path.
  3. VAL     : 4 unseen validation points (accuracy + screen-space jitter).
  4. DONE    : final joint fit, JSON + telemetry + chart, callback.

The overlay never runs a pupil detector itself: it reads the shared EyeSamples
published by BinocularController and consumes each camera frame exactly once.
[ESC] cancels at any time.
"""

import os
import sys
import copy
import math
import time
import shutil
import queue
import threading
import tkinter as tk
from typing import Optional, Callable, Dict, Any, List, Tuple
import numpy as np

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from binocular_calibrator import (BinocularCalibrator, nudge_grid, grid_fit, grid_shape_of, load_grid_shape,
                                  save_grid_shape, GRID_SHAPE_ENABLED)
from binocular_calibrator import SHAPE_LEARN_RATE
from gaze_engine import GazeSmoother
from ui_round import round_rect

CHART_ROOT = os.path.join(_THIS_DIR, "binocular_calibration_chart.png")
_chart_jobs: "queue.Queue" = queue.Queue()
_chart_worker: Optional[threading.Thread] = None
_chart_start_lock = threading.Lock()


def _chart_loop():
    from binocular_calibration_reporter import generate_binocular_chart   # matplotlib (~1 s) loads here, not in the UI
    while True:
        metrics, pursuit, archive_png, tag = _chart_jobs.get()
        try:
            generate_binocular_chart(metrics, archive_png, pursuit)         # writes a temp file, then renames
            tmp = CHART_ROOT + ".tmp.png"
            shutil.copy2(archive_png, tmp)
            os.replace(tmp, CHART_ROOT)          # the app / launcher never read a half-written chart
            print(f"[{tag}] chart ready: {archive_png}")
        except Exception as e:
            print(f"[{tag}] chart failed: {e}")


def render_chart_async(metrics: Dict[str, Any], pursuit_records, archive_png: str, tag: str):
    """Draws the diagnostic chart (~1.3 s) in ONE background worker after the calibration was saved, so the
    completion screen is not frozen. Jobs run in order (a later fine-tune chart is never overwritten by an older
    one). The app (chart_version = file time in /status) and the launcher (_poll) show the new chart when it lands."""
    global _chart_worker
    job = (copy.deepcopy(metrics), list(pursuit_records or []), archive_png, tag)
    with _chart_start_lock:
        if _chart_worker is None:
            _chart_worker = threading.Thread(target=_chart_loop, name="chart-render", daemon=True)
            _chart_worker.start()
    _chart_jobs.put(job)

# Colours of the three calibration screens (full calibration, Level 2 fine-tune, Quick Re-centre), in the AAC app's
# colours. Setting "calibration_background": "dark" (default, same near-black screen as before) or "light" (the app's
# light background), so the user can test which background gives the better calibration (screen brightness changes
# pupil size). Names kept from the old dark-only design: CYAN = main accent + gaze pointer, TEXT_WHITE = main text
# and dot centres, GOLD = amber/orange accent (pursuit dot, warnings).
PALETTES = {
    "dark": dict(BG_COLOR="#080c14", CYAN="#00bcd4", GREEN="#43a047", GOLD="#fb8c00", RED="#e53935",
                 TEXT_WHITE="#ffffff", TEXT_MUTED="#8b9bb4", GRID_GUIDE="#162030", BORDER="#243b6e",
                 HUD_FILL="#0d141e", RING_TRACK="#1a2736", PATH_GUIDE="#202c3d", TRAIL="#005f6b",
                 POINTER_NAME="cyan", GOLD_NAME="orange"),
    "light": dict(BG_COLOR="#ecf2fb", CYAN="#1565c0", GREEN="#2e7d32", GOLD="#e65100", RED="#c62828",
                  TEXT_WHITE="#1a2e52", TEXT_MUTED="#5f6b7a", GRID_GUIDE="#c9d6e8", BORDER="#bbd0f0",
                  HUD_FILL="#ffffff", RING_TRACK="#dce5f1", PATH_GUIDE="#b0c8f0", TRAIL="#90b8e8",
                  POINTER_NAME="blue", GOLD_NAME="orange"),
}
# module constants = the dark palette; use_palette() replaces them when a calibration screen opens
BG_COLOR, CYAN, GREEN, GOLD, RED = "#080c14", "#00bcd4", "#43a047", "#fb8c00", "#e53935"
TEXT_WHITE, TEXT_MUTED, GRID_GUIDE, BORDER = "#ffffff", "#8b9bb4", "#162030", "#243b6e"
HUD_FILL, RING_TRACK, PATH_GUIDE, TRAIL = "#0d141e", "#1a2736", "#202c3d", "#005f6b"
POINTER_NAME, GOLD_NAME = "cyan", "orange"
assert dict(BG_COLOR=BG_COLOR, CYAN=CYAN, GREEN=GREEN, GOLD=GOLD, RED=RED, TEXT_WHITE=TEXT_WHITE,
            TEXT_MUTED=TEXT_MUTED, GRID_GUIDE=GRID_GUIDE, BORDER=BORDER, HUD_FILL=HUD_FILL, RING_TRACK=RING_TRACK,
            PATH_GUIDE=PATH_GUIDE, TRAIL=TRAIL, POINTER_NAME=POINTER_NAME, GOLD_NAME=GOLD_NAME) == PALETTES["dark"]

Point = Tuple[float, float]


def background_of(controller) -> str:
    """'dark' or 'light' from the 'calibration_background' setting (anything else counts as dark)."""
    v = str(getattr(controller, "cfg", {}).get("calibration_background", "dark")).lower()
    return v if v in PALETTES else "dark"


def use_palette(controller) -> dict:
    """Called when a calibration screen opens: sets this module's colour constants for the chosen background and
    returns the palette (finetune_overlay copies it). Only one calibration screen is open at a time."""
    p = PALETTES[background_of(controller)]
    globals().update(p)
    return p


def pointer_enabled(controller) -> bool:
    """'Show gaze pointer during calibration' setting (launcher / app); read every frame so a toggle acts at once."""
    return bool(getattr(controller, "cfg", {}).get("show_gaze_pointer", True))


def draw_gaze_pointer(canvas, pos, trail, big: bool = True):
    """The cyan live-gaze pointer (same look in full calibration, Level 2 fine-tune and Quick Re-centre)."""
    if pos is None:
        return
    if big:
        for i, (gx, gy) in enumerate(trail):
            tr_r = max(2, int(2 + i * 0.35))
            canvas.create_oval(gx - tr_r, gy - tr_r, gx + tr_r, gy + tr_r, fill=TRAIL, outline="")
    cgx, cgy = pos
    r = 14 if big else 12
    canvas.create_oval(cgx - r, cgy - r, cgx + r, cgy + r, outline=CYAN, width=2)
    if big:
        canvas.create_line(cgx - 22, cgy, cgx - 7, cgy, fill=CYAN, width=2)
        canvas.create_line(cgx + 7, cgy, cgx + 22, cgy, fill=CYAN, width=2)
        canvas.create_line(cgx, cgy - 22, cgx, cgy - 7, fill=CYAN, width=2)
        canvas.create_line(cgx, cgy + 7, cgx, cgy + 22, fill=CYAN, width=2)
    canvas.create_oval(cgx - 3, cgy - 3, cgx + 3, cgy + 3, fill=CYAN, outline="")


class GazePointer:
    """Live pointer for fine-tune and re-centre: same smoother, Smoothing Alpha, blink reset and trail as the full
    calibration's pointer. Display only: it never feeds the collected data."""

    def __init__(self, controller, sw: int, sh: int):
        self.controller, self.sw, self.sh = controller, sw, sh
        self.smoother = GazeSmoother(alpha=self._alpha(), grace_s=0.24)
        self.pos: Optional[Tuple[int, int]] = None
        self.trail: List[Tuple[int, int]] = []
        self._last_t = 0.0

    def _alpha(self) -> float:
        try:
            return max(0.005, min(0.9, float(self.controller.cfg.get("mouse_alpha", 0.15))))
        except (AttributeError, TypeError, ValueError):
            return 0.15

    def update(self, pred):
        if pred is None:
            return
        now = time.perf_counter()
        if now - self._last_t > 0.35:                     # signal was gone: start again where the eye is now
            self.smoother.reset()
            self.trail = []
        self._last_t = now
        self.smoother.alpha = self._alpha()
        sx, sy = self.smoother.update(float(pred[0]), float(pred[1]), now)
        self.pos = (max(0, min(self.sw - 1, int(round(sx)))), max(0, min(self.sh - 1, int(round(sy)))))
        self.trail.append(self.pos)
        if len(self.trail) > 16:
            self.trail.pop(0)

    def draw(self, canvas, big: bool = True):
        if pointer_enabled(self.controller) and time.perf_counter() - self._last_t <= 0.35:
            draw_gaze_pointer(canvas, self.pos, self.trail, big)


class BinocularCalibrationOverlay(tk.Toplevel):
    """Fullscreen dual-sensor calibration overlay with live pursuit tracking pointer."""

    START_SECS        = 1.0   # when the calibration opens: first dot shown still before its ring starts (user 2026-11-25)
    APPROACH_SECS     = 1.3   # ring shrinks; eye settles on target
    COLLECT_SECS      = 1.1   # arc sweeps 360°; frames are collected
    FLASH_SECS        = 0.40  # confirmation flash
    RETRY_SECS        = 1.2   # pause to display warning message before retrying
    STATIC_VAL_APPROACH_SECS = 0.8 # settle time for the second look (the eye needs ~0.5 s to arrive)
    STATIC_VAL_COLLECT_SECS  = 0.8 # dwell collection time with live gaze reticle
    GATE_TIMEOUT_SECS = 4.0   # seconds before auto-proceeding to dynamic pursuit
    TRACK_BREAK_SECS  = 1.6   # settle break before each pursuit path (was 1.3; +0.3 s, user 2026-11-25)
    TRACK_SECS        = 6.0   # slow duration of one pursuit path (smooth physiological pursuit < 6 deg/s)
    REACTION_LAG_S    = 0.15  # oculomotor pursuit lag used for target compensation
    SAMPLE_MIN_CONF   = 0.15  # detections below this are ignored
    MIN_STATIC_SAMPLES = 4    # minimum accepted frames for a static point to count
    POINT_MIN_SAMPLES = 8     # minimum accepted frames during 1.1s for quality pass
    # [C3] max mean radial jitter (camera px) of a static point. 1 camera px is ~8-10 screen px; a steady
    # fixation measures 0.3-1 px, a detector stuck on lashes or a moving eye 3-40 px. Was 14 (accepted almost all).
    POINT_MAX_JITTER_PX = 3.0
    MAX_POINT_RETRIES = 2     # max automatic retries per static point
    # HIDDEN SWITCH (2026-11-24, user): True = old behaviour (re-fit after EVERY pursuit path). False = the live pointer
    # stays on the 9-dot map during the paths and the map is re-fitted once after the last path. The saved
    # calibration does not change (the final fit uses only pupils/targets, never the live pointer); only the pointer
    # shown during the paths and the pursuit numbers in summary.txt / chart (measured with the live map) differ.
    PROGRESSIVE_REFIT = False
    # HIDDEN SWITCH (2026-11-25, user): extra map updates after these many paths (4 = top + middle lines, 8 = + bottom
    # + cross lines), so the bottom / cross / vertical lines use a map that already knows that part of the screen.
    # 18 recorded sessions, pointer error: bottom 65 -> 42 px, cross 66 -> 53, vertical 83 -> 57, gap between the two
    # vertical lines 121 -> 4 px. The final map (after the last path) is unchanged. () = old behaviour.
    REFIT_AFTER_PATHS = (4, 8)
    MAX_PATH_RETRIES  = 2     # max automatic retries per pursuit path (was 1; 2 like the dots, user 2026-11-25)
    # [2026-11-25, user] a path is also retried when an eye's pupil JUMPED (eyelash / shadow taken as the pupil: still
    # "tracked", so the lost-frames check missed it): share of that eye's frames further than PATH_JUMP_PX from the
    # line after removing the line's own shift (same test as the fit's PURSUIT_GATE_PX). 48 recorded calibrations:
    # > 20 % in 8 lines / 8 sessions (27-100 % = clear detector jumps); 15 % would also catch map-bend cases.
    # [2026-11-25, user] the 4 final check dots: a dot whose landing is more than this from where the other dots put it
    # (their shared shift = head/headset movement removed) or that has no reading is looked at ONCE more; the new
    # reading replaces the old one. 47 recorded calibrations: 7 of 188 dots (6 sessions), e.g. one 370 px off.
    VAL_RETAKE_PX     = 50.0
    PATH_MAX_JUMPED   = 0.20
    PATH_JUMP_PX      = 150.0
    PATH_MIN_TRACKED  = 0.55  # minimum fraction of tracked frames per pursuit path
    PATH_MIN_FRAMES   = 35    # minimum absolute tracked frames
    CAM_FPS_NOMINAL   = 30.0  # used only to express per-point signal quality as a ratio

    PATH_NAMES = [
        "Top Horizontal (Left → Right)",
        "Top Horizontal (Right → Left)",
        "Center Horizontal (Left → Right)",
        "Center Horizontal (Right → Left)",
        "Bottom Horizontal (Left → Right)",
        "Bottom Horizontal (Right → Left)",
        "Diagonal 1 (Top-Left ↘ Bottom-Right)",
        "Diagonal 2 (Top-Right ↙ Bottom-Left)",
        "Vertical Left (Top ↓ Bottom)",
        "Vertical Right (Bottom ↑ Top)",
    ]

    def __init__(self, master, controller, on_complete: Optional[Callable] = None, on_cancel: Optional[Callable] = None):
        super().__init__(master)
        self.controller = controller
        self.on_complete = on_complete
        self.on_cancel = on_cancel
        use_palette(controller)
        self.background = background_of(controller)       # saved with the calibration (dark vs light test)

        self.attributes("-fullscreen", True)
        self.attributes("-topmost", True)
        self.configure(bg=BG_COLOR)
        self.title("Binocular Gaze Calibration & Smooth Pursuit")

        self.screen_w = self.winfo_screenwidth()
        self.screen_h = self.winfo_screenheight()

        self.canvas = tk.Canvas(self, width=self.screen_w, height=self.screen_h,
                                bg=BG_COLOR, highlightthickness=0, cursor="none")
        self.canvas.pack(fill="both", expand=True)
        self.bind("<Escape>", lambda e: self.cancel())
        self.focus_force()

        self.calibrator = BinocularCalibrator(self.screen_w, self.screen_h)

        # the on-screen gaze pointer uses the SAME smoother and Smoothing Alpha as the mouse (launcher slider), so
        # calibration and mouse control feel the same; a calmer pointer is also not chased by the eyes
        self.pointer_smoother = GazeSmoother(alpha=self._mouse_alpha(), grace_s=0.24)
        self.live_raw_pos: Optional[Tuple[float, float]] = None   # unsmoothed gaze (used for the 9-point check)
        self._last_gaze_t = 0.0

        # State machine
        self.stage = "CALIB"      # CALIB -> PURSUIT -> VAL -> DONE
        self.phase = "START"      # START (once) | APPROACH | COLLECT | RETRY | FLASH | TRACK
        self.target_idx = 0
        self.pursuit_idx = 0
        self.phase_t0 = time.time()

        # Retry tracking state
        self.point_retries = 0
        self.path_retries = 0
        self.retry_msg = ""
        self._path_pupil_window_l: List[Point] = []
        self._path_pupil_window_r: List[Point] = []
        self._clean_l: Optional[Point] = None      # last filtered pupil per eye (pointer on ticks without a new frame)
        self._clean_r: Optional[Point] = None

        # Frame bookkeeping (consume each camera frame once)
        self._last_seq_l = -1
        self._last_seq_r = -1
        self._new_l = self._new_r = False      # did this eye deliver a new frame on this tick

        # Static calibration accumulation (None = eye unusable at that point)
        self.calib_left_medians: List[Optional[Point]] = []
        self.calib_right_medians: List[Optional[Point]] = []
        self.calib_targets: List[Tuple[int, int]] = []
        # [E1/E2] error checks: second look at each dot (live revisit), slip, and the re-check queue
        self.revisit: Dict[str, List[Optional[Point]]] = {"left": [], "right": []}
        self.slip: Dict[str, Tuple[float, float]] = {"left": (0.0, 0.0), "right": (0.0, 0.0)}
        self.recheck_kind = ""            # "E1" (after the revisit) or "E2" (after the pursuit paths)
        self.recheck_queue: List[int] = []
        self.recheck_info: Dict[str, Any] = {}
        # [S1/S2] soft learning: every reading of a dot is kept and combined by weight; the map is nudged, never reset
        self.first_looks: Dict[str, List[Optional[Point]]] = {"left": [], "right": []}
        self.extra_looks: Dict[str, Dict[int, List[Point]]] = {"left": {}, "right": {}}   # re-check looks (slip removed)
        self.combined: Dict[str, List[Optional[Point]]] = {"left": [], "right": []}       # before the grid nudge
        self.anchor_weights: Dict[str, List[float]] = {}
        self.grid_shape = load_grid_shape()
        self.static_quality: Dict[str, List[float]] = {"left": [], "right": []}

        # Current point raw samples
        self.curr_left_samples: List[Point] = []
        self.curr_right_samples: List[Point] = []
        self.curr_left_confs: List[float] = []
        self.curr_right_confs: List[float] = []
        self.curr_frames_l = 0
        self.curr_frames_r = 0

        # Pursuit & validation
        self.pursuit_records: List[Dict[str, Any]] = []
        self.val_records: List[Dict[str, Any]] = []
        self.val_fused: List[Tuple[float, float]] = []      # per-tick fused gaze of the current check dot (jitter)
        self.val_retake_done = False                         # the 4 check dots are done; now in the one-more-look round
        self.val_retake_queue: List[int] = []
        self.static_val_records: List[Dict[str, Any]] = []
        self.static_val_gaze_samples: List[Tuple[float, float]] = []
        self.live_gaze_pos: Optional[Tuple[int, int]] = None
        self.gaze_trail: List[Tuple[int, int]] = []

        self._active = True
        self._anim_timer = None
        # optional raw-frame recording of the whole calibration (launcher checkbox), with target positions
        self._rec = None
        if self.controller.cfg.get("record_calibration"):
            self._rec = self.controller.start_recording("calibration", screen=(self.screen_w, self.screen_h))
        self._tick()

    def _current_target(self, elapsed: float):
        """Screen position the user is asked to look at right now (None during gates/breaks)."""
        try:
            if self.stage in ("CALIB", "STATIC_VAL", "RECHECK"):
                return self.calibrator.grid_px[self.target_idx]
            if self.stage == "VAL":
                return self.calibrator.val_px[self.target_idx]
            if self.stage == "PURSUIT":
                p0, p1 = self.calibrator.pursuit_px[self.pursuit_idx]
                if self.phase != "TRACK":
                    return p0
                f = min(1.0, elapsed / self.TRACK_SECS)
                return (round(p0[0] + (p1[0] - p0[0]) * f), round(p0[1] + (p1[1] - p0[1]) * f))
        except (IndexError, AttributeError):
            pass
        return None

    def _stop_recording(self):
        """Calibration saved: stop and KEEP the recording."""
        if self._rec is not None:
            self.controller.stop_recording()
            self._rec = None

    def _discard_recording(self, wait: bool = False):
        """Calibration not completed (ESC, failed fit, closed at shutdown): stop and DELETE the recording."""
        rec = getattr(self, "_rec", None)
        if rec is not None:
            if self.controller.recorder is rec:
                self.controller.recorder = None
            rec.discard(wait)
            self._rec = None

    # =========================================================================
    # Shared-sample access
    # =========================================================================
    def _read_eyes(self):
        """
        Returns (new_frame, lp, lc, rp, rc) from the controller's latest samples.
        new_frame is True if at least one eye delivered a frame not seen before.
        Low-confidence detections are reported as None.
        """
        smp_l, smp_r = self.controller.get_samples()
        self._new_l, self._new_r = smp_l.seq != self._last_seq_l, smp_r.seq != self._last_seq_r
        new = self._new_l or self._new_r
        self._last_seq_l, self._last_seq_r = smp_l.seq, smp_r.seq

        lp = smp_l.pupil if (smp_l.connected and smp_l.pupil is not None and smp_l.conf > self.SAMPLE_MIN_CONF) else None
        rp = smp_r.pupil if (smp_r.connected and smp_r.pupil is not None and smp_r.conf > self.SAMPLE_MIN_CONF) else None
        lc = smp_l.conf if lp is not None else 0.0
        rc = smp_r.conf if rp is not None else 0.0
        return new, lp, lc, rp, rc

    def _reset_point_buffers(self):
        self.curr_left_samples, self.curr_right_samples = [], []
        self.curr_left_confs, self.curr_right_confs = [], []
        self.curr_frames_l = self.curr_frames_r = 0

    def _accumulate_static(self, lp, lc, rp, rc):
        """Each camera frame is stored once: an eye's reading is added only on a tick where THAT eye has a new
        frame (the ticks run at ~60/s and either camera can trigger one; before, the other eye's unchanged reading
        was added again, ~40 % duplicates, and the accepted-frame quality always read 100 %)."""
        if self._new_l:
            self.curr_frames_l += 1
            if lp is not None:
                self.curr_left_samples.append(lp)
                self.curr_left_confs.append(lc)
        if self._new_r:
            self.curr_frames_r += 1
            if rp is not None:
                self.curr_right_samples.append(rp)
                self.curr_right_confs.append(rc)

    def _mouse_alpha(self) -> float:
        """Current Smoothing Alpha from the launcher's mouse settings (saved in the controller config)."""
        try:
            return max(0.005, min(0.9, float(self.controller.cfg.get("mouse_alpha", 0.15))))
        except (TypeError, ValueError):
            return 0.15

    def _update_live_pointer(self, lp, lc, rp, rc) -> Dict[str, Any]:
        """Predicts gaze from the current pupils, smooths it for display, and returns predict() details."""
        if not self.calibrator.is_calibrated:
            return {"pred": None, "mode": "Not Calibrated", "details": {}}
        pred, mode, details = self.calibrator.predict(lp, rp, lc, rc)
        self.live_raw_pos = None
        if pred is not None:
            now = time.perf_counter()
            if now - self._last_gaze_t > 0.35:            # signal was gone: start again where the eye is now
                self.pointer_smoother.reset()
            self._last_gaze_t = now
            self.live_raw_pos = (float(pred[0]), float(pred[1]))
            self.pointer_smoother.alpha = self._mouse_alpha()
            sx, sy = self.pointer_smoother.update(float(pred[0]), float(pred[1]), now)
            fx, fy = int(round(sx)), int(round(sy))
            self.live_gaze_pos = (max(0, min(self.screen_w - 1, fx)), max(0, min(self.screen_h - 1, fy)))
            self.gaze_trail.append(self.live_gaze_pos)
            if len(self.gaze_trail) > 16:
                self.gaze_trail.pop(0)
        return {"pred": pred, "mode": mode, "details": details}

    # =========================================================================
    # Quality & Filtering Helpers
    # =========================================================================
    def _check_point_quality(self) -> Tuple[bool, str]:
        """
        Evaluates pupil stability collected during the static COLLECT phase.
        Returns (is_valid, reason_str).
        Checks frame count and radial jitter for each connected camera.
        """
        smp_l, smp_r = self.controller.get_samples()
        checks = []

        if smp_l.connected:
            n_l = len(self.curr_left_samples)
            if n_l < self.POINT_MIN_SAMPLES:
                checks.append(f"Left eye low signal ({n_l} frames)")
            else:
                pts_l = np.array(self.curr_left_samples, dtype=np.float64)
                clean_l = BinocularCalibrator._tukey_filter(pts_l)
                med_l = np.median(clean_l, axis=0)
                jit_l = float(np.mean(np.hypot(clean_l[:, 0] - med_l[0], clean_l[:, 1] - med_l[1])))
                if jit_l > self.POINT_MAX_JITTER_PX:
                    checks.append(f"Left eye jittery ({jit_l:.1f}px)")

        if smp_r.connected:
            n_r = len(self.curr_right_samples)
            if n_r < self.POINT_MIN_SAMPLES:
                checks.append(f"Right eye low signal ({n_r} frames)")
            else:
                pts_r = np.array(self.curr_right_samples, dtype=np.float64)
                clean_r = BinocularCalibrator._tukey_filter(pts_r)
                med_r = np.median(clean_r, axis=0)
                jit_r = float(np.mean(np.hypot(clean_r[:, 0] - med_r[0], clean_r[:, 1] - med_r[1])))
                if jit_r > self.POINT_MAX_JITTER_PX:
                    checks.append(f"Right eye jittery ({jit_r:.1f}px)")

        if checks:
            return False, "; ".join(checks)
        return True, "OK"

    def _filter_path_pupil(self, p: Optional[Point], window: List[Point]) -> Optional[Point]:
        """Applies a rolling 5-frame median filter on pupil coordinates to reject transient eyelash spikes."""
        if p is None:
            window.clear()
            return None
        window.append(p)
        if len(window) > 5:
            window.pop(0)
        if len(window) == 1:
            return p
        mx = float(np.median([pt[0] for pt in window]))
        my = float(np.median([pt[1] for pt in window]))
        return (mx, my)

    def _path_eye_lost(self, recs) -> Optional[Tuple[str, float]]:
        """(eye, tracked share) when ONE eye had a pupil in fewer than PATH_MIN_TRACKED of its own camera frames on
        this line (the line check above counts a frame as tracked if EITHER eye has a pupil). Counted per camera
        frame (seq), because a row stores an eye's pupil only on that camera's new frames. 45 recorded calibrations:
        lowest 55 %, so this is a safety net (never triggered there)."""
        worst = None
        for eye in self._eyes_on():
            seen: Dict[int, bool] = {}
            for r in recs:
                s = r.get(f"seq_{eye}")
                if s is not None:
                    seen[s] = seen.get(s, False) or r.get(f"{eye}_pupil") is not None
            if len(seen) < 20:                   # camera delivered (almost) nothing: a retry would not help
                continue
            share = sum(seen.values()) / len(seen)
            if share < self.PATH_MIN_TRACKED and (worst is None or share < worst[1]):
                worst = (eye.capitalize(), share)
        return worst

    def _path_jumped(self, recs) -> Optional[Tuple[str, float]]:
        """(eye, share) when an eye's pupil jumped off the line in more than PATH_MAX_JUMPED of its frames (frames
        after the first 0.5 s, compared with the current map after removing the line's own shift), else None."""
        c = self.calibrator
        if not c.is_calibrated:
            return None
        worst = None
        for eye in self._eyes_on():
            W, norm = (c.w_left, c.norm_left) if eye == "left" else (c.w_right, c.norm_right)
            d = []
            for r in recs:
                p = r.get(f"{eye}_pupil")
                if p is None or r.get("elapsed_sec", 0.0) <= 0.5:
                    continue
                g = c._predict_eye(p, W, norm, None)
                if g is not None:
                    tg = r["compensated_target"]
                    d.append((g[0] - tg[0], g[1] - tg[1]))
            if len(d) < 20:
                continue
            d = np.array(d)
            share = float(np.mean(np.hypot(*(d - np.median(d, axis=0)).T) > self.PATH_JUMP_PX))
            if share > self.PATH_MAX_JUMPED and (worst is None or share > worst[1]):
                worst = (eye.capitalize(), share)
        return worst

    # =========================================================================
    # Main loop
    # =========================================================================
    def _tick(self):
        if not self._active:
            return

        t = time.time()
        elapsed = t - self.phase_t0
        new, lp, lc, rp, rc = self._read_eyes()
        if self._rec is not None:
            self._rec.mark(self.stage, self.phase, self._current_target(elapsed))

        # ------------------------------------------------------------- CALIB
        if self.stage == "CALIB":
            targets = self.calibrator.grid_px
            tx, ty = targets[self.target_idx]

            if self.phase == "START":
                if elapsed >= self.START_SECS:
                    self.phase, self.phase_t0 = "APPROACH", t

            elif self.phase == "APPROACH":
                if elapsed >= self.APPROACH_SECS:
                    self.phase, self.phase_t0 = "COLLECT", t
                    self._reset_point_buffers()

            elif self.phase == "COLLECT":
                if new:
                    self._accumulate_static(lp, lc, rp, rc)
                if elapsed >= self.COLLECT_SECS:
                    is_ok, reason = self._check_point_quality()
                    if not is_ok and self.point_retries < self.MAX_POINT_RETRIES:
                        self.point_retries += 1
                        self.phase, self.phase_t0 = "RETRY", t
                        self.retry_msg = f"Point Unstable ({reason}) — Retrying ({self.point_retries}/{self.MAX_POINT_RETRIES})"
                        self._reset_point_buffers()
                    else:
                        self._finalize_calib_point(tx, ty)
                        self.phase, self.phase_t0 = "FLASH", t
                        self.point_retries = 0
                        self.retry_msg = ""

            elif self.phase == "RETRY":
                if elapsed >= self.RETRY_SECS:
                    self.phase, self.phase_t0 = "APPROACH", t
                    self._reset_point_buffers()
                    self.retry_msg = ""

            elif self.phase == "FLASH":
                if elapsed >= self.FLASH_SECS:
                    self.target_idx += 1
                    if self.target_idx >= len(targets):
                        # 1. map v1: the 9 first looks, each nudged toward where the learned grid says it should be [S1]
                        self._apply_grid_nudge(from_first_looks=True)
                        self.calibrator.fit(self.calib_left_medians, self.calib_right_medians, self.calib_targets,
                                            static_quality=self.static_quality)
                        # 2. Transition to Phase 2: Interactive 9-Point Validation
                        self.stage, self.target_idx, self.phase = "STATIC_VAL", 0, "APPROACH"
                        self.static_val_records = []
                        self.static_val_gaze_samples = []
                        self.live_gaze_pos, self.gaze_trail = None, []
                        self._reset_point_buffers()
                    else:
                        self.phase = "APPROACH"
                    self.phase_t0 = t

        # ----------------------------------------------------------- STATIC_VAL
        elif self.stage == "STATIC_VAL":
            targets = self.calibrator.grid_px
            tx, ty = targets[self.target_idx]

            if new:
                self._update_live_pointer(lp, lc, rp, rc)

            if self.phase == "APPROACH":
                if elapsed >= self.STATIC_VAL_APPROACH_SECS:
                    self.phase, self.phase_t0 = "COLLECT", t
                    self.static_val_gaze_samples = []
                    self._reset_point_buffers()

            elif self.phase == "COLLECT":
                # measured on the UNSMOOTHED gaze, so a calm (slow) pointer does not make the error look worse
                if new and self.live_raw_pos is not None:
                    self.static_val_gaze_samples.append(self.live_raw_pos)
                if new:
                    self._accumulate_static(lp, lc, rp, rc)     # second look at this dot, for the repeat check
                if elapsed >= self.STATIC_VAL_COLLECT_SECS:
                    if self.static_val_gaze_samples:
                        arr = np.array(self.static_val_gaze_samples, dtype=np.float64)
                        clean_pts = BinocularCalibrator._tukey_filter(arr)
                        med_gx = float(np.median(clean_pts[:, 0]))
                        med_gy = float(np.median(clean_pts[:, 1]))
                        err = math.hypot(med_gx - tx, med_gy - ty)
                    else:
                        med_gx, med_gy = None, None
                        err = 999.0

                    self.static_val_records.append({
                        "target_idx": self.target_idx,
                        "target": (tx, ty),
                        "gaze": (med_gx, med_gy) if med_gx is not None else None,
                        "error_px": err,
                    })
                    for eye, buf in (("left", self.curr_left_samples), ("right", self.curr_right_samples)):
                        res = self._median_of(buf)
                        self.revisit[eye].append(res[0] if res else None)

                    self.target_idx += 1
                    if self.target_idx >= len(targets):
                        self._start_repeat_check(t)               # [E1] -> RECHECK or the acceptance gate
                    else:
                        self.phase = "APPROACH"
                        self.phase_t0 = t

        # ----------------------------------------------------------- RECHECK [E1/E2]
        elif self.stage == "RECHECK":
            tx, ty = self.calibrator.grid_px[self.target_idx]
            if self.phase == "APPROACH":
                if elapsed >= self.APPROACH_SECS:
                    self.phase, self.phase_t0 = "COLLECT", t
                    self._reset_point_buffers()
            elif self.phase == "COLLECT":
                if new:
                    self._accumulate_static(lp, lc, rp, rc)
                if elapsed >= self.COLLECT_SECS:
                    self._resolve_recheck(self.target_idx)
                    self.recheck_queue.pop(0)
                    if self.recheck_queue:
                        self.target_idx, self.phase, self.phase_t0 = self.recheck_queue[0], "APPROACH", t
                    else:
                        self._finish_recheck(t)

        # ----------------------------------------------------------- ACCEPTANCE_GATE
        elif self.stage == "ACCEPTANCE_GATE":
            if elapsed >= self.GATE_TIMEOUT_SECS:
                self._on_gate_proceed()

        # ----------------------------------------------------------- PURSUIT
        elif self.stage == "PURSUIT":
            paths = self.calibrator.pursuit_px
            p_start, p_end = paths[self.pursuit_idx]
            path_name = self.PATH_NAMES[self.pursuit_idx] if self.pursuit_idx < len(self.PATH_NAMES) else f"Path {self.pursuit_idx + 1}"

            if self.phase == "APPROACH":
                self._path_pupil_window_l.clear()
                self._path_pupil_window_r.clear()
                self._clean_l = self._clean_r = None
                if new:
                    self._update_live_pointer(lp, lc, rp, rc)
                if elapsed >= self.TRACK_BREAK_SECS:
                    self.phase, self.phase_t0 = "TRACK", t
                    self.gaze_trail = []

            elif self.phase == "TRACK":
                frac = min(1.0, elapsed / self.TRACK_SECS)
                tx = int(round(p_start[0] + (p_end[0] - p_start[0]) * frac))
                ty = int(round(p_start[1] + (p_end[1] - p_start[1]) * frac))
                vx = (p_end[0] - p_start[0]) / self.TRACK_SECS
                vy = (p_end[1] - p_start[1]) / self.TRACK_SECS
                # Where the eye actually is while following: the target's position REACTION_LAG_S ago
                lag_frac = max(0.0, (elapsed - self.REACTION_LAG_S) / self.TRACK_SECS)
                tx_comp = int(round(p_start[0] + (p_end[0] - p_start[0]) * min(1.0, lag_frac)))
                ty_comp = int(round(p_start[1] + (p_end[1] - p_start[1]) * min(1.0, lag_frac)))

                # Standard reference constraint: for horizontal paths, lock compensated target Y to path elevation
                if abs(p_start[1] - p_end[1]) < 2.0:
                    ty_comp = int(round(p_start[1]))

                # Rolling median pupil smoothing for pursuit frames: each camera frame enters its eye's window once
                if self._new_l:
                    self._clean_l = self._filter_path_pupil(lp, self._path_pupil_window_l)
                if self._new_r:
                    self._clean_r = self._filter_path_pupil(rp, self._path_pupil_window_r)

                if new:
                    self._record_pursuit_frame(self._clean_l, lc, self._clean_r, rc, tx, ty, tx_comp, ty_comp, vx, vy,
                                               elapsed, frac, path_name, (self._new_l, self._new_r))

                if elapsed >= self.TRACK_SECS:
                    # Check path tracking quality
                    curr_path_recs = [r for r in self.pursuit_records if r.get("path_idx") == self.pursuit_idx]
                    tracked_cnt = sum(1 for r in curr_path_recs if not r.get("is_blink") and (r.get("left_pupil") or r.get("right_pupil")))
                    ratio = tracked_cnt / max(1, len(curr_path_recs))
                    lost = ratio < self.PATH_MIN_TRACKED or tracked_cnt < self.PATH_MIN_FRAMES
                    eye_lost = None if lost else self._path_eye_lost(curr_path_recs)
                    jumped = None if (lost or eye_lost) else self._path_jumped(curr_path_recs)

                    if (lost or eye_lost or jumped) and self.path_retries < self.MAX_PATH_RETRIES:
                        self.path_retries += 1
                        # Purge corrupted frames for this path
                        self.pursuit_records = [r for r in self.pursuit_records if r.get("path_idx") != self.pursuit_idx]
                        self.phase, self.phase_t0 = "RETRY", t
                        why = (f"{int((1.0 - ratio) * 100)}% lost" if lost else
                               f"{eye_lost[0]} eye lost in {int((1.0 - eye_lost[1]) * 100)}%" if eye_lost else
                               f"{jumped[0]} eye pupil jumped in {int(jumped[1] * 100)}%")
                        print(f"[Calibration] {path_name}: {why} -> retry {self.path_retries}/{self.MAX_PATH_RETRIES}")
                        self.retry_msg = f"Path Interrupted ({why}) — Retrying Path ({self.path_retries}/{self.MAX_PATH_RETRIES})"
                        self._path_pupil_window_l.clear()
                        self._path_pupil_window_r.clear()
                    else:
                        # Path completed! Re-fit with everything collected so far - after paths REFIT_AFTER_PATHS and
                        # the LAST path (every path only with PROGRESSIVE_REFIT: a map fitted on the top line only bent
                        # the live pointer on the middle line up to ~100 px); the final map is the same either way
                        done = self.pursuit_idx + 1
                        if self.PROGRESSIVE_REFIT or done in self.REFIT_AFTER_PATHS or done >= len(paths):
                            self.calibrator.fit(self.calib_left_medians, self.calib_right_medians, self.calib_targets,
                                                pursuit_records=self.pursuit_records, static_quality=self.static_quality)
                        self.pursuit_idx += 1
                        self.path_retries = 0
                        self.retry_msg = ""
                        if self.pursuit_idx >= len(paths):
                            self._start_path_check(t)                 # [E2] -> RECHECK or VAL
                        else:
                            # the settle break starts after the refit, so it keeps its full TRACK_BREAK_SECS
                            self.phase, self.phase_t0 = "APPROACH", time.time()

            elif self.phase == "RETRY":
                if elapsed >= self.RETRY_SECS:
                    self.phase, self.phase_t0 = "APPROACH", t
                    self.retry_msg = ""
                    self.gaze_trail = []

        # --------------------------------------------------------------- VAL
        elif self.stage == "VAL":
            targets = self.calibrator.val_px
            tx, ty = targets[self.target_idx]

            if self.phase == "APPROACH":
                if new:
                    self._update_live_pointer(lp, lc, rp, rc)
                if elapsed >= self.APPROACH_SECS * 0.8:
                    self.phase, self.phase_t0 = "COLLECT", t
                    self._reset_point_buffers()
                    self.val_fused = []

            elif self.phase == "COLLECT":
                if new:
                    self._accumulate_static(lp, lc, rp, rc)
                    rf = self._update_live_pointer(lp, lc, rp, rc)["details"].get("raw_fused")
                    if rf is not None:
                        self.val_fused.append((float(rf[0]), float(rf[1])))
                if elapsed >= self.COLLECT_SECS:
                    self._finalize_val_point(tx, ty, slot=self.target_idx if self.val_retake_done else None)
                    self.phase, self.phase_t0 = "FLASH", t

            elif self.phase == "FLASH":
                if new:
                    self._update_live_pointer(lp, lc, rp, rc)
                if elapsed >= self.FLASH_SECS:
                    if self.val_retake_done:                      # one-more-look round
                        self.val_retake_queue.pop(0)
                    else:
                        self.target_idx += 1
                        if self.target_idx >= len(targets):
                            self.val_retake_done = True
                            self.val_retake_queue = self._val_retake_dots()
                    if self.val_retake_done:
                        if not self.val_retake_queue:
                            self._finish_calibration()
                            return
                        self.target_idx = self.val_retake_queue[0]
                    self.phase = "APPROACH"
                    self.phase_t0 = t

        self._render(elapsed)
        if self._active:
            self._anim_timer = self.after(16, self._tick)

    # =========================================================================
    # Recording helpers
    # =========================================================================
    def _record_pursuit_frame(self, lp, lc, rp, rc, tx, ty, tx_comp, ty_comp, vx, vy, elapsed, frac, path_name,
                              fresh=(True, True)):
        """fresh = (left, right) delivered a new camera frame on this tick; an eye without one still feeds the live
        pointer / fused gaze / blink flag, but its (repeated) pupil is not stored as a training sample."""
        is_blink = (lp is None and rp is None)
        res = self._update_live_pointer(lp, lc, rp, rc)
        details = res["details"]
        pred_l = details.get("pred_left")
        pred_r = details.get("pred_right")
        raw_fused = details.get("raw_fused")

        if raw_fused is not None:
            instant_err = math.hypot(raw_fused[0] - tx, raw_fused[1] - ty)
            comp_err = math.hypot(raw_fused[0] - tx_comp, raw_fused[1] - ty_comp)
        else:
            instant_err = comp_err = None
        disparity = math.hypot(pred_l[0] - pred_r[0], pred_l[1] - pred_r[1]) if (pred_l and pred_r) else None

        self.pursuit_records.append({
            "timestamp": round(time.time(), 4),
            "frame_idx": len(self.pursuit_records),
            "seq_left": self._last_seq_l,
            "seq_right": self._last_seq_r,
            "path_idx": self.pursuit_idx,
            "path_name": path_name,
            "elapsed_sec": round(elapsed, 3),
            "path_progress": round(frac, 3),
            "target": (tx, ty),
            "compensated_target": (tx_comp, ty_comp),
            "target_velocity": (round(vx, 1), round(vy, 1)),
            "left_pupil": (round(lp[0], 2), round(lp[1], 2)) if (lp and fresh[0]) else None,
            "right_pupil": (round(rp[0], 2), round(rp[1], 2)) if (rp and fresh[1]) else None,
            "left_conf": round(float(lc), 3),
            "right_conf": round(float(rc), 3),
            "pred_left": (round(pred_l[0], 1), round(pred_l[1], 1)) if pred_l else None,
            "pred_right": (round(pred_r[0], 1), round(pred_r[1], 1)) if pred_r else None,
            "raw_fused": (round(raw_fused[0], 1), round(raw_fused[1], 1)) if raw_fused else None,
            "gaze": self.live_gaze_pos if raw_fused is not None else None,
            "mode": res["mode"],
            "instant_error_px": round(instant_err, 1) if instant_err is not None else None,
            "compensated_error_px": round(comp_err, 1) if comp_err is not None else None,
            "disparity_px": round(disparity, 1) if disparity is not None else None,
            "is_blink": is_blink,
        })

    def _median_of(self, samples: List[Point]) -> Optional[Tuple[Point, np.ndarray]]:
        if len(samples) < self.MIN_STATIC_SAMPLES:
            return None
        pts = np.array(samples, dtype=np.float64)
        clean = BinocularCalibrator._tukey_filter(pts)
        med = np.median(clean, axis=0)
        return (float(med[0]), float(med[1])), clean

    def _finalize_calib_point(self, tx: int, ty: int):
        """Stores per-eye medians (None if the eye had too few accepted frames) and signal quality."""
        expected = max(1.0, self.COLLECT_SECS * self.CAM_FPS_NOMINAL)
        res_l = self._median_of(self.curr_left_samples)
        res_r = self._median_of(self.curr_right_samples)
        self.calib_left_medians.append(res_l[0] if res_l else None)
        self.calib_right_medians.append(res_r[0] if res_r else None)
        self.static_quality["left"].append(round(min(1.0, len(self.curr_left_samples) / expected), 3))
        self.static_quality["right"].append(round(min(1.0, len(self.curr_right_samples) / expected), 3))
        self.calib_targets.append((tx, ty))
        miss_l = res_l is None and self.controller.is_eye_enabled("left")
        miss_r = res_r is None and self.controller.is_eye_enabled("right")
        if miss_l or miss_r:
            print(f"[Calibration] Point {len(self.calib_targets)}: "
                  f"left={'ok' if res_l else 'skipped'} ({len(self.curr_left_samples)} frames), "
                  f"right={'ok' if res_r else 'skipped'} ({len(self.curr_right_samples)} frames)")

    def _screen_jitter(self, samples: List[Point], eye: str) -> Optional[float]:
        """Mean radial deviation (screen px) of the per-frame predictions of one eye around their median."""
        if len(samples) < self.MIN_STATIC_SAMPLES:
            return None
        preds = []
        for s in samples:
            p = self.calibrator.predict(s if eye == "left" else None, s if eye == "right" else None,
                                        1.0 if eye == "left" else 0.0, 1.0 if eye == "right" else 0.0)[2].get("raw_fused")
            if p is not None:
                preds.append(p)
        if len(preds) < self.MIN_STATIC_SAMPLES:
            return None
        arr = np.array(preds)
        med = np.median(arr, axis=0)
        return float(np.mean(np.hypot(arr[:, 0] - med[0], arr[:, 1] - med[1])))

    def _finalize_val_point(self, tx: int, ty: int, slot: Optional[int] = None):
        """slot = index of a check dot whose earlier reading this one REPLACES (one-more-look round)."""
        res_l = self._median_of(self.curr_left_samples)
        res_r = self._median_of(self.curr_right_samples)
        med_l = res_l[0] if res_l else None
        med_r = res_r[0] if res_r else None
        conf_l = float(np.mean(self.curr_left_confs)) if (res_l and self.curr_left_confs) else 0.0
        conf_r = float(np.mean(self.curr_right_confs)) if (res_r and self.curr_right_confs) else 0.0

        jit_l = self._screen_jitter(self.curr_left_samples, "left") if med_l else None
        jit_r = self._screen_jitter(self.curr_right_samples, "right") if med_r else None

        # Fused jitter: the fused gaze of every tick of this dot (both eyes' latest readings at the SAME moment).
        # Before 2026-11-25 sample i of the left list was paired with sample i of the right list, but each eye's
        # list only grows on that camera's own new frames, so the pairs were from different moments.
        jit_f = None
        if med_l and med_r and len(self.val_fused) >= self.MIN_STATIC_SAMPLES:
            arr = np.array(self.val_fused)
            med = np.median(arr, axis=0)
            jit_f = float(np.mean(np.hypot(arr[:, 0] - med[0], arr[:, 1] - med[1])))
        elif jit_l is not None or jit_r is not None:
            jit_f = jit_l if jit_l is not None else jit_r

        rec = {
            "target": (tx, ty),
            "left_median": med_l,
            "right_median": med_r,
            "left_conf": conf_l,
            "right_conf": conf_r,
            "left_frames": len(self.curr_left_samples),
            "right_frames": len(self.curr_right_samples),
            "jitter": round(jit_f, 2) if jit_f is not None else None,
            "jitter_left": round(jit_l, 2) if jit_l is not None else None,
            "jitter_right": round(jit_r, 2) if jit_r is not None else None,
        }
        if slot is not None and slot < len(self.val_records):
            rec["retaken"] = True
            self.val_records[slot] = rec
        else:
            self.val_records.append(rec)

    def _val_retake_dots(self) -> List[int]:
        """Check dots to look at once more: no reading, or the landing is more than VAL_RETAKE_PX from where the
        other dots put it (median of all 4 = the shared shift, so head/headset movement alone never triggers)."""
        d: List[Optional[Tuple[float, float]]] = []
        for r in self.val_records:
            g = self.calibrator.predict(r.get("left_median"), r.get("right_median"),
                                        r.get("left_conf", 0.0), r.get("right_conf", 0.0))[2].get("raw_fused")
            d.append(None if g is None else (g[0] - r["target"][0], g[1] - r["target"][1]))
        ok = [x for x in d if x is not None]
        shift = np.median(np.array(ok), axis=0) if len(ok) >= 3 else None
        bad = [i for i, x in enumerate(d)
               if x is None or (shift is not None and math.hypot(x[0] - shift[0], x[1] - shift[1]) > self.VAL_RETAKE_PX)]
        if bad:
            info = ", ".join(f"{i + 1}: " + ("no reading" if d[i] is None else
                             f"{math.hypot(d[i][0] - shift[0], d[i][1] - shift[1]):.0f} px") for i in bad)
            print(f"[Calibration] check dots off from the others ({info}; limit {self.VAL_RETAKE_PX:.0f}) -> one more look")
        return bad

    # =========================================================================
    # Error checks [E1] repeat check after the revisit, [E2] path check after the pursuit paths
    # =========================================================================
    def _anchors(self, eye: str) -> List[Optional[Point]]:
        return self.calib_left_medians if eye == "left" else self.calib_right_medians

    def _eyes_on(self):
        return [e for e in ("left", "right") if self.controller.is_eye_enabled(e)] \
            if hasattr(self.controller, "is_eye_enabled") else ["left", "right"]

    def _set_anchors(self, eye: str, pts: List[Optional[Point]]):
        if eye == "left":
            self.calib_left_medians[:] = pts
        else:
            self.calib_right_medians[:] = pts

    def _apply_grid_nudge(self, from_first_looks: bool = False):
        """[S1] anchors = readings nudged toward the learned grid (points within ~4 camera px are untouched)."""
        for eye in ("left", "right"):
            if from_first_looks:
                self.first_looks[eye] = list(self._anchors(eye))
                self.combined[eye] = list(self._anchors(eye))
            pts = self.combined[eye] if self.combined[eye] else list(self._anchors(eye))
            nudged, dev = nudge_grid(pts, self.grid_shape.get(eye))
            self._set_anchors(eye, nudged)
            big = [(i + 1, round(d, 1)) for i, d in enumerate(dev) if d is not None and d > 4.0]
            if big:
                print(f"[Calibration] grid nudge {eye}: dots {big} camera px from the expected grid (pulled toward it)")

    def _start_repeat_check(self, t: float):
        """[E1] Each dot was seen twice (9-point stage + live revisit). A dot whose two readings disagree by more than
        REPEAT_FLAG_CAM_PX after removing the shared slip is shown once more, to get one more reading of it."""
        lim = BinocularCalibrator.REPEAT_FLAG_CAM_PX
        flagged = set()
        for eye in self._eyes_on():
            self.slip[eye], res = BinocularCalibrator.repeat_check(self.first_looks[eye], self.revisit[eye])
            bad = [i for i, r in enumerate(res) if r is not None and r > lim]
            if bad:
                print(f"[Calibration] repeat check {eye}: dots {[i + 1 for i in bad]} disagree "
                      f"({', '.join(f'{res[i]:.1f}' for i in bad)} camera px, limit {lim:.0f}) -> one more look")
            flagged.update(bad)
        self._begin_recheck("E1", sorted(flagged), t, {})

    def _start_path_check(self, t: float):
        """[E2] After the pursuit paths: each dot is predicted by the model trained on everything else. Clearly
        inconsistent dots get one more look; then every dot's influence is set by its consistency [S2]."""
        flagged, info = set(), {}
        for eye in self._eyes_on():
            loo = self.calibrator.anchor_loo(self._anchors(eye), self.calib_targets, self.pursuit_records, eye)
            lim = self.calibrator.flag_limit(loo)
            bad = self.calibrator.flag_anchors(loo)
            info[eye] = {"bad": bad, "limit": lim, "loo": loo}
            if bad:
                print(f"[Calibration] path check {eye}: dots {[i + 1 for i in bad]} disagree with the paths "
                      f"({', '.join(f'{loo[i]:.0f}' for i in bad)} px, limit {lim:.0f}) -> one more look")
            flagged.update(bad)
        self._begin_recheck("E2", sorted(flagged), t, info)

    def _begin_recheck(self, kind: str, queue: List[int], t: float, info: Dict[str, Any]):
        self.recheck_kind, self.recheck_queue, self.recheck_info = kind, list(queue), info
        if queue:
            self.stage, self.target_idx, self.phase, self.phase_t0 = "RECHECK", queue[0], "APPROACH", time.time()
            self._reset_point_buffers()
        else:
            self._finish_recheck(t)

    def _resolve_recheck(self, i: int):
        """Store the extra look at dot i (slip removed). Nothing is replaced: all looks are combined by weight."""
        for eye, buf in (("left", self.curr_left_samples), ("right", self.curr_right_samples)):
            if eye not in self._eyes_on():
                continue
            res = self._median_of(buf)
            if res is not None:
                sx, sy = self.slip[eye]
                self.extra_looks[eye].setdefault(i, []).append((res[0][0] - sx, res[0][1] - sy))

    def _combine_looks(self):
        """[S2] every look at a dot (first, revisit, extra; slip removed) is averaged with a weight that falls with
        its distance from where the grid expects the dot, so an odd look counts little but is never thrown away."""
        for eye in self._eyes_on():
            sx, sy = self.slip[eye]
            looks = []
            for i, f in enumerate(self.first_looks[eye]):
                L = [] if f is None else [f]
                r = self.revisit[eye][i] if i < len(self.revisit[eye]) else None
                if r is not None:
                    L.append((r[0] - sx, r[1] - sy))
                L += self.extra_looks[eye].get(i, [])
                looks.append(L)
            mean = [None if not L else (float(np.mean([q[0] for q in L])), float(np.mean([q[1] for q in L]))) for L in looks]
            g = grid_fit(mean, self.grid_shape.get(eye))
            comb = []
            for i, L in enumerate(looks):
                if not L:
                    comb.append(None)
                    continue
                if g is None:
                    comb.append(mean[i])
                    continue
                e = g[0][i]
                w = np.array([1.0 / (1.0 + (math.hypot(q[0] - e[0], q[1] - e[1]) / 6.0) ** 2) for q in L])
                comb.append((float(np.dot(w, [q[0] for q in L]) / w.sum()), float(np.dot(w, [q[1] for q in L]) / w.sum())))
            self.combined[eye] = comb

    def _soften_with_paths(self):
        """[S2] after the paths: a re-looked dot's readings are blended by how well each fits the model trained on the
        other data, then every dot gets a fit weight from its consistency (soft_weights). Nothing is removed."""
        self.anchor_weights = {}
        for eye in self._eyes_on():
            info = self.recheck_info.get(eye) or {}
            anchors = self._anchors(eye)
            lim = info.get("limit") or 120.0
            for i in info.get("bad", []):
                extra = self.extra_looks[eye].get(i, [])[-1:] if self.extra_looks[eye].get(i) else []
                if not extra or anchors[i] is None:
                    continue
                sub = list(anchors)
                sub[i] = None
                fit = self.calibrator._fit_eye_aligned(sub, self.calib_targets, self.pursuit_records, eye)
                if fit is None:
                    continue
                cands = [anchors[i]] + extra
                errs = []
                for c in cands:
                    pr = self.calibrator._predict_eye(c, fit[0], fit[1], None)
                    errs.append(99999.0 if pr is None else math.hypot(pr[0] - self.calib_targets[i][0], pr[1] - self.calib_targets[i][1]))
                w = np.array([1.0 / (1.0 + (e / (0.5 * lim)) ** 2) for e in errs])
                anchors[i] = (float(np.dot(w, [c[0] for c in cands]) / w.sum()), float(np.dot(w, [c[1] for c in cands]) / w.sum()))
                print(f"[Calibration] dot {i + 1} {eye}: readings blended by fit "
                      f"({', '.join(f'{e:.0f} px' for e in errs)} -> weights {', '.join(f'{x / w.sum():.2f}' for x in w)})")
            loo = self.calibrator.anchor_loo(anchors, self.calib_targets, self.pursuit_records, eye)
            self.anchor_weights[eye] = self.calibrator.soft_weights(loo)
            low = [(i + 1, round(x, 2)) for i, x in enumerate(self.anchor_weights[eye]) if x < 0.99]
            if low:
                print(f"[Calibration] {eye}: reduced influence for dots {low}")

    def _finish_recheck(self, t: float):
        if self.recheck_kind == "E1":
            self._combine_looks()
            self._apply_grid_nudge()                               # map v1.5: all looks combined, then nudged
            self.calibrator.fit(self.calib_left_medians, self.calib_right_medians, self.calib_targets,
                                static_quality=self.static_quality)
            self.stage, self.phase, self.phase_t0 = "ACCEPTANCE_GATE", "WAIT", time.time()   # timers start after the fit
            self._setup_gate_keys()
        else:
            self._soften_with_paths()                              # map v2: consistency-weighted, with the paths
            self.calibrator.fit(self.calib_left_medians, self.calib_right_medians, self.calib_targets,
                                pursuit_records=self.pursuit_records, static_quality=self.static_quality,
                                anchor_weights=self.anchor_weights)
            self.stage, self.target_idx, self.phase, self.phase_t0 = "VAL", 0, "APPROACH", time.time()
        self.recheck_kind, self.recheck_queue = "", []

    def _learn_grid_shape(self):
        """[S1] after a full calibration: the reference curve moves SHAPE_LEARN_RATE toward this session's curve
        (only from a complete, consistent set of combined readings)."""
        if not GRID_SHAPE_ENABLED:                     # hidden switch in binocular_calibrator.py: nothing learned
            return
        changed = False
        for eye in self._eyes_on():
            pts = self.combined[eye]
            ref = self.grid_shape.get(eye)
            g = grid_fit(pts, ref) if pts else None
            if g is None or any(p is None for p in pts) or float(np.nanmax(g[1])) > BinocularCalibrator.REPEAT_FLAG_CAM_PX:
                continue
            new = grid_shape_of(pts, ref)
            if new is None:
                continue
            self.grid_shape[eye] = new if ref is None else (1 - SHAPE_LEARN_RATE) * ref + SHAPE_LEARN_RATE * new
            changed = True
        if changed:
            self.grid_shape["n"] = int(self.grid_shape.get("n", 0)) + 1
            save_grid_shape(self.grid_shape)
            print(f"[Calibration] grid shape updated (sessions learned: {self.grid_shape['n']})")

    # =========================================================================
    # Acceptance Gate Handlers (User Decision Interface)
    # =========================================================================
    def _setup_gate_keys(self):
        self.bind("<space>", lambda e: self._on_gate_redo())
        self.bind("r", lambda e: self._on_gate_redo())
        self.bind("R", lambda e: self._on_gate_redo())
        self.bind("<Return>", lambda e: self._on_gate_proceed())

    def _teardown_gate_keys(self):
        try:
            self.unbind("<space>")
            self.unbind("r")
            self.unbind("R")
            self.unbind("<Return>")
        except Exception:
            pass

    def _on_gate_redo(self):
        """User pressed [Spacebar] or [R] to cleanly redo 9-point static calibration."""
        self._teardown_gate_keys()
        self.calib_left_medians.clear()
        self.calib_right_medians.clear()
        self.calib_targets.clear()
        self.static_quality["left"].clear()
        self.static_quality["right"].clear()
        self.static_val_records.clear()
        self.static_val_gaze_samples.clear()
        self.revisit = {"left": [], "right": []}
        self.first_looks = {"left": [], "right": []}
        self.extra_looks = {"left": {}, "right": {}}
        self.combined = {"left": [], "right": []}
        self.anchor_weights = {}
        self.live_gaze_pos, self.gaze_trail = None, []
        self._reset_point_buffers()
        self.point_retries = 0
        self.stage = "CALIB"
        self.target_idx = 0
        self.phase = "APPROACH"
        self.phase_t0 = time.time()

    def _on_gate_proceed(self):
        """User pressed [Enter] or grace timer elapsed to proceed to dynamic line tracking."""
        self._teardown_gate_keys()
        self.stage = "PURSUIT"
        self.pursuit_idx = 0
        self.phase = "APPROACH"
        self.phase_t0 = time.time()
        self.live_gaze_pos, self.gaze_trail = None, []
        self._path_pupil_window_l.clear()
        self._path_pupil_window_r.clear()

    # =========================================================================
    # Completion
    # =========================================================================
    def _finish_calibration(self):
        self.stage = "DONE"
        self.canvas.delete("all")

        ok = self.calibrator.fit(self.calib_left_medians, self.calib_right_medians, self.calib_targets,
                                 self.val_records, self.pursuit_records,
                                 static_quality=self.static_quality,
                                 static_val_records=self.static_val_records,
                                 anchor_weights=self.anchor_weights)

        cx, cy = self.screen_w // 2, self.screen_h // 2

        if ok:
            es = self.calibrator.measure_end_shift(self.val_records)   # stored; the app switch decides if it is used
            if es:
                print(f"[Calibration] end shift ({es['shift_px'][0]:+.0f}, {es['shift_px'][1]:+.0f}) px "
                      f"{'stored' if es['stored'] else 'not stored: ' + es.get('reason', '')} · check dots "
                      f"{es['val_mean_before_px']} px, with it {es['val_mean_after_loo_px']} px (leave-one-out)")
            self._learn_grid_shape()
            self.calibrator.metrics["background"] = self.background
            alg = self.controller.get_pupil_algorithm() if hasattr(self.controller, "get_pupil_algorithm") else "ArcFit"
            ts = time.strftime("%Y%m%d_%H%M%S")
            alg_safe = alg.replace(" ", "_")
            session_folder = f"{ts}_{alg_safe}"
            session_dir = os.path.join(_THIS_DIR, "data", "calibrations", session_folder)
            os.makedirs(session_dir, exist_ok=True)

            # 1. Save full historical archive and sync root JSONs
            self.calibrator.save_archive(session_dir, alg, root_dir=_THIS_DIR)
            self._saved = True
            if self._rec is not None:
                self._rec.link("calibration_archive", session_folder)

            # 2. Diagnostic chart (session archive + root copy) drawn in the background: no frozen screen
            render_chart_async(self.calibrator.metrics, self.calibrator.pursuit_records,
                               os.path.join(session_dir, "calibration_chart.png"), "Calibration")

            # no "Calibration Complete" message screen (user, 2026-11-24): close as soon as it is saved;
            # grade and errors are in summary.txt, the chart and the app's Calibration card
            self.after(10, self._close_and_notify)
        else:
            n_l = sum(1 for s in self.calib_left_medians if s is not None)
            n_r = sum(1 for s in self.calib_right_medians if s is not None)
            self.canvas.create_text(
                cx, cy,
                text=(f"Calibration Failed: insufficient pupil signal.\n"
                      f"Usable static points — Left: {n_l}/9, Right: {n_r}/9 (need at least 4 for an eye in use).\n"
                      "Check camera alignment, boundary cage and lighting."),
                font=("Segoe UI", 20, "bold"), fill=RED, justify="center")
            self.after(3000, self.cancel)

    def _close_and_notify(self):
        self._active = False
        self._stop_recording()
        if callable(self.on_complete):
            self.on_complete(self.calibrator.metrics, CHART_ROOT)   # chart may still be drawing (render_chart_async)
        self.destroy()

    def cancel(self):
        if self.stage == "DONE" and getattr(self, "_saved", False):
            return self._close_and_notify()            # already saved: ESC just closes, the new calibration is used
        if not self._active and self.stage != "DONE":
            return
        self._active = False
        self._discard_recording()                      # not saved -> the unfinished recording is deleted
        if self._anim_timer is not None:
            try:
                self.after_cancel(self._anim_timer)
            except Exception:
                pass
        if callable(self.on_cancel):
            self.on_cancel()
        self.destroy()

    def destroy(self):
        # also reached when the app / launcher closes mid-calibration (they destroy the window, not cancel()):
        # keep the recording only if the calibration was saved; wait so the deletion finishes before exit
        if getattr(self, "_saved", False):
            self._stop_recording()
        else:
            self._discard_recording(wait=True)
        super().destroy()

    # =========================================================================
    # Renderer
    # =========================================================================
    def _draw_pointer(self, big: bool = True):
        # only the drawing is switched off: live_gaze_pos is still computed (the 9-point check records it)
        if pointer_enabled(self.controller):
            draw_gaze_pointer(self.canvas, self.live_gaze_pos, self.gaze_trail, big)

    def _render(self, elapsed: float):
        self.canvas.delete("all")
        for gx, gy in self.calibrator.grid_px:
            self.canvas.create_oval(gx - 3, gy - 3, gx + 3, gy + 3, fill=GRID_GUIDE, outline="")
        if self.stage == "DONE":
            return

        if self.stage == "CALIB":
            targets = self.calibrator.grid_px
            tx, ty = targets[self.target_idx]
            self.canvas.create_text(self.screen_w // 2, 40,
                                    text=f"Stage 1: Static Calibration Point {self.target_idx + 1} of {len(targets)}",
                                    font=("Segoe UI", 15, "bold"), fill=CYAN)
            self.canvas.create_text(self.screen_w // 2, 68, text="Focus steadily on the center dot  ·  [ESC] to Cancel",
                                    font=("Segoe UI", 11), fill=TEXT_MUTED)
            if self.phase in ("START", "APPROACH"):
                frac = 0.0 if self.phase == "START" else min(1.0, elapsed / self.APPROACH_SECS)
                ring_r = 65 - int(48 * frac)
                self.canvas.create_oval(tx - ring_r, ty - ring_r, tx + ring_r, ty + ring_r, outline=CYAN, width=2)
                self.canvas.create_oval(tx - 7, ty - 7, tx + 7, ty + 7, fill=TEXT_WHITE, outline=CYAN, width=2)
            elif self.phase == "COLLECT":
                frac = min(1.0, elapsed / self.COLLECT_SECS)
                extent_deg = int(round(360.0 * frac))
                self.canvas.create_oval(tx - 24, ty - 24, tx + 24, ty + 24, outline=RING_TRACK, width=3)
                if extent_deg > 0:
                    self.canvas.create_arc(tx - 24, ty - 24, tx + 24, ty + 24, start=90, extent=-extent_deg,
                                           outline=CYAN, width=3, style="arc")
                self.canvas.create_oval(tx - 8, ty - 8, tx + 8, ty + 8, fill=TEXT_WHITE, outline=CYAN, width=2)
                # Tiny live counters so the operator can see both eyes are being accepted
                self.canvas.create_text(tx, ty + 44 if ty < self.screen_h - 70 else ty - 44,
                                        text=f"L {len(self.curr_left_samples)}  ·  R {len(self.curr_right_samples)}",
                                        font=("Segoe UI", 9), fill=TEXT_MUTED)
            elif self.phase == "RETRY":
                pulse = int(4 * abs(math.sin(elapsed * 6.0)))
                self.canvas.create_oval(tx - 34 - pulse, ty - 34 - pulse, tx + 34 + pulse, ty + 34 + pulse,
                                        outline=GOLD, width=3)
                self.canvas.create_oval(tx - 8, ty - 8, tx + 8, ty + 8, fill=GOLD, outline=TEXT_WHITE, width=2)
                lbl_y = ty + 52 if ty < self.screen_h - 90 else ty - 52
                self.canvas.create_text(tx, lbl_y, text=f"⚠  {self.retry_msg}",
                                        font=("Segoe UI", 11, "bold"), fill=GOLD)
            elif self.phase == "FLASH":
                # a disabled eye counts as fine (single-eye mode)
                ok_l = (self.calib_left_medians and self.calib_left_medians[-1] is not None) or not self.controller.is_eye_enabled("left")
                ok_r = (self.calib_right_medians and self.calib_right_medians[-1] is not None) or not self.controller.is_eye_enabled("right")
                col = GREEN if (ok_l and ok_r) else (GOLD if (ok_l or ok_r) else RED)
                self.canvas.create_oval(tx - 32, ty - 32, tx + 32, ty + 32, outline=col, width=3)
                self.canvas.create_oval(tx - 8, ty - 8, tx + 8, ty + 8, fill=col, outline=TEXT_WHITE, width=2)

        elif self.stage == "RECHECK":
            tx, ty = self.calibrator.grid_px[self.target_idx]
            left = len(self.recheck_queue)
            self.canvas.create_text(self.screen_w // 2, 40,
                                    text=f"Re-checking a dot  ·  {left} left",
                                    font=("Segoe UI", 15, "bold"), fill=GOLD)
            self.canvas.create_text(self.screen_w // 2, 68,
                                    text="The pupil reading at this dot did not match the rest  ·  focus on the dot once more",
                                    font=("Segoe UI", 11), fill=TEXT_MUTED)
            if self.phase == "APPROACH":
                frac = min(1.0, elapsed / self.APPROACH_SECS)
                ring_r = 65 - int(48 * frac)
                self.canvas.create_oval(tx - ring_r, ty - ring_r, tx + ring_r, ty + ring_r, outline=GOLD, width=2)
            else:
                ext = int(round(360.0 * min(1.0, elapsed / self.COLLECT_SECS)))
                if ext > 0:
                    self.canvas.create_arc(tx - 24, ty - 24, tx + 24, ty + 24, start=90, extent=-ext,
                                           outline=GOLD, width=3, style="arc")
            self.canvas.create_oval(tx - 8, ty - 8, tx + 8, ty + 8, fill=TEXT_WHITE, outline=GOLD, width=2)

        elif self.stage == "STATIC_VAL":
            targets = self.calibrator.grid_px
            tx, ty = targets[self.target_idx]
            self.canvas.create_text(self.screen_w // 2, 40,
                                    text=f"Phase 2: 9-Point Live Validation — Point {self.target_idx + 1} of {len(targets)}",
                                    font=("Segoe UI", 16, "bold"), fill=CYAN)
            self.canvas.create_text(self.screen_w // 2, 68,
                                    text="Look steadily at the target" + (f"  ·  The {POINTER_NAME} crosshair shows your live detected gaze"
                                                                         if pointer_enabled(self.controller) else ""),
                                    font=("Segoe UI", 11), fill=TEXT_MUTED)

            arm = 22
            self.canvas.create_line(tx - arm, ty, tx + arm, ty, fill=TEXT_WHITE, width=2)
            self.canvas.create_line(tx, ty - arm, tx, ty + arm, fill=TEXT_WHITE, width=2)
            self.canvas.create_oval(tx - 7, ty - 7, tx + 7, ty + 7, fill=CYAN, outline=TEXT_WHITE, width=2)

            if self.phase == "COLLECT":
                frac = min(1.0, elapsed / self.STATIC_VAL_COLLECT_SECS)
                ext = int(round(360.0 * frac))
                if ext > 0:
                    self.canvas.create_arc(tx - 28, ty - 28, tx + 28, ty + 28, start=90, extent=-ext,
                                           outline=CYAN, width=3, style="arc")
            self._draw_pointer(big=True)

        elif self.stage == "ACCEPTANCE_GATE":
            errs = [r["error_px"] for r in self.static_val_records if r.get("error_px") is not None and r["error_px"] < 500]
            mean_err = float(np.mean(errs)) if errs else 0.0

            if mean_err <= 35.0:
                grade_str, grade_col = "Grade A [EXCELLENT - CLINICAL]", GREEN
            elif mean_err <= 50.0:
                grade_str, grade_col = "Grade B [GOOD - ACCESSIBLE]", CYAN
            elif mean_err <= 75.0:
                grade_str, grade_col = "Grade C [ACCEPTABLE]", GOLD
            else:
                grade_str, grade_col = "Grade D [POOR - REDO ADVISED]", RED

            for r in self.static_val_records:
                tx, ty = r["target"]
                gz = r.get("gaze")
                err = r.get("error_px", 0.0)

                self.canvas.create_oval(tx - 6, ty - 6, tx + 6, ty + 6, fill=TEXT_MUTED, outline="")

                if gz is not None:
                    gx, gy = gz
                    col = GREEN if err < 38.0 else (GOLD if err < 70.0 else RED)
                    self.canvas.create_line(tx, ty, gx, gy, fill=col, width=2)
                    self.canvas.create_oval(gx - 5, gy - 5, gx + 5, gy + 5, fill=col, outline=TEXT_WHITE, width=1)
                    self.canvas.create_text(gx, gy + 16, text=f"{err:.0f}px", font=("Segoe UI", 9, "bold"), fill=col)

            cx, cy = self.screen_w // 2, self.screen_h // 2
            countdown = max(0.0, self.GATE_TIMEOUT_SECS - elapsed)

            w_hud, h_hud = 640, 240
            round_rect(self.canvas, cx - w_hud // 2, cy - h_hud // 2, cx + w_hud // 2, cy + h_hud // 2, 18,
                       fill=HUD_FILL, outline=BORDER, width=2)

            self.canvas.create_text(cx, cy - 70, text="9-POINT STATIC CALIBRATION VALIDATION",
                                    font=("Segoe UI", 16, "bold"), fill=TEXT_WHITE)
            self.canvas.create_text(cx, cy - 35,
                                    text=f"Quality: {grade_str}   ·   Mean Error: {mean_err:.1f} px",
                                    font=("Segoe UI", 14, "bold"), fill=grade_col)

            self.canvas.create_text(cx, cy + 15,
                                    text="[Spacebar]  or  [R]  :  Redo 9-Point Calibration",
                                    font=("Segoe UI", 13, "bold"), fill=GOLD)
            self.canvas.create_text(cx, cy + 50,
                                    text=f"[Enter]  or  Auto-Proceed in {countdown:.1f}s  :  Dynamic Line Tracking",
                                    font=("Segoe UI", 13, "bold"), fill=CYAN)
            self.canvas.create_text(cx, cy + 85,
                                    text=f"Vectors show measured gaze landing for each point (Green < 38px, {GOLD_NAME.capitalize()} < 70px, Red > 70px)",
                                    font=("Segoe UI", 9), fill=TEXT_MUTED)

        elif self.stage == "PURSUIT":
            paths = self.calibrator.pursuit_px
            p_start, p_end = paths[self.pursuit_idx]
            path_name = self.PATH_NAMES[self.pursuit_idx] if self.pursuit_idx < len(self.PATH_NAMES) else f"Path {self.pursuit_idx + 1}"
            self.canvas.create_text(self.screen_w // 2, 40,
                                    text=f"Stage 2: Smooth Pursuit Path {self.pursuit_idx + 1} of {len(paths)} — {path_name}",
                                    font=("Segoe UI", 15, "bold"), fill=GOLD)
            self.canvas.create_text(self.screen_w // 2, 68,
                                    text=f"Follow the moving {GOLD_NAME} dot smoothly" + (f"  ·  the {POINTER_NAME} pointer is your live tracked gaze"
                                                                                 if pointer_enabled(self.controller) else ""),
                                    font=("Segoe UI", 11), fill=CYAN)
            self.canvas.create_line(p_start[0], p_start[1], p_end[0], p_end[1], fill=PATH_GUIDE, width=2, dash=(6, 6))

            if self.phase == "APPROACH":
                tx, ty = p_start
                frac = min(1.0, elapsed / self.TRACK_BREAK_SECS)
                ease = 0.5 - 0.5 * math.cos(frac * math.pi)
                ring_r = int(75 - (75 - 14) * ease)
                self.canvas.create_oval(tx - 75, ty - 75, tx + 75, ty + 75, outline=PATH_GUIDE, width=1, dash=(4, 4))
                self.canvas.create_oval(tx - ring_r, ty - ring_r, tx + ring_r, ty + ring_r, outline=GOLD, width=3)
                pulse = int(2 * abs(math.sin(elapsed * 4.0)))
                self.canvas.create_oval(tx - 8 - pulse, ty - 8 - pulse, tx + 8 + pulse, ty + 8 + pulse,
                                        fill=GOLD, outline=TEXT_WHITE, width=2)
                countdown_s = max(0.0, self.TRACK_BREAK_SECS - elapsed)
                lbl_y = ty + 95 if ty < self.screen_h - 120 else ty - 95
                self.canvas.create_text(tx, lbl_y,
                                        text=f"Break: blink & settle on the start dot  ·  gliding in {countdown_s:.1f}s",
                                        font=("Segoe UI", 11, "bold"), fill=GOLD)
            elif self.phase == "TRACK":
                frac = min(1.0, elapsed / self.TRACK_SECS)
                tx = int(round(p_start[0] + (p_end[0] - p_start[0]) * frac))
                ty = int(round(p_start[1] + (p_end[1] - p_start[1]) * frac))
                self.canvas.create_oval(tx - 20, ty - 20, tx + 20, ty + 20, outline=GOLD, width=2)
                self.canvas.create_oval(tx - 8, ty - 8, tx + 8, ty + 8, fill=GOLD, outline=TEXT_WHITE, width=2)
            elif self.phase == "RETRY":
                tx, ty = p_start
                pulse = int(4 * abs(math.sin(elapsed * 6.0)))
                self.canvas.create_oval(tx - 40 - pulse, ty - 40 - pulse, tx + 40 + pulse, ty + 40 + pulse,
                                        outline=GOLD, width=3)
                self.canvas.create_oval(tx - 8, ty - 8, tx + 8, ty + 8, fill=GOLD, outline=TEXT_WHITE, width=2)
                lbl_y = ty + 65 if ty < self.screen_h - 90 else ty - 65
                self.canvas.create_text(self.screen_w // 2, lbl_y, text=f"⚠  {self.retry_msg}",
                                        font=("Segoe UI", 12, "bold"), fill=GOLD)
            self._draw_pointer(big=True)

        elif self.stage == "VAL":
            targets = self.calibrator.val_px
            tx, ty = targets[self.target_idx]
            self.canvas.create_text(self.screen_w // 2, 40,
                                    text=f"Stage 3: Spatial Validation Point {self.target_idx + 1} of {len(targets)}"
                                         + ("  ·  one more look" if self.val_retake_done else ""),
                                    font=("Segoe UI", 15, "bold"), fill=GREEN)
            self.canvas.create_text(self.screen_w // 2, 68,
                                    text="Focus on the target dot to verify real accuracy  ·  [ESC] to Cancel",
                                    font=("Segoe UI", 11), fill=TEXT_MUTED)
            if self.phase in ("APPROACH", "COLLECT"):
                arm = 20
                self.canvas.create_line(tx - arm, ty, tx + arm, ty, fill=TEXT_WHITE, width=2)
                self.canvas.create_line(tx, ty - arm, tx, ty + arm, fill=TEXT_WHITE, width=2)
                self.canvas.create_oval(tx - 6, ty - 6, tx + 6, ty + 6, fill=GREEN, outline=TEXT_WHITE, width=2)
                if self.phase == "COLLECT":
                    frac = min(1.0, elapsed / self.COLLECT_SECS)
                    extent_deg = int(round(360.0 * frac))
                    if extent_deg > 0:
                        self.canvas.create_arc(tx - 26, ty - 26, tx + 26, ty + 26, start=90, extent=-extent_deg,
                                               outline=GREEN, width=2, style="arc")
            elif self.phase == "FLASH":
                self.canvas.create_oval(tx - 28, ty - 28, tx + 28, ty + 28, outline=GREEN, width=3)
                self.canvas.create_oval(tx - 7, ty - 7, tx + 7, ty + 7, fill=GREEN, outline=TEXT_WHITE, width=2)
            self._draw_pointer(big=False)


class RecentreOverlay(tk.Toplevel):
    """
    [R1] Quick Re-centre (~8 s): 5 dots (centre + 4 edge middles). For each dot the fused gaze is collected
    for 0.9 s after a 0.8 s settle; the median of (dot - gaze) over the dots becomes calibrator.offset.
    Fixes the headset slip that builds up after calibration (measured 15-70 screen px within a minute) without
    a full recalibration. Tested on the 5 saved sessions: corner error 41.8 -> 31.0 px.
    """
    POINTS = [(0.5, 0.5), (0.5, 0.12), (0.12, 0.5), (0.88, 0.5), (0.5, 0.88)]
    SETTLE_S, COLLECT_S = 0.8, 0.9
    MAX_SHIFT = 250.0          # larger shifts mean something else is wrong: recalibrate instead

    def __init__(self, master, controller, calibrator, on_done: Optional[Callable] = None):
        super().__init__(master)
        self.controller, self.cal, self.on_done = controller, calibrator, on_done
        use_palette(controller)
        self.attributes("-fullscreen", True)
        self.attributes("-topmost", True)
        self.configure(bg=BG_COLOR)
        self.sw, self.sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.canvas = tk.Canvas(self, width=self.sw, height=self.sh, bg=BG_COLOR, highlightthickness=0, cursor="none")
        self.canvas.pack(fill="both", expand=True)
        self.bind("<Escape>", lambda e: self._finish(None))
        self.focus_force()
        self.idx, self.t0, self.samples, self.offsets = 0, time.time(), [], []
        self.seq = (-1, -1)
        self.pointer = GazePointer(controller, self.sw, self.sh)
        self._tick()

    def _target(self):
        fx, fy = self.POINTS[self.idx]
        return int(fx * self.sw), int(fy * self.sh)

    def _tick(self):
        if not self.winfo_exists():
            return
        el = time.time() - self.t0
        tx, ty = self._target()
        collecting = el >= self.SETTLE_S
        sl, sr = self.controller.get_samples()
        if (sl.seq, sr.seq) != self.seq and (collecting or pointer_enabled(self.controller)):
            self.seq = (sl.seq, sr.seq)
            lp = sl.pupil if sl.connected and sl.conf > 0.15 else None
            rp = sr.pupil if sr.connected and sr.conf > 0.15 else None
            pred, _, det = self.cal.predict(lp, rp, sl.conf if lp else 0.0, sr.conf if rp else 0.0)
            self.pointer.update(pred)                          # display only (with the current correction)
            if collecting:
                g = det.get("raw_fused")
                if g is not None:
                    ox, oy = self.cal.correction()                     # offset (+ end shift when switched on)
                    self.samples.append((g[0] - ox, g[1] - oy))        # gaze without the current correction
        if el >= self.SETTLE_S + self.COLLECT_S:
            if len(self.samples) >= 8:
                med = np.median(np.array(self.samples), axis=0)
                self.offsets.append((tx - med[0], ty - med[1]))
            self.idx, self.t0, self.samples = self.idx + 1, time.time(), []
            if self.idx >= len(self.POINTS):
                return self._finish(np.median(np.array(self.offsets), axis=0) if len(self.offsets) >= 3 else None)
            tx, ty = self._target()
        c = self.canvas
        c.delete("all")
        c.create_text(self.sw // 2, 40, text=f"Quick Re-centre  ·  look at the dot  ({self.idx + 1}/{len(self.POINTS)})  ·  [ESC] cancel",
                      font=("Segoe UI", 15, "bold"), fill=CYAN)
        r = 8 if el >= self.SETTLE_S else int(30 - 22 * min(1.0, el / self.SETTLE_S))
        c.create_oval(tx - r, ty - r, tx + r, ty + r, outline=CYAN, width=2)
        c.create_oval(tx - 5, ty - 5, tx + 5, ty + 5, fill=TEXT_WHITE, outline="")
        self.pointer.draw(c, big=False)
        self.after(16, self._tick)

    def _finish(self, off):
        msg = "Re-centre cancelled"
        if off is not None:
            if math.hypot(off[0], off[1]) <= self.MAX_SHIFT:
                self.cal.offset = (float(off[0]), float(off[1]))
                self.cal.end_shift = None                      # the re-centre measured the whole correction again
                try:
                    self.cal.save(os.path.join(_THIS_DIR, "binocular_calibration.json"))
                except Exception as e:
                    print(f"[Recentre] could not save: {e}")
                msg = f"Re-centred: shift ({off[0]:+.0f}, {off[1]:+.0f}) px"
            else:
                msg = f"Shift too large ({off[0]:+.0f}, {off[1]:+.0f}) px - please recalibrate"
        elif self.offsets is not None and self.idx >= len(self.POINTS):
            msg = "Re-centre failed: too little pupil signal"
        if callable(self.on_done):
            self.on_done(msg)
        self.destroy()
