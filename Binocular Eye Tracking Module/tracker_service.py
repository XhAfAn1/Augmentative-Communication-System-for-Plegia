#!/usr/bin/env python3
"""
tracker_service.py
==================
The eye tracker without the launcher window, controlled by the Flutter AAC app over a local HTTP API.

It runs exactly the same code as launcher.py (BinocularController, BinocularGazeEngine, the calibration /
fine-tune / re-centre overlays and the Eye View window); only the control panel moved to Flutter.
launcher.py keeps working on its own (never run both at once: they would fight over the cameras).

  python tracker_service.py [--port 8001] [--parent-pid PID]

  * listens on 127.0.0.1 only; one instance per port (exits with code 3 if the port is taken, before any camera opens)
  * a hidden Tk root owns all windows; every command that changes something runs on that Tk thread, in order
    (same as clicking the launcher), status reads come straight from the thread-safe controller / engine
  * --parent-pid: the app that started us; if it disappears (crash), the service shuts down and frees the cameras

API (JSON in/out; every reply has "ok"):
  GET  /health                    GET /status                   GET /calibration/chart (PNG)
  POST /eyes/<left|right>/enabled {enabled}      /eyes/<eye>/camera {camera_id}   /eyes/<eye>/flip {h, v}
  POST /algorithm {name}          /pupil_smoothing {level}
  POST /mouse/enabled {enabled}   /mouse/toggle    /mouse/mode {mode}   /mouse/params {alpha, gain, dpi, grace_ms,
       spacebar_click}            /mouse/reset     /mouse/spacebar_pause {paused}
  POST /calibration/start  /calibration/finetune  /calibration/recentre  /calibration/record {enabled}
       /calibration/pointer {enabled}  (show the live gaze pointer in all three calibrations)
       /calibration/auto_recentre {enabled}   /calibration/implicit {dx, dy, age_ms}  (auto re-centre, see cmd_implicit)
       /calibration/chart/open    /eyeview/open    /eyeview/close        /shutdown
"""

import os
import sys
import math
import json
import time
import queue
import ctypes
import argparse
import threading
import tkinter as tk
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from typing import Any, Callable, Dict, Optional

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

DEFAULT_PORT = 8001
CALIB_FILE = os.path.join(_THIS_DIR, "binocular_calibration.json")
CHART_FILE = os.path.join(_THIS_DIR, "binocular_calibration_chart.png")
EYES = ("left", "right")
AVAILABLE_CAMERAS = [0, 1, 2]


class _Server(ThreadingHTTPServer):
    allow_reuse_address = False      # on Windows SO_REUSEADDR would let a second copy bind the same port
    daemon_threads = True


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _pid_alive(pid: int) -> bool:
    """Windows-safe liveness check (os.kill(pid, 0) would TERMINATE the process on Windows)."""
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, int(pid))          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        return bool(ok) and code.value == 259             # STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


def _to_front(w):
    """Bring a Tk window to the foreground (the app that asked for it allows this, see the Flutter client)."""
    try:
        w.lift()
        w.focus_force()
        hwnd = ctypes.windll.user32.GetParent(w.winfo_id())
        ctypes.windll.user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


class TrackerService:
    def __init__(self, root: tk.Tk, controller=None, engine=None):
        # imported here so `--help` and a busy port never touch the cameras
        from binocular_controller import BinocularController
        from gaze_engine import BinocularGazeEngine
        self.root = root
        self.controller = controller or BinocularController()
        self.controller.start()
        self.engine = engine or BinocularGazeEngine(self.controller)
        self.engine.start()
        self.jobs: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self.activity: Optional[str] = None           # "calibration" | "finetune" | "recentre" while an overlay runs
        self.overlay = None
        self.eye_win = None
        self.event_seq = 0                            # increases with every finished action (the app shows a message)
        self.event_msg = ""
        self.space_paused = False                     # the app owns the Spacebar while it is in front
        self.stopping = False
        self.root.after(20, self._pump)

    # ------------------------------------------------------------------ Tk-thread execution
    def call(self, fn: Callable[[], Any], timeout: float = 15.0) -> Any:
        """Runs fn on the Tk thread (where the launcher would run it) and returns its result."""
        if self.stopping:
            raise ApiError(503, "tracker is shutting down")
        done, box = threading.Event(), {}

        def job():
            try:
                box["r"] = fn()
            except Exception as e:                    # passed back to the HTTP thread
                box["e"] = e
            finally:
                done.set()
        self.jobs.put(job)
        if not done.wait(timeout):
            raise ApiError(504, "tracker UI thread busy")
        if "e" in box:
            raise box["e"]
        return box.get("r")

    def _pump(self):
        while True:
            try:
                job = self.jobs.get_nowait()
            except queue.Empty:
                break
            job()
        if not self.stopping:
            self.root.after(20, self._pump)

    def _event(self, msg: str):
        self.event_seq += 1
        self.event_msg = msg
        print(f"[TrackerService] {msg}", flush=True)

    # ------------------------------------------------------------------ status (any thread)
    def status(self) -> Dict[str, Any]:
        st = self.controller.get_status()
        tel = self.engine.get_telemetry()
        cfg = self.controller.cfg
        cal = self.engine.calibrator
        m = cal.metrics if cal.is_calibrated else {}
        eyes = {}
        for e in EYES:
            s = st[e]
            eyes[e] = {k: s.get(k) for k in ("enabled", "camera_id", "connected", "fps", "det_fps", "conf",
                                               "latency_ms", "flip_h", "flip_v")}
            eyes[e]["pupil"] = list(s["pupil"]) if s.get("pupil") else None
            eyes[e]["cage"] = s.get("boundary")
        from data.pupil_algorithm import ALGORITHM_NAMES, ALGORITHM_DISPLAY
        return {
            "ok": True,
            "service": {"pid": os.getpid(), "time": time.time()},
            "algorithm": st.get("pupil_algorithm"),
            "algorithms": [{"id": a, "label": ALGORITHM_DISPLAY.get(a, a)} for a in ALGORITHM_NAMES + ["None"]],
            "pupil_smoothing": cfg.get("pupil_smoothing", 3),
            "record_calibration": bool(cfg.get("record_calibration", False)),
            "show_gaze_pointer": bool(cfg.get("show_gaze_pointer", True)),
            "auto_recentre": bool(cfg.get("auto_recentre", False)),
            "auto_recentre_info": self._implicit_info(),
            "eyes": eyes,
            "mouse": {
                "enabled": tel.get("mouse_enabled"), "mode": tel.get("mouse_mode"),
                "state": tel.get("cursor_state"), "cursor": tel.get("cursor_pos"), "fps": tel.get("fps"),
                "signal_mode": tel.get("mode"), "alpha": tel.get("alpha"), "gain": tel.get("gain"),
                "dpi": tel.get("dpi"), "grace_ms": tel.get("grace_ms"), "effective_gain": tel.get("effective_gain"),
                "spacebar_click": bool(cfg.get("mouse_spacebar_click", True)),   # the user's setting
                "spacebar_paused": self.space_paused,                          # the app has the Spacebar now
                "failsafe_tripped": tel.get("failsafe_tripped"),
            },
            "calibration": {
                "calibrated": bool(cal.is_calibrated), "grade": m.get("grade"),
                "mean_error_px": m.get("mean_error_px"), "pursuit_rmse": m.get("pursuit_rmse"),
                "mean_jitter": m.get("mean_jitter"), "calibrated_at": m.get("calibrated_at"),
                "offset": list(getattr(cal, "offset", (0.0, 0.0))),
                "can_finetune": bool(getattr(cal, "anchors", None)),
                "chart_version": os.path.getmtime(CHART_FILE) if os.path.exists(CHART_FILE) else None,
                "finetune": m.get("finetune"),
            },
            "activity": self.activity,
            "eye_view_open": self.eye_win is not None,
            "event": {"seq": self.event_seq, "message": self.event_msg},
        }

    # ------------------------------------------------------------------ helpers (Tk thread)
    def _load_calibration(self):
        from binocular_calibrator import BinocularCalibrator
        cal = BinocularCalibrator()
        cal.load(CALIB_FILE)
        return cal                                   # handed over complete: the mouse never sees a half-loaded one

    def _apply_space(self):
        """Effective engine Spacebar click = the user's setting, unless the app owns the Spacebar right now.
        Not saved: the saved setting stays the user's choice."""
        want = bool(self.controller.cfg.get("mouse_spacebar_click", True)) and not self.space_paused
        with self.engine._lock:
            self.engine.spacebar_click = want
            self.engine.telemetry["spacebar_click"] = want

    def _set_mouse_params(self, **kw):
        # set_mouse_params saves every mouse setting incl. spacebar_click: give it the user's value, then re-apply
        with self.engine._lock:
            self.engine.spacebar_click = bool(self.controller.cfg.get("mouse_spacebar_click", True))
        self.engine.set_mouse_params(**kw)
        self._apply_space()

    def _need_idle(self):
        if self.activity is not None:
            raise ApiError(409, f"{self.activity} is already running")

    def _start_overlay(self, kind: str, make: Callable[[Callable[[str], None]], Any]):
        self._need_idle()

        def finished(msg: str, reload: bool = True):
            if self.activity is None:
                return
            self.activity, self.overlay = None, None
            if reload:
                self.engine.calibrator = self._load_calibration()
            self._event(msg)

        self.activity = kind
        try:
            ov = make(finished)
        except Exception:
            self.activity = None
            raise
        self.overlay = ov
        # safety net: if the window disappears without a callback, the service is free again
        ov.bind("<Destroy>", lambda e: (e.widget is ov) and self.activity == kind and finished(f"{kind} closed"), add="+")
        self.root.after(80, lambda: _to_front(ov))
        return {"ok": True, "started": kind}

    # ------------------------------------------------------------------ commands (Tk thread)
    def cmd_eye_enabled(self, eye: str, enabled: bool):
        if not self.controller.set_eye_enabled(eye, bool(enabled)):
            raise ApiError(409, "At least one eye must stay enabled.")
        return {"ok": True}

    def cmd_camera(self, eye: str, cam: int):
        """Same rule as the Eye View: if the other eye uses that camera, it moves to a free one."""
        c = self.controller
        other = "right" if eye == "left" else "left"
        if int(c.cfg.get(f"camera_{eye}_id", 0)) == cam:
            return {"ok": True}
        if int(c.cfg.get(f"camera_{other}_id", 1)) == cam and c.is_eye_enabled(other):
            rem = [x for x in AVAILABLE_CAMERAS if x != cam]
            c.set_camera(other, rem[0] if rem else (1 if other == "right" else 0))
        c.set_camera(eye, cam)
        return {"ok": True}

    def cmd_flip(self, eye: str, h: bool, v: bool):
        self.controller.set_flip(eye, bool(h), bool(v))
        return {"ok": True}

    def cmd_algorithm(self, name: str):
        from data.pupil_algorithm import to_canonical_name
        self.controller.set_pupil_algorithm(to_canonical_name(name))
        return {"ok": True}

    def cmd_smoothing(self, level: float):
        self.controller.set_pupil_smoothing(float(level))
        return {"ok": True}

    def cmd_mouse_enabled(self, enabled: Optional[bool]):
        if enabled is None:
            return {"ok": True, "enabled": self.engine.toggle_mouse()}
        self.engine.set_mouse_enabled(bool(enabled))
        return {"ok": True, "enabled": bool(enabled)}

    def cmd_mouse_mode(self, mode: str):
        if mode not in ("smoother", "filtered", "raw"):
            raise ApiError(400, f"unknown mouse mode {mode!r}")
        self.engine.set_mouse_mode(mode)
        return {"ok": True}

    def cmd_mouse_params(self, body: Dict[str, Any]):
        kw = {k: body[k] for k in ("alpha", "gain", "dpi", "grace_ms", "spacebar_click") if body.get(k) is not None}
        if "spacebar_click" in kw:                    # the user's own setting changes: save it first
            self.controller.set_mouse_settings(mouse_spacebar_click=bool(kw["spacebar_click"]))
        self._set_mouse_params(**kw)
        return {"ok": True}

    def cmd_mouse_reset(self):
        """Same as the launcher's 'Reset to EyeTracker Defaults' (alpha 0.15, gain 1, 96 DPI, 240 ms, Spacebar on)."""
        self.controller.set_mouse_settings(mouse_spacebar_click=True)
        self._set_mouse_params(alpha=0.15, gain=1.0, dpi=96.0, grace_ms=240, spacebar_click=True, edge_expansion=True)
        return {"ok": True}

    def cmd_space_pause(self, paused: bool):
        self.space_paused = bool(paused)
        self._apply_space()
        return {"ok": True, "paused": self.space_paused}

    def cmd_record(self, enabled: bool):
        self.controller.set_mouse_settings(record_calibration=bool(enabled))
        return {"ok": True}

    # ------------------------------------------------------------------ auto re-centre (learned from selections)
    IMPLICIT_MAX_SAMPLE = 150   # px: a bigger difference = probably not looking at that button -> ignored

    def _implicit_info(self):
        e = self.engine
        if e._impl_cal is not e.calibrator:          # new calibration loaded: nothing learned for it yet
            return {"count": 0, "shift": [0, 0], "scale": [1.0, 1.0]}
        sx, sy, tx, ty = e.impl
        return {"count": e.impl_count, "shift": [round(tx), round(ty)], "scale": [round(sx, 3), round(sy, 3)]}

    def cmd_auto_recentre(self, enabled: bool):
        self.controller.set_mouse_settings(auto_recentre=bool(enabled))
        return {"ok": True}

    def cmd_implicit(self, dx, dy, age_ms):
        """The app reports a button selection made `age_ms` ago: (button centre - where the cursor rested) in screen
        px; dx or dy is None for an axis the button is too long in (e.g. Space). The engine refits its correction
        (shift, and scale when the selections are spread) to the last 5 selections (gaze_engine.add_implicit_sample).
        Not saved: a new calibration / fine-tune / re-centre starts without correction."""
        why = None
        if not bool(self.controller.cfg.get("auto_recentre", False)):
            why = "off"
        elif self.activity is not None:
            why = f"{self.activity} running"
        elif not self.engine.mouse_enabled:
            why = "eye mouse off"           # e.g. a caregiver used the real mouse
        elif not self.engine.calibrator.is_calibrated:
            why = "not calibrated"
        elif math.hypot(0.0 if dx is None else float(dx), 0.0 if dy is None else float(dy)) > self.IMPLICIT_MAX_SAMPLE:
            why = "too far from the button"
        if why is not None:
            return {"ok": True, "used": False, "why": why}
        r = self.engine.add_implicit_sample(float(age_ms or 0) / 1000.0, dx, dy)
        if r.get("used"):
            sx, sy = r["scale"]
            tx, ty = r["shift"]
            print(f"[AutoRecentre] selection off by ({0 if dx is None else dx:+.0f}, {0 if dy is None else dy:+.0f}) px"
                  f" -> correction shift ({tx:+.0f}, {ty:+.0f}) px, scale ({sx:.3f}, {sy:.3f})", flush=True)
        return {"ok": True, **r}

    def cmd_pointer(self, enabled: bool):
        self.controller.set_mouse_settings(show_gaze_pointer=bool(enabled))   # overlays read it every frame
        return {"ok": True}

    def cmd_calibrate(self):
        from binocular_calibration_overlay import BinocularCalibrationOverlay

        def make(finished):
            fmt = lambda v: "n/a" if v is None else f"{v:.1f} px"
            return BinocularCalibrationOverlay(
                self.root, self.controller,
                on_complete=lambda m, chart: finished(f"Calibration complete · Grade {m.get('grade', 'N/A')} · "
                                                      f"mean error {fmt(m.get('mean_error_px'))}"),
                on_cancel=lambda: finished("Calibration cancelled · previous calibration kept", reload=False))
        return self._start_overlay("calibration", make)

    def cmd_finetune(self):
        from finetune_overlay import FineTuneOverlay
        return self._start_overlay("finetune", lambda finished: FineTuneOverlay(self.root, self.controller,
                                                                                on_done=lambda msg: finished(msg)))

    def cmd_recentre(self):
        from binocular_calibration_overlay import RecentreOverlay
        if not self.engine.calibrator.is_calibrated:
            raise ApiError(409, "Calibrate first, then use Quick Re-centre")
        # works on the engine's own calibrator (offset changes live), so no reload afterwards
        return self._start_overlay("recentre", lambda finished: RecentreOverlay(
            self.root, self.controller, self.engine.calibrator, on_done=lambda msg: finished(msg, reload=False)))

    def cmd_chart_open(self):
        if not os.path.exists(CHART_FILE):
            raise ApiError(404, "No calibration chart yet")
        os.startfile(CHART_FILE)
        return {"ok": True}

    def cmd_eyeview_open(self):
        from eye_view_window import BinocularEyeViewWindow
        if self.eye_win is not None and self.eye_win.winfo_exists():
            _to_front(self.eye_win)
            return {"ok": True}

        def closed():
            self.eye_win = None
        self.eye_win = BinocularEyeViewWindow(self.root, self.controller, on_close_callback=closed)
        self.root.after(80, lambda: self.eye_win is not None and _to_front(self.eye_win))
        return {"ok": True}

    def cmd_eyeview_close(self):
        if self.eye_win is not None and self.eye_win.winfo_exists():
            self.eye_win.destroy()
        self.eye_win = None
        return {"ok": True}

    # ------------------------------------------------------------------ shutdown
    def shutdown(self):
        """Tk thread: stop the mouse, close windows, release the cameras, end the main loop."""
        if self.stopping:
            return
        self.stopping = True
        print("[TrackerService] shutting down", flush=True)
        try:
            self.engine.stop()
        except Exception:
            pass
        for w in (self.overlay, self.eye_win):
            try:
                if w is not None and w.winfo_exists():
                    w.destroy()
            except Exception:
                pass
        try:
            self.controller.stop()
        finally:
            self.root.quit()


# =============================================================================
# HTTP
# =============================================================================
def make_handler(svc: TrackerService):
    def post_routes(path: str, b: Dict[str, Any]):
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "eyes" and parts[1] in EYES:
            eye, what = parts[1], parts[2]
            if what == "enabled":
                return svc.call(lambda: svc.cmd_eye_enabled(eye, b.get("enabled", True)))
            if what == "camera":
                return svc.call(lambda: svc.cmd_camera(eye, int(b["camera_id"])))
            if what == "flip":
                return svc.call(lambda: svc.cmd_flip(eye, b.get("h", False), b.get("v", False)))
        simple = {
            "algorithm": lambda: svc.cmd_algorithm(str(b.get("name", "ArcFit"))),
            "pupil_smoothing": lambda: svc.cmd_smoothing(float(b.get("level", 3))),
            "mouse/enabled": lambda: svc.cmd_mouse_enabled(b.get("enabled")),
            "mouse/toggle": lambda: svc.cmd_mouse_enabled(None),
            "mouse/mode": lambda: svc.cmd_mouse_mode(str(b.get("mode", ""))),
            "mouse/params": lambda: svc.cmd_mouse_params(b),
            "mouse/reset": svc.cmd_mouse_reset,
            "mouse/spacebar_pause": lambda: svc.cmd_space_pause(bool(b.get("paused", False))),
            "calibration/start": svc.cmd_calibrate,
            "calibration/finetune": svc.cmd_finetune,
            "calibration/recentre": svc.cmd_recentre,
            "calibration/record": lambda: svc.cmd_record(bool(b.get("enabled", False))),
            "calibration/pointer": lambda: svc.cmd_pointer(bool(b.get("enabled", True))),
            "calibration/auto_recentre": lambda: svc.cmd_auto_recentre(bool(b.get("enabled", False))),
            "calibration/implicit": lambda: svc.cmd_implicit(b.get("dx"), b.get("dy"), b.get("age_ms", 0)),
            "calibration/chart/open": svc.cmd_chart_open,
            "eyeview/open": svc.cmd_eyeview_open,
            "eyeview/close": svc.cmd_eyeview_close,
        }
        key = "/".join(parts)
        if key in simple:
            return svc.call(simple[key])
        raise ApiError(404, f"unknown command /{key}")

    class Handler(BaseHTTPRequestHandler):
        server_version = "EyeTrackerService/1"

        def log_message(self, fmt, *args):          # quiet: the app polls /status several times a second
            pass

        def _send(self, code: int, obj: Any = None, raw: Optional[bytes] = None, ctype: str = "application/json"):
            data = raw if raw is not None else json.dumps(
                obj, default=lambda o: o.item() if hasattr(o, "item") else str(o)).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _fail(self, e: Exception):
            code = e.status if isinstance(e, ApiError) else (400 if isinstance(e, (KeyError, ValueError, TypeError)) else 500)
            self._send(code, {"ok": False, "error": str(e) or e.__class__.__name__})

        def do_GET(self):
            path = self.path.split("?")[0].rstrip("/") or "/"
            try:
                if path == "/health":
                    return self._send(200, {"ok": True, "service": "eye-tracker", "pid": os.getpid()})
                if path == "/status":
                    return self._send(200, svc.status())
                if path == "/calibration/chart":
                    if not os.path.exists(CHART_FILE):
                        raise ApiError(404, "No calibration chart yet")
                    with open(CHART_FILE, "rb") as f:
                        return self._send(200, raw=f.read(), ctype="image/png")
                raise ApiError(404, f"unknown path {path}")
            except Exception as e:
                self._fail(e)

        def do_POST(self):
            path = self.path.split("?")[0].rstrip("/")
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                if not isinstance(body, dict):
                    raise ApiError(400, "JSON object expected")
                if path == "/shutdown":
                    self._send(200, {"ok": True})
                    svc.jobs.put(svc.shutdown)
                    return
                return self._send(200, post_routes(path, body))
            except Exception as e:
                self._fail(e)

    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(description="Headless eye tracker controlled by the AAC app")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--parent-pid", type=int, default=None, help="shut down when this process ends")
    a = ap.parse_args(argv)

    try:                                              # bind first: a second copy must not open the cameras
        server = _Server(("127.0.0.1", a.port), BaseHTTPRequestHandler)
    except OSError:
        print(f"[TrackerService] port {a.port} is in use (tracker already running?)", flush=True)
        return 3

    root = tk.Tk()
    root.withdraw()
    try:
        ctypes.windll.user32.SetProcessDPIAware()     # same as launcher.py: overlays use real screen pixels
    except Exception:
        pass
    svc = TrackerService(root)
    server.RequestHandlerClass = make_handler(svc)
    threading.Thread(target=server.serve_forever, daemon=True, name="TrackerHTTP").start()
    print(f"[TrackerService] ready on http://127.0.0.1:{a.port} (pid {os.getpid()})", flush=True)

    if a.parent_pid:
        def watch():
            while not svc.stopping:
                time.sleep(2.0)
                if not _pid_alive(a.parent_pid):
                    print("[TrackerService] the app has closed", flush=True)
                    svc.jobs.put(svc.shutdown)
                    return
        threading.Thread(target=watch, daemon=True, name="ParentWatch").start()

    try:
        root.mainloop()
    except KeyboardInterrupt:
        svc.shutdown()
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
