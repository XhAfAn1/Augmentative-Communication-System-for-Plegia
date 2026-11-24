#!/usr/bin/env python3
"""
gaze_engine.py
==============
Real-Time Binocular Gaze Engine & System Mouse Controller.

Faithfully implements the exact mouse control architecture from
Eye Tracking Module (gaze_mouse.py / gaze_controller.py):
  * GazeSmoother: Pure exponential moving-average filter on mapped gaze
    coordinates (alpha in [0.01..0.50]), providing buttery, continuous,
    inertial mouse glide with zero artificial deadzones or teleporting.
  * Blink grace period: Freezes cursor position in place for ~240ms
    during spontaneous blinks so tracking remains uninterrupted.
  * Gain & DPI scaling:
        effective_gain = gain * (dpi / 96.0)
        mx = screen_w/2 + (sx - screen_w/2) * effective_gain
    Amplifies eye range so natural eye rotations reach all screen corners.
  * Spacebar-to-Click: Tap Spacebar anywhere to left-click at the cursor.
  * Global Hotkeys: [F5] Toggle Mouse ON/OFF, [F6] Emergency Stop.
  * Physical Fail-Safe: Drag real mouse to top-left (0,0) corner to disengage.
"""

import os
import sys
import time
import math
import ctypes
import threading
from collections import deque
from typing import Optional, Tuple, Dict, Any

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from binocular_calibrator import BinocularCalibrator

CALIB_FILE = os.path.join(_THIS_DIR, "binocular_calibration.json")


# =============================================================================
# GazeSmoother (Identical to Eye Tracking Module / gaze_mouse.py)
# =============================================================================
class GazeSmoother:
    """
    Exponential moving-average gaze smoother with blink grace period.
    Directly ported from Eye Tracking Module (gaze_mouse.py & gaze_controller.py).

    TIME_BASED (2026-11-17): `alpha` is the smoothing per 1/REF_HZ second, and each update uses
    a = 1 - (1 - alpha) ** (dt * REF_HZ). The engine updates on every frame of either eye (~60/s with two
    eyes, ~30/s with one, less with a slow detector); a per-update alpha made the cursor twice as laggy in
    single-eye mode and with slower detectors. REF_HZ = 60 keeps today's two-eye feel for every alpha.
    Set TIME_BASED = False to get the old per-update behaviour.
    """
    TIME_BASED = True
    REF_HZ = 60.0

    def __init__(self, alpha: float = 0.04, grace_s: float = 0.24):
        self.alpha = float(alpha)          # 0 = max smooth / 1 = no smooth
        self.sx: Optional[float] = None
        self.sy: Optional[float] = None
        self.last_good_time: float = 0.0
        self.last_t: Optional[float] = None
        self.grace_s = float(grace_s)      # seconds to hold last position during blinks

    def reset(self):
        self.sx = None
        self.sy = None
        self.last_good_time = 0.0
        self.last_t = None

    def update(self, x: Optional[float], y: Optional[float], now: Optional[float] = None) -> Tuple[Optional[float], Optional[float]]:
        if now is None:
            now = time.time()
        if x is None or y is None:
            if (now - self.last_good_time) < self.grace_s and self.sx is not None and self.sy is not None:
                return self.sx, self.sy
            return None, None
        if self.sx is None or self.sy is None:
            self.sx, self.sy = float(x), float(y)
        else:
            a = self.alpha
            if self.TIME_BASED and self.last_t is not None:
                dt = min(max(now - self.last_t, 0.0), 0.25)
                a = 1.0 - (1.0 - min(self.alpha, 0.999)) ** (dt * self.REF_HZ)
            self.sx = a * float(x) + (1.0 - a) * self.sx
            self.sy = a * float(y) + (1.0 - a) * self.sy
        self.last_t = now
        self.last_good_time = now
        return self.sx, self.sy


# =============================================================================
# 1-Euro Baseline Filter (Casiez, Roussel, Vogel - ACM CHI 2012)
# =============================================================================
class LowPassFilter:
    def __init__(self, alpha: float = 0.5):
        self._alpha = alpha
        self._s: Optional[float] = None

    def filter(self, value: float, alpha: Optional[float] = None) -> float:
        if alpha is not None:
            self._alpha = alpha
        if self._s is None:
            self._s = value
        else:
            self._s = self._alpha * value + (1.0 - self._alpha) * self._s
        return self._s

    def last_value(self) -> Optional[float]:
        return self._s

    def reset(self):
        self._s = None


class OneEuroFilter:
    """Adaptive low-pass filter for comparison benchmarking."""

    def __init__(self, min_cutoff: float = 0.85, beta: float = 0.007, d_cutoff: float = 1.0):
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self.x_filt = LowPassFilter()
        self.dx_filt = LowPassFilter()
        self.t_prev: Optional[float] = None

    @staticmethod
    def _alpha(rate: float, cutoff: float) -> float:
        tau = 1.0 / (2.0 * math.pi * cutoff)
        te = 1.0 / rate if rate > 0 else 0.0
        return 1.0 / (1.0 + tau / te) if (tau + te) > 0 else 1.0

    def reset(self):
        self.x_filt.reset()
        self.dx_filt.reset()
        self.t_prev = None

    def filter(self, x: float, t: Optional[float] = None) -> float:
        if t is None:
            t = time.perf_counter()
        if self.t_prev is None:
            self.t_prev = t
            return self.x_filt.filter(x)

        dt = t - self.t_prev
        self.t_prev = t
        if dt <= 0.0:
            last = self.x_filt.last_value()
            return x if last is None else last

        rate = 1.0 / dt
        prev_x = self.x_filt.last_value()
        dx = (x - prev_x) * rate if prev_x is not None else 0.0
        edx = self.dx_filt.filter(dx, self._alpha(rate, self.d_cutoff))
        cutoff = self.min_cutoff + self.beta * abs(edx)
        return self.x_filt.filter(x, self._alpha(rate, cutoff))


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


# =============================================================================
# Real-Time Binocular Gaze Engine
# =============================================================================
class BinocularGazeEngine:
    """Coordinates binocular gaze mapping, GazeSmoother, and mouse driving."""

    VK_SPACE = 0x20
    VK_F5 = 0x74
    VK_F6 = 0x75
    CORNER_PX = 2

    # auto re-centre while using
    IMPL_WINDOW = 5              # the correction is fitted to the last 5 selections together
    IMPL_REST_S = 0.6            # where the gaze rested: the 0.6 s before the selection
    IMPL_SPREAD = 0.25           # scale is fitted only when the selections span >= 25 % of the screen on that axis
    IMPL_SCALE = (0.85, 1.15)    # scale limits (e.g. sitting ~15 % closer / farther than at calibration)
    IMPL_MAX_SHIFT = 250.0       # px, same limit as Quick Re-centre
    # HIDDEN SWITCH (code only, not in the app / settings): False = learn only a SHIFT (same everywhere on the
    # screen); True = also learn the STRETCH (scale). 2026-11-23: off - the stretch was fitted on the keyboard only
    # (last 5 clicks) and magnified the error on the far right column (sentences, Speak/Clear); vertical scale sat
    # at its 0.85 limit in 48 of 135 updates. Set True and restart the tracker to bring the stretch back.
    IMPL_FIT_SCALE = False
    IMPL_EASE_S = 0.3            # a new correction is eased in (time constant; ~95 % after 0.9 s), no jump
    # little nudge per click (2026-11-23, user: "live fine tune with low impact"): each accepted selection moves the
    # correction only this share of the way towards the fit of the last 5 selections (1.0 = all the way, as before).
    # One wrong click moves it ~4 % of its error at once (1/5 window x 0.2), at most ~13 % while it stays among the
    # last 5, then it fades (Backspace cancels it anyway); a steady slip is ~90 % learned after 10 clicks.
    IMPL_STEP = 0.2
    # ---- LOCAL mode (2026-11-23, user: "like Level 2 fine-tune, low impact, never saved") ----
    # HIDDEN SWITCH: "local" = a temporary per-area correction of the CALIBRATION OUTPUT (map stage, before smoothing,
    # gain and edge reach), learned from the app's button selections; "shift" = the global-shift version above
    # (IMPL_FIT_SCALE / IMPL_STEP / easing apply only to "shift"). Nothing is ever saved: a new calibration,
    # fine-tune, re-centre or tracker restart starts from the plain calibration again.
    # Each screen area (LOCAL_CELL, about one key) keeps ITS OWN last IMPL_WINDOW clicks; per click in an area its
    # correction moves LOCAL_STEP of the way to the average error of those clicks. Around each learned area the
    # correction fades out (LOCAL_SIGMA), so fixing Z/Q does not move Y. Live check: if in an area the error WITH
    # the correction gets bigger than without it (>= 3 clicks), that area is reset and learns again.
    IMPL_MODE = "local"
    LOCAL_CELL = (160.0, 135.0)  # screen px per area (12 x 8 areas on 1920 x 1080)
    LOCAL_STEP = 0.15            # 15 % of the way to the area's last-5 average error per click
    LOCAL_MIN_CLICKS = 2         # an area starts learning at its 2nd click
    LOCAL_MAX_PX = 80.0          # largest correction of one area
    LOCAL_SIGMA = 120.0          # px: full effect on the area, ~60 % one key away, ~0 three keys away
    LOCAL_BG = 0.05              # far from every learned area the correction fades to nothing

    MODE_SMOOTHER = "smoother"
    MODE_FILTERED = "filtered"
    MODE_RAW = "raw"

    def __init__(self, controller):
        self.controller = controller
        self.calibrator = self._load_selected_calibration()

        m_cfg = controller.get_mouse_settings() if hasattr(controller, "get_mouse_settings") else {}
        self.mouse_mode = m_cfg.get("mouse_mode", self.MODE_SMOOTHER)
        self.alpha = float(m_cfg.get("mouse_alpha", 0.15))
        self.gain = float(m_cfg.get("mouse_gain", 1.0))
        self.dpi = float(m_cfg.get("mouse_dpi", 96.0))
        self.grace_ms = int(m_cfg.get("mouse_grace_ms", 240))
        self.spacebar_click = bool(m_cfg.get("mouse_spacebar_click", True))
        self.edge_expansion = bool(m_cfg.get("edge_expansion", True))
        self.edge_margin = float(m_cfg.get("edge_margin", 0.05))
        self.edge_inner = float(m_cfg.get("edge_inner", 0.35))

        # Core EyeTracker Module smoother
        self.smoother = GazeSmoother(alpha=self.alpha, grace_s=self.grace_ms / 1000.0)

        # Baseline 1-Euro filter
        self.filter_x = OneEuroFilter(min_cutoff=0.85, beta=0.007)
        self.filter_y = OneEuroFilter(min_cutoff=0.85, beta=0.007)

        self.mouse_enabled = False
        self.use_filter = (self.mouse_mode != self.MODE_RAW)

        # Auto re-centre while using (fed by the AAC app's button selections, see add_implicit_sample):
        # screen-space correction per axis around the screen centre: x' = cx + sx*(x - cx) + tx (same for y)
        self.impl = [1.0, 1.0, 0.0, 0.0]          # sx, sy, tx, ty  (identity = no correction) - the learned target
        self._impl_shown = [1.0, 1.0, 0.0, 0.0]   # what is applied now: moves towards self.impl (IMPL_EASE_S)
        self._impl_shown_t = None
        self.impl_count = 0
        self._impl_cal = None                     # calibration the correction belongs to (new one -> reset)
        self._impl_samples = deque(maxlen=self.IMPL_WINDOW)
        self._hist = deque(maxlen=600)            # (t, raw_x, raw_y, cursor_x, cursor_y): ~10 s at 60 Hz
        # LOCAL mode state (temporary, never saved; see IMPL_MODE)
        self._areas: Dict[Any, Dict[str, Any]] = {}   # area -> last clicks, correction, live check
        self._local_field: tuple = ()             # (gaze_x, gaze_y, corr_x, corr_y) per learned area, for the worker
        self._ghist = deque(maxlen=600)           # (t, gaze_x, gaze_y) of the PLAIN calibration (before correction)
        self.local_count = 0

        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        eff_gain = self.gain * (self.dpi / 96.0)
        self.telemetry: Dict[str, Any] = {
            "mouse_enabled": False,
            "mouse_mode": self.mouse_mode,
            "cursor_state": "Ready",
            "cursor_pos": None,
            "raw_gaze": None,
            "filtered_gaze": None,
            "left_pupil": None,
            "right_pupil": None,
            "left_conf": 0.0,
            "right_conf": 0.0,
            "mode": "No Signal",
            "fps": 0.0,
            "alpha": self.alpha,
            "gain": self.gain,
            "dpi": self.dpi,
            "grace_ms": self.grace_ms,
            "effective_gain": eff_gain,
            "spacebar_click": self.spacebar_click,
            "edge_expansion": self.edge_expansion,
            "is_calibrated": self.calibrator.is_calibrated,
            "failsafe_tripped": False,
        }

    # ------------------------------------------------------------------ controls
    def _load_selected_calibration(self):
        calibrator = BinocularCalibrator()
        calibrator.load(CALIB_FILE)
        return calibrator

    def set_mouse_enabled(self, enabled: bool):
        with self._lock:
            self.mouse_enabled = bool(enabled)
            self.telemetry["mouse_enabled"] = self.mouse_enabled
            if enabled:
                self.telemetry["failsafe_tripped"] = False
                self.smoother.reset()

    def toggle_mouse(self) -> bool:
        with self._lock:
            self.mouse_enabled = not self.mouse_enabled
            self.telemetry["mouse_enabled"] = self.mouse_enabled
            if self.mouse_enabled:
                self.telemetry["failsafe_tripped"] = False
                self.smoother.reset()
            return self.mouse_enabled

    def set_mouse_mode(self, mode: str):
        with self._lock:
            if mode in (self.MODE_SMOOTHER, self.MODE_FILTERED, self.MODE_RAW):
                self.mouse_mode = mode
                self.use_filter = (mode != self.MODE_RAW)
                self.telemetry["mouse_mode"] = mode
                self.smoother.reset()
                self.filter_x.reset()
                self.filter_y.reset()
                if hasattr(self.controller, "set_mouse_settings"):
                    self.controller.set_mouse_settings(mouse_mode=mode)

    def set_filtering(self, use_filter: bool):
        """Legacy helper: False -> RAW, True -> SMOOTHER."""
        self.set_mouse_mode(self.MODE_SMOOTHER if use_filter else self.MODE_RAW)

    def set_mouse_params(
        self,
        alpha: Optional[float] = None,
        gain: Optional[float] = None,
        dpi: Optional[float] = None,
        grace_ms: Optional[int] = None,
        spacebar_click: Optional[bool] = None,
        edge_expansion: Optional[bool] = None,
    ):
        """Dynamically tunes mouse ergonomics in real time."""
        with self._lock:
            if alpha is not None:
                self.alpha = max(0.005, min(0.90, float(alpha)))
                self.smoother.alpha = self.alpha
            if gain is not None:
                self.gain = max(0.2, min(5.0, float(gain)))
            if dpi is not None:
                self.dpi = max(30.0, min(300.0, float(dpi)))
            if grace_ms is not None:
                self.grace_ms = max(50, min(1000, int(grace_ms)))
                self.smoother.grace_s = self.grace_ms / 1000.0
            if spacebar_click is not None:
                self.spacebar_click = bool(spacebar_click)
            if edge_expansion is not None:
                self.edge_expansion = bool(edge_expansion)

            eff_gain = self.gain * (self.dpi / 96.0)
            self.telemetry.update({
                "alpha": self.alpha,
                "gain": self.gain,
                "dpi": self.dpi,
                "grace_ms": self.grace_ms,
                "effective_gain": eff_gain,
                "spacebar_click": self.spacebar_click,
                "edge_expansion": self.edge_expansion,
            })
            if hasattr(self.controller, "set_mouse_settings"):
                self.controller.set_mouse_settings(
                    mouse_alpha=self.alpha,
                    mouse_gain=self.gain,
                    mouse_dpi=self.dpi,
                    mouse_grace_ms=self.grace_ms,
                    mouse_spacebar_click=self.spacebar_click,
                    mouse_edge_reach=self.edge_expansion,
                )

    def reset_mouse_params(self):
        """Resets to standard 1:1 true calibration mapping defaults."""
        self.set_mouse_params(alpha=0.15, gain=1.0, dpi=96.0, grace_ms=240, spacebar_click=True, edge_expansion=True)

    @staticmethod
    def _apply_edge_reach(x: float, y: float, screen_w: int, screen_h: int,
                          margin: float = 0.05, inner: float = 0.35) -> Tuple[float, float]:
        """
        Smooth C1-continuous boundary expansion.
        Preserves 100% 1:1 calibration precision in the central working zone (|u| <= inner),
        while smoothly accelerating toward the display edges so users can effortlessly reach
        the taskbar, window title bars, and close buttons without eye strain or edge blocking.
        """
        def _expand_1d(val: float, center: float, half_span: float) -> float:
            u = (val - center) / half_span
            abs_u = abs(u)
            if abs_u <= inner:
                return val
            sign = 1.0 if u >= 0 else -1.0
            u_edge = 1.0 - 2.0 * margin
            span = max(1e-4, u_edge - inner)
            t = (abs_u - inner) / span
            k = (1.0 - inner) - span
            v = inner + span * t + k * (t ** 1.6)
            return center + (sign * v) * half_span

        cx = screen_w / 2.0
        cy = screen_h / 2.0
        ex = _expand_1d(x, cx, cx)
        ey = _expand_1d(y, cy, cy)
        return ex, ey

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._worker, daemon=True, name="GazeEngineWorker")
        self._thread.start()

    def stop(self):
        self._running = False
        with self._lock:
            self.mouse_enabled = False
            self.telemetry["mouse_enabled"] = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        self._thread = None

    def get_telemetry(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self.telemetry)

    # ------------------------------------------------------------------ auto re-centre while using
    def reset_implicit(self, cal=None):
        with self._lock:
            self.impl = [1.0, 1.0, 0.0, 0.0]
            self._impl_shown = [1.0, 1.0, 0.0, 0.0]   # new calibration: no correction at once (no easing out)
            self._impl_shown_t = None
            self.impl_count = 0
            self._impl_cal = cal if cal is not None else self.calibrator
            self._impl_samples.clear()
            self._clear_local()

    def _clear_local(self):
        """Forgets the LOCAL correction (caller holds self._lock)."""
        self._areas = {}
        self._local_field = ()
        self.local_count = 0

    def _local_corr(self, gx: float, gy: float, field) -> Tuple[float, float]:
        """LOCAL correction at plain-calibration gaze (gx, gy): learned areas blended by distance, fading to 0."""
        s2 = 2.0 * self.LOCAL_SIGMA ** 2
        wsum, cx, cy = self.LOCAL_BG, 0.0, 0.0
        for ax, ay, ccx, ccy in field:
            k = math.exp(-((gx - ax) ** 2 + (gy - ay) ** 2) / s2)
            wsum += k
            cx += k * ccx
            cy += k * ccy
        return cx / wsum, cy / wsum

    def _to_map_space(self, x: Optional[float], y: Optional[float], cal) -> Tuple[Optional[float], Optional[float]]:
        """Screen point -> the calibration-output point that the cursor pipeline (gain, then edge reach) turns into
        it, so a correction learned at the map stage lands the CURSOR on the button."""
        W, H = float(cal.screen_w), float(cal.screen_h)

        def inv(val, idx, size):
            if val is None:
                return None
            if self.edge_expansion:                                   # edge reach is monotonic: bisection
                lo, hi = -size, 2.0 * size
                for _ in range(50):
                    mid = 0.5 * (lo + hi)
                    p = (mid, H / 2.0) if idx == 0 else (W / 2.0, mid)
                    out = self._apply_edge_reach(p[0], p[1], int(W), int(H),
                                                 margin=self.edge_margin, inner=self.edge_inner)[idx]
                    lo, hi = (mid, hi) if out < val else (lo, mid)
                val = 0.5 * (lo + hi)
            eff = self.gain * (self.dpi / 96.0)
            if abs(eff - 1.0) >= 0.005:
                val = size / 2.0 + (val - size / 2.0) / eff
            return val
        return inv(x, 0, W), inv(y, 1, H)

    def _add_local_sample(self, age_s: float, dx, dy) -> Dict[str, Any]:
        """LOCAL mode: one button selection (see IMPL_MODE)."""
        cal = self.calibrator
        now = time.perf_counter()
        t1 = now - max(0.0, float(age_s))
        with self._lock:
            if cal is not self._impl_cal:
                self.impl, self.impl_count, self._impl_cal = [1.0, 1.0, 0.0, 0.0], 0, cal
                self._impl_shown, self._impl_shown_t = [1.0, 1.0, 0.0, 0.0], None
                self._impl_samples.clear()
                self._clear_local()
            pts = [h for h in self._hist if t1 - self.IMPL_REST_S <= h[0] <= t1]
            gps = [g for g in self._ghist if t1 - self.IMPL_REST_S <= g[0] <= t1]
        if len(pts) < 5 or len(gps) < 5:
            return {"used": False, "why": "no gaze data at that moment"}
        cur = (sum(h[3] for h in pts) / len(pts), sum(h[4] for h in pts) / len(pts))          # where the cursor rested
        g = (sum(p[1] for p in gps) / len(gps), sum(p[2] for p in gps) / len(gps))          # plain calibration gaze
        tgt = (None if dx is None else cur[0] + float(dx), None if dy is None else cur[1] + float(dy))  # button centre
        tm = self._to_map_space(tgt[0], tgt[1], cal)
        r = (None if tm[0] is None else tm[0] - g[0], None if tm[1] is None else tm[1] - g[1])   # error of the plain map
        cell = (int((tgt[0] if tgt[0] is not None else cur[0]) // self.LOCAL_CELL[0]),
                int((tgt[1] if tgt[1] is not None else cur[1]) // self.LOCAL_CELL[1]))
        verdict = "learning"
        with self._lock:
            a = self._areas.setdefault(cell, {"r": deque(maxlen=self.IMPL_WINDOW), "g": deque(maxlen=self.IMPL_WINDOW),
                                              "c": [0.0, 0.0], "chk": deque(maxlen=self.IMPL_WINDOW)})
            # live check entry, measured BEFORE this click changes anything: error without vs with the correction
            now_c = self._local_corr(g[0], g[1], self._local_field)
            e0 = math.sqrt(sum(r[k] ** 2 for k in (0, 1) if r[k] is not None))
            e1 = math.sqrt(sum((r[k] - now_c[k]) ** 2 for k in (0, 1) if r[k] is not None))
            a["chk"].append((e0, e1))
            a["r"].append(r)
            a["g"].append(g)
            chk = list(a["chk"])
            if (len(chk) >= 3 and (a["c"][0] or a["c"][1])
                    and sum(c1 for _, c1 in chk) / len(chk) > sum(c0 for c0, _ in chk) / len(chk) + 2.0):
                a["c"] = [0.0, 0.0]                  # the correction made this area worse: forget it, learn again
                a["r"].clear()
                a["chk"].clear()
                verdict = "reset: correction made this area worse"
            elif len(a["r"]) >= self.LOCAL_MIN_CLICKS:
                for k in (0, 1):
                    vals = [rr[k] for rr in a["r"] if rr[k] is not None]
                    if vals:
                        target = sum(vals) / len(vals)
                        c = a["c"][k] + self.LOCAL_STEP * (target - a["c"][k])
                        a["c"][k] = min(max(c, -self.LOCAL_MAX_PX), self.LOCAL_MAX_PX)
            self.local_count += 1
            active = [(sum(p[0] for p in v["g"]) / len(v["g"]), sum(p[1] for p in v["g"]) / len(v["g"]),
                       v["c"][0], v["c"][1]) for v in self._areas.values() if (v["c"][0] or v["c"][1]) and v["g"]]
            self._local_field = tuple(active)
            c_area = list(a["c"])
        n = len(active)
        mean_c = [sum(f[2] for f in active) / n, sum(f[3] for f in active) / n] if n else [0.0, 0.0]
        return {"used": True, "area": list(cell), "correction": c_area, "areas": n, "check": verdict,
                "shift": mean_c, "scale": [1.0, 1.0], "error_plain": e0, "error_corrected": e1}

    def add_implicit_sample(self, age_s: float, dx, dy) -> Dict[str, Any]:
        if self.IMPL_MODE == "local":
            return self._add_local_sample(age_s, dx, dy)
        return self._add_shift_sample(age_s, dx, dy)

    def _add_shift_sample(self, age_s: float, dx, dy) -> Dict[str, Any]:
        """A button was selected `age_s` seconds ago; dx / dy = (button centre - rested cursor) in screen px
        (None for an axis the button is too long in). Where the cursor rested is taken from this engine's own
        history (cursor before and after the correction), the button centre = rested cursor + (dx, dy). The
        correction (applied to the final cursor) is refitted to the last IMPL_WINDOW selections, per axis: shift
        always, scale too when they are spread."""
        cal = self.calibrator
        now = time.perf_counter()
        t1 = now - max(0.0, float(age_s))
        with self._lock:
            if cal is not self._impl_cal:
                self.impl, self.impl_count, self._impl_cal = [1.0, 1.0, 0.0, 0.0], 0, cal
                self._impl_shown, self._impl_shown_t = [1.0, 1.0, 0.0, 0.0], None
                self._impl_samples.clear()
            pts = [h for h in self._hist if t1 - self.IMPL_REST_S <= h[0] <= t1]
        if len(pts) < 5:
            return {"used": False, "why": "no gaze data at that moment"}
        n = float(len(pts))
        raw = (sum(h[1] for h in pts) / n, sum(h[2] for h in pts) / n)
        cur = (sum(h[3] for h in pts) / n, sum(h[4] for h in pts) / n)
        tgt = (None if dx is None else cur[0] + float(dx), None if dy is None else cur[1] + float(dy))
        with self._lock:
            self._impl_samples.append((raw, tgt))
            samples = list(self._impl_samples)
            new = list(self.impl)
        for ax, size in ((0, cal.screen_w), (1, cal.screen_h)):
            c = size / 2.0
            uv = [(r[ax] - c, t[ax] - c) for r, t in samples if t[ax] is not None]
            if not uv:
                continue
            s_ax = new[ax]
            us = [u for u, _ in uv]
            if not self.IMPL_FIT_SCALE:
                s_ax = 1.0                          # shift only (hidden switch)
            elif len(uv) >= 3 and max(us) - min(us) >= self.IMPL_SPREAD * size:
                mu, mv = sum(us) / len(uv), sum(v for _, v in uv) / len(uv)
                var = sum((u - mu) ** 2 for u in us)
                if var > 1e-6:
                    s_ax = sum((u - mu) * (v - mv) for u, v in uv) / var
                s_ax = min(max(s_ax, self.IMPL_SCALE[0]), self.IMPL_SCALE[1])
            t_ax = sum(v - s_ax * u for u, v in uv) / len(uv)
            # little nudge: only IMPL_STEP of the way from the current correction to the fit
            s_ax = new[ax] + self.IMPL_STEP * (s_ax - new[ax])
            t_ax = new[ax + 2] + self.IMPL_STEP * (t_ax - new[ax + 2])
            new[ax], new[ax + 2] = s_ax, min(max(t_ax, -self.IMPL_MAX_SHIFT), self.IMPL_MAX_SHIFT)
        with self._lock:
            self.impl = new
            self.impl_count += 1
        return {"used": True, "scale": [new[0], new[1]], "shift": [new[2], new[3]]}

    # ------------------------------------------------------------------ helpers
    def _set_cursor(self, user32, x: int, y: int):
        user32.SetCursorPos(int(x), int(y))

    def _cursor_pos(self, user32) -> Optional[Tuple[int, int]]:
        pt = _POINT()
        if user32.GetCursorPos(ctypes.byref(pt)):
            return int(pt.x), int(pt.y)
        return None

    # ------------------------------------------------------------------ worker
    def _worker(self):
        user32 = ctypes.windll.user32

        last_f5 = last_f6 = last_space = False
        last_seq_l = last_seq_r = -1
        last_set_pos: Optional[Tuple[int, int]] = None
        last_signal_t = 0.0
        fps_hist = []
        last_frame_t = time.perf_counter()

        while self._running:
            # 1. Global hotkeys (edge-triggered)
            f5 = bool(user32.GetAsyncKeyState(self.VK_F5) & 0x8000)
            f6 = bool(user32.GetAsyncKeyState(self.VK_F6) & 0x8000)
            if f5 and not last_f5:
                self.toggle_mouse()
            if f6 and not last_f6:
                self.set_mouse_enabled(False)
            last_f5, last_f6 = f5, f6

            # 2. Physical fail-safe: real mouse dragged into top-left corner
            with self._lock:
                is_mouse_on = self.mouse_enabled
                space_click_en = self.spacebar_click
                cur_gain = self.gain
                cur_dpi = self.dpi
                cur_mode = self.mouse_mode
                edge_exp_en = self.edge_expansion
                edge_m = self.edge_margin
                edge_in = self.edge_inner
            if is_mouse_on:
                phys = self._cursor_pos(user32)
                if phys is not None and phys[0] <= self.CORNER_PX and phys[1] <= self.CORNER_PX:
                    placed_there = last_set_pos is not None and last_set_pos[0] <= 8 and last_set_pos[1] <= 8
                    if not placed_there:
                        with self._lock:
                            self.mouse_enabled = False
                            self.telemetry["mouse_enabled"] = False
                            self.telemetry["failsafe_tripped"] = True
                        is_mouse_on = False

            # 3. Wait for a new sample from either camera stream
            smp_l, smp_r = self.controller.get_samples()
            if smp_l.seq == last_seq_l and smp_r.seq == last_seq_r:
                time.sleep(0.003)
                continue
            last_seq_l, last_seq_r = smp_l.seq, smp_r.seq

            now = time.perf_counter()
            dt = now - last_frame_t
            last_frame_t = now
            if dt > 0:
                fps_hist.append(1.0 / dt)
                if len(fps_hist) > 30:
                    fps_hist.pop(0)
            cur_fps = float(sum(fps_hist) / len(fps_hist)) if fps_hist else 0.0

            lp, lc = (smp_l.pupil, smp_l.conf) if smp_l.connected else (None, 0.0)
            rp, rc = (smp_r.pupil, smp_r.conf) if smp_r.connected else (None, 0.0)

            # 4. Gaze prediction
            with self._lock:
                cal = self.calibrator
            raw_pos = filt_pos = target_pos = None
            cur_state = "No Signal" if cal.is_calibrated else "Not Calibrated"
            mode_str = "No Signal" if cal.is_calibrated else "Not Calibrated"

            if cal.is_calibrated:
                pred, mode_str, details = cal.predict(lp, rp, lc, rc)
                raw_fused = details.get("raw_fused")

                if raw_fused is not None:
                    raw_x, raw_y = float(raw_fused[0]), float(raw_fused[1])
                    raw_pos = (int(round(raw_x)), int(round(raw_y)))
                    last_signal_t = now
                elif pred is not None:
                    raw_x, raw_y = float(pred[0]), float(pred[1])
                    raw_pos = pred
                    last_signal_t = now
                else:
                    raw_x, raw_y = None, None
                    raw_pos = None

                # 4b. LOCAL auto re-centre: temporary per-area correction of the calibration output (IMPL_MODE)
                if raw_x is not None and self.IMPL_MODE == "local":
                    self._ghist.append((now, raw_x, raw_y))        # plain calibration gaze, for learning
                    field = self._local_field
                    if field and cal is self._impl_cal:
                        lcx, lcy = self._local_corr(raw_x, raw_y, field)
                        raw_x, raw_y = raw_x + lcx, raw_y + lcy
                        raw_pos = (int(round(raw_x)), int(round(raw_y)))

                # 5. Cursor Driving Pipeline
                if cur_mode == self.MODE_SMOOTHER:
                    sx, sy = self.smoother.update(raw_x, raw_y, now=now)
                    if sx is not None and sy is not None:
                        eff_gain = cur_gain * (cur_dpi / 96.0)
                        if abs(eff_gain - 1.0) < 0.005:
                            # 1:1 True Calibration Mapping (Zero displacement distortion)
                            mx = float(sx)
                            my = float(sy)
                        else:
                            cx_scr = cal.screen_w / 2.0
                            cy_scr = cal.screen_h / 2.0
                            mx = cx_scr + (sx - cx_scr) * eff_gain
                            my = cy_scr + (sy - cy_scr) * eff_gain

                        if edge_exp_en:
                            mx, my = self._apply_edge_reach(mx, my, cal.screen_w, cal.screen_h,
                                                            margin=edge_m, inner=edge_in)

                        target_x = max(0, min(cal.screen_w - 1, int(round(mx))))
                        target_y = max(0, min(cal.screen_h - 1, int(round(my))))
                        target_pos = (target_x, target_y)
                        filt_pos = target_pos
                        cur_state = "Blink Grace Hold" if (raw_x is None) else "Tracking (1:1 + Edge Reach)"
                    else:
                        target_pos = None
                        filt_pos = None
                        cur_state = "No Signal"

                elif cur_mode == self.MODE_FILTERED:
                    # 1-Euro Filter
                    if raw_pos is not None:
                        if now - last_signal_t > 0.35:
                            self.filter_x.reset()
                            self.filter_y.reset()
                        fx = self.filter_x.filter(float(raw_pos[0]), now)
                        fy = self.filter_y.filter(float(raw_pos[1]), now)
                        eff_gain = cur_gain * (cur_dpi / 96.0)
                        if abs(eff_gain - 1.0) >= 0.005:
                            cx_scr = cal.screen_w / 2.0
                            cy_scr = cal.screen_h / 2.0
                            fx = cx_scr + (fx - cx_scr) * eff_gain
                            fy = cy_scr + (fy - cy_scr) * eff_gain
                        if edge_exp_en:
                            fx, fy = self._apply_edge_reach(fx, fy, cal.screen_w, cal.screen_h,
                                                            margin=edge_m, inner=edge_in)
                        target_pos = (max(0, min(cal.screen_w - 1, int(round(fx)))),
                                      max(0, min(cal.screen_h - 1, int(round(fy)))))
                        filt_pos = target_pos
                        cur_state = "1-Euro Filtered"
                    else:
                        target_pos = None
                        filt_pos = None
                        cur_state = "No Signal"

                else:
                    # RAW Direct
                    if raw_pos is not None:
                        rx, ry = float(raw_pos[0]), float(raw_pos[1])
                        eff_gain = cur_gain * (cur_dpi / 96.0)
                        if abs(eff_gain - 1.0) >= 0.005:
                            cx_scr = cal.screen_w / 2.0
                            cy_scr = cal.screen_h / 2.0
                            rx = cx_scr + (rx - cx_scr) * eff_gain
                            ry = cy_scr + (ry - cy_scr) * eff_gain
                        if edge_exp_en:
                            rx, ry = self._apply_edge_reach(rx, ry, cal.screen_w, cal.screen_h,
                                                            margin=edge_m, inner=edge_in)
                        target_pos = (max(0, min(cal.screen_w - 1, int(round(rx)))),
                                      max(0, min(cal.screen_h - 1, int(round(ry)))))
                    else:
                        target_pos = None
                    filt_pos = target_pos
                    cur_state = "Raw Direct" if raw_pos is not None else "No Signal"

            # 5b. Auto re-centre correction on the FINAL cursor position (after smoothing, gain and edge reach),
            #     identity unless the app taught it; history = (cursor before, cursor after) for the next fit
            if cal.is_calibrated and target_pos is not None:
                if cal is not self._impl_cal:
                    self.reset_implicit(cal)
                # ease the applied correction towards the learned one (the fit uses the uncorrected gaze, so
                # easing only changes how the cursor moves, not what is learned)
                tgt_impl = self.impl
                shown = self._impl_shown
                if self._impl_shown_t is None:
                    a = 1.0
                else:
                    a = 1.0 - math.exp(-max(0.0, now - self._impl_shown_t) / self.IMPL_EASE_S)
                self._impl_shown_t = now
                shown = [s + (t - s) * a for s, t in zip(shown, tgt_impl)]
                self._impl_shown = shown
                isx, isy, itx, ity = shown
                ccx, ccy = cal.screen_w / 2.0, cal.screen_h / 2.0
                bx, by = float(target_pos[0]), float(target_pos[1])
                ax_ = ccx + isx * (bx - ccx) + itx
                ay_ = ccy + isy * (by - ccy) + ity
                target_pos = (max(0, min(cal.screen_w - 1, int(round(ax_)))), max(0, min(cal.screen_h - 1, int(round(ay_)))))
                filt_pos = target_pos
                with self._lock:
                    self._hist.append((now, bx, by, ax_, ay_))

            # 6. Drive System Cursor
            if is_mouse_on and target_pos is not None:
                self._set_cursor(user32, target_pos[0], target_pos[1])
                last_set_pos = target_pos

            # 7. Spacebar Click (Exact match to Eye Tracking Module)
            if space_click_en and is_mouse_on and target_pos is not None:
                space_down = bool(user32.GetAsyncKeyState(self.VK_SPACE) & 0x8000)
                if space_down and not last_space:
                    user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
                    time.sleep(0.03)
                    user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP
                last_space = space_down
            else:
                last_space = bool(user32.GetAsyncKeyState(self.VK_SPACE) & 0x8000)

            with self._lock:
                self.telemetry.update({
                    "raw_gaze": raw_pos,
                    "filtered_gaze": filt_pos,
                    "cursor_pos": target_pos,
                    "cursor_state": cur_state,
                    "left_pupil": lp,
                    "right_pupil": rp,
                    "left_conf": lc,
                    "right_conf": rc,
                    "mode": mode_str,
                    "fps": cur_fps,
                })
