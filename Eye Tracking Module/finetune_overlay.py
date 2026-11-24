#!/usr/bin/env python3
"""
finetune_overlay.py - Level 2 (rebuilt 2026-11-25 to the user's design). It does NOT refit the calibration map: it
measures how the map misses at the places that matter in the AAC app and stores a correction on top of the map.

  1. Centre dot: the user moves the head until the pointer sits on the dot (back to the calibration head position),
     then confirms. The live gaze pointer follows the "Show gaze pointer" switch on ALL points (user, 2026-11-25).
  2. 12 points at the app's real buttons (positions sent by the app when Level 2 starts): Q, P, Z, ".", Space, G,
     Speak, Clear, first + last word box of the top prediction row, first + last visible sentence. Without app
     positions (launcher): 12 points spread over the screen.
  3. Confirm per point: Space (or an EMG squeeze, which presses Space). The reading = median pupil of the 0.5 s
     before + 0.7 s after the press; needs >= 8 frames and a steady pupil (<= 3 camera px) per eye in use, else
     "press again" (3 tries, then it is taken anyway).
  4. After all points: a point with no reading or far from what the other points say (> RETAKE_PX) gets one more look.
  5. Correction = BinocularCalibrator.fit_level2: shift, or shift + stretch only when it predicts the points better
     (each point judged without itself). It replaces the previous Level 2 / Re-centre correction and the end shift.
     Saved to binocular_calibration.json + data/calibrations/<time>_level2.json (all readings, for evaluation).
ESC cancels: nothing is saved. The old version (9 random dots + careful map refit) is in
research/before_level2_app_points_20261125/.
"""
import os
import sys
import json
import time
import ctypes
import tkinter as tk
from typing import Optional, Callable, Dict, Any, List, Tuple
import numpy as np

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from binocular_calibrator import BinocularCalibrator
from binocular_calibration_overlay import GazePointer, use_palette, pointer_enabled

BG, CYAN, GOLD, GREEN, RED, WHITE, MUTED = "#080c14", "#00bcd4", "#fb8c00", "#43a047", "#e53935", "#ffffff", "#8b9bb4"
CALIB_FILE = os.path.join(_THIS_DIR, "binocular_calibration.json")
APP_WINDOW_TITLE = "AAC System"          # frontend/windows/runner/main.cpp
Point = Tuple[float, float]

try:
    _user32 = ctypes.windll.user32
except AttributeError:                   # not Windows: Space comes from the Tk key binding
    _user32 = None


def _apply_palette(controller):
    global BG, CYAN, GOLD, GREEN, RED, WHITE, MUTED
    p = use_palette(controller)
    BG, CYAN, GOLD, GREEN, RED = p["BG_COLOR"], p["CYAN"], p["GOLD"], p["GREEN"], p["RED"]
    WHITE, MUTED = p["TEXT_WHITE"], p["TEXT_MUTED"]


def _app_origin() -> Tuple[int, int]:
    """Screen position of the app window's client area (the app sends button centres relative to it)."""
    if _user32 is None:
        return 0, 0
    try:
        hwnd = _user32.FindWindowW(None, APP_WINDOW_TITLE)
        if not hwnd or _user32.IsIconic(hwnd):
            return 0, 0

        class _PT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = _PT(0, 0)
        if _user32.ClientToScreen(hwnd, ctypes.byref(pt)):
            return int(pt.x), int(pt.y)
    except Exception:
        pass
    return 0, 0


class FineTuneOverlay(tk.Toplevel):
    APPROACH_S = 0.8                 # the dot appears with a shrinking ring, then waits for the confirm
    PRE_S, POST_S = 0.5, 0.7         # readings used around the confirm press
    FLASH_S = 0.35
    MIN_SAMPLES = 8
    MAX_JITTER_CAM_PX = 3.0
    MAX_TRIES = 3
    RETAKE_PX = 80.0                 # after all points: one more look when a point is this far from the others' fit
    MAX_SHIFT_PX = 250.0             # same limit as Quick Re-centre
    MIN_APP_POINTS = 6               # fewer app positions than this -> the spread-out points

    def __init__(self, master, controller, on_done: Optional[Callable[[str], None]] = None,
                 targets: Optional[List[Dict[str, Any]]] = None):
        super().__init__(master)
        self.controller, self.on_done = controller, on_done
        _apply_palette(controller)
        self.attributes("-fullscreen", True)
        self.attributes("-topmost", True)
        self.configure(bg=BG)
        self.sw, self.sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.cv = tk.Canvas(self, width=self.sw, height=self.sh, bg=BG, highlightthickness=0, cursor="none")
        self.cv.pack(fill="both", expand=True)
        self.bind("<Escape>", lambda e: self._end("Level 2 cancelled"))
        self._tk_space = False
        self.bind("<KeyPress-space>", lambda e: setattr(self, "_tk_space", True))
        self.bind("<KeyRelease-space>", lambda e: setattr(self, "_tk_space", False))
        self.focus_force()
        self._active = True

        self.cal = BinocularCalibrator()
        if not self.cal.load(CALIB_FILE):
            self.after(10, lambda: self._end("Level 2 needs a calibration first"))
            return
        self.points, self.source = self._make_points(targets)
        self.read: List[Optional[Tuple[Optional[Point], Optional[Point]]]] = [None] * len(self.points)
        self.quality: List[Dict[str, Any]] = [{} for _ in self.points]
        self.queue = list(range(len(self.points)))
        self.rechecked = False
        self.stage, self.phase, self.t0 = "DOTS", "APPROACH", time.time()
        self.tries = 0
        self.press_t = 0.0
        self.msg = ""
        self.hist: List[Tuple[float, str, Point]] = []     # (time, eye, pupil) of the last few seconds
        self.seq = (-1, -1)
        self._was_down = True                              # a confirm needs a fresh press
        self.pointer = GazePointer(controller, self.sw, self.sh)
        self._tick()

    # ------------------------------------------------------------------ points
    def _make_points(self, targets) -> Tuple[List[Tuple[str, Tuple[int, int]]], str]:
        pts = [("Centre", (self.sw // 2, self.sh // 2))]
        ox, oy = _app_origin()
        app = []
        for t in targets or []:
            try:
                x, y = int(round(ox + float(t["x"]))), int(round(oy + float(t["y"])))
            except (KeyError, TypeError, ValueError):
                continue
            if 10 <= x < self.sw - 10 and 10 <= y < self.sh - 10:
                app.append((str(t.get("name", "Point")), (x, y)))
        if len(app) >= self.MIN_APP_POINTS:
            return pts + app, "app"
        spread = [(fx, fy) for fy in (0.2, 0.5, 0.8) for fx in (0.12, 0.37, 0.63, 0.88)]
        return pts + [(f"Point {i + 1}", (int(fx * self.sw), int(fy * self.sh))) for i, (fx, fy) in enumerate(spread)], \
            "spread"

    # ------------------------------------------------------------------ input
    def _space_down(self) -> bool:
        if _user32 is not None:
            return bool(_user32.GetAsyncKeyState(0x20) & 0x8000)
        return self._tk_space

    def _eyes_on(self):
        return [e for e in ("left", "right") if self.controller.is_eye_enabled(e)] \
            if hasattr(self.controller, "is_eye_enabled") else ["left", "right"]

    def _read_eyes(self, t: float):
        sl, sr = self.controller.get_samples()
        lp = sl.pupil if sl.connected and sl.pupil is not None and sl.conf > 0.15 else None
        rp = sr.pupil if sr.connected and sr.pupil is not None and sr.conf > 0.15 else None
        if sl.seq != self.seq[0] and lp is not None:       # each camera frame stored once
            self.hist.append((t, "left", (float(lp[0]), float(lp[1]))))
        if sr.seq != self.seq[1] and rp is not None:
            self.hist.append((t, "right", (float(rp[0]), float(rp[1]))))
        new = (sl.seq, sr.seq) != self.seq
        self.seq = (sl.seq, sr.seq)
        while self.hist and self.hist[0][0] < t - 3.0:
            self.hist.pop(0)
        return new, lp, (sl.conf if lp else 0.0), rp, (sr.conf if rp else 0.0)

    # ------------------------------------------------------------------ loop
    def _tick(self):
        if not self._active:
            return
        t = time.time()
        el = t - self.t0
        new, lp, lc, rp, rc = self._read_eyes(t)
        i = self.queue[0] if self.queue else None
        if new and pointer_enabled(self.controller):       # "Show gaze pointer" switch decides (display only)
            self.pointer.update(self.cal.predict(lp, rp, lc, rc)[0])
        down = self._space_down()
        pressed = down and not self._was_down
        self._was_down = down

        if self.phase == "APPROACH" and el >= self.APPROACH_S:
            self.phase, self.t0 = "WAIT", t
        elif self.phase == "WAIT" and pressed:
            self.phase, self.t0, self.press_t, self.msg = "COLLECT", t, t, ""
        elif self.phase == "COLLECT" and el >= self.POST_S:
            self._take_reading(i, t)
        elif self.phase == "FLASH" and el >= self.FLASH_S:
            self.queue.pop(0)
            if self.queue:
                self.phase, self.t0, self.tries = "APPROACH", t, 0
            else:
                self._render(0.0)
                return self.after(30, self._after_points)
        self._render(el)
        self.after(16, self._tick)

    def _take_reading(self, i: int, t: float):
        lo, hi = self.press_t - self.PRE_S, self.press_t + self.POST_S
        med: Dict[str, Optional[Point]] = {"left": None, "right": None}
        q: Dict[str, Any] = {}
        steady = True
        for eye in self._eyes_on():
            buf = [p for (ts, e, p) in self.hist if e == eye and lo <= ts <= hi]
            if len(buf) < self.MIN_SAMPLES:
                q[eye] = {"frames": len(buf)}
                steady = False
                continue
            a = BinocularCalibrator._tukey_filter(np.array(buf, float))
            m = np.median(a, axis=0)
            jit = float(np.mean(np.hypot(*(a - m).T)))
            med[eye] = (float(m[0]), float(m[1]))
            q[eye] = {"frames": len(buf), "jitter_cam_px": round(jit, 2)}
            steady = steady and jit <= self.MAX_JITTER_CAM_PX
        self.tries += 1
        if not steady and self.tries < self.MAX_TRIES:
            self.phase, self.t0 = "WAIT", t
            self.msg = f"Not steady - keep looking at the dot and press again ({self.tries}/{self.MAX_TRIES - 1})"
            return
        self.read[i] = (med["left"], med["right"]) if (med["left"] or med["right"]) else None
        self.quality[i] = {**q, "tries": self.tries, "steady": steady}
        self.phase, self.t0, self.msg = "FLASH", t, ""

    # ------------------------------------------------------------------ analysis
    def _gaze_lists(self):
        """-> indices, map gaze G (the map alone), current cursor C (with the current correction), targets T."""
        idx, G, C, T = [], [], [], []
        for i, r in enumerate(self.read):
            if r is None:
                continue
            l, rr = r
            _, _, d = self.cal.predict(l, rr, 1.0 if l else 0.0, 1.0 if rr else 0.0)
            if d.get("map_fused") is None:
                continue
            idx.append(i)
            G.append(d["map_fused"])
            C.append(d["raw_fused"])
            T.append(self.points[i][1])
        return idx, np.array(G, float).reshape(-1, 2), np.array(C, float).reshape(-1, 2), np.array(T, float).reshape(-1, 2)

    @staticmethod
    def _outliers(idx, G, T, limit: float, max_n: int = 3) -> List[int]:
        """Points that disagree with the others, worst first: the worst point (judged by a correction fitted without
        it) is taken out while it is further than `limit`, so one wrong reading cannot make its neighbours look
        wrong too. At most max_n points, and at least 6 stay."""
        keep, out = list(range(len(idx))), []
        while len(out) < max_n and len(keep) > 6:
            fit = BinocularCalibrator.fit_level2(G[keep], T[keep])
            if fit is None:
                break
            k = int(np.argmax(fit["loo"]))
            if fit["loo"][k] <= limit:
                break
            out.append(idx[keep[k]])
            keep.pop(k)
        return out

    def _after_points(self):
        if not self._active:
            return
        idx, G, _, T = self._gaze_lists()
        if not self.rechecked:
            self.rechecked = True
            again = [i for i in range(len(self.points)) if self.read[i] is None]
            if len(idx) >= 3:
                again += self._outliers(idx, G, T, self.RETAKE_PX)
            if again:
                print(f"[Level 2] one more look: " + ", ".join(self.points[i][0] for i in sorted(set(again))))
                self.queue = sorted(set(again))
                self.stage, self.phase, self.t0, self.tries = "RECHECK", "APPROACH", time.time(), 0
                return self._tick()
        self._finish()

    def _finish(self):
        idx, G, C, T = self._gaze_lists()
        # a reading that is still far off after its second look (> 2 x RETAKE_PX from what the others say) is a
        # detector error: left out of the correction (logged and kept in the level2 json)
        drop = self._outliers(idx, G, T, 2 * self.RETAKE_PX) if len(idx) >= 3 else []
        if drop:
            print("[Level 2] left out (reading far from all other points): " + ", ".join(self.points[i][0] for i in drop))
            keep = [k for k, i in enumerate(idx) if i not in drop]
            idx, G, C, T = [idx[k] for k in keep], G[keep], C[keep], T[keep]
        self.dropped = drop
        fit = BinocularCalibrator.fit_level2(G, T) if len(idx) >= 3 else None
        if fit is None:
            return self._end("Level 2 failed: too little pupil signal")
        c = np.array([self.sw / 2, self.sh / 2])
        move = float(np.hypot(*(np.r_[1.0, c] @ fit["A"] - c)))
        if move > self.MAX_SHIFT_PX:
            return self._end(f"Level 2: shift too large ({move:.0f} px) - please recalibrate")
        now_px = float(np.mean(np.hypot(*(C - T).T)))
        self.cal.affine = np.asarray(fit["A"], float)
        self.cal.offset = (0.0, 0.0)                     # Level 2 measured the whole correction: it replaces these
        self.cal.end_shift = None
        info = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "points_source": self.source, "kind": fit["kind"],
                "points_used": len(idx), "left_out": [self.points[i][0] for i in getattr(self, "dropped", [])],
                "cursor_before_px": round(now_px, 1),
                "map_only_px": round(fit["before_px"], 1), "after_px": round(fit["after_px"], 1),
                "shift_only_px": round(fit["shift_loo_px"], 1),
                "shift_stretch_px": None if fit["stretch_loo_px"] is None else round(fit["stretch_loo_px"], 1),
                "A": np.round(fit["A"], 5).tolist()}
        self.cal.metrics["level2"] = info
        try:
            self.cal.save(CALIB_FILE)
            folder = os.path.join(_THIS_DIR, "data", "calibrations")
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, time.strftime("%Y%m%d_%H%M%S") + "_level2.json"), "w") as f:
                json.dump({**info, "points": [{"name": n, "target": list(p), "reading": self.read[i],
                                               "quality": self.quality[i]}
                                              for i, (n, p) in enumerate(self.points)]}, f, indent=1)
        except Exception as e:
            print(f"[Level 2] save failed: {e}")
        self._end(f"Level 2 done ({fit['kind']}): points {now_px:.0f} -> {fit['after_px']:.0f} px "
                  f"(each point checked without itself)")

    def _end(self, msg: str):
        if not self._active:
            return
        self._active = False
        print(f"[Level 2] {msg}")
        if callable(self.on_done):
            self.on_done(msg)
        self.destroy()

    # ------------------------------------------------------------------ drawing
    def _render(self, el: float):
        c = self.cv
        c.delete("all")
        if not self.queue:
            c.create_text(self.sw // 2, self.sh // 2, text="Working out the correction ...",
                          font=("Segoe UI", 16, "bold"), fill=CYAN)
            return
        i = self.queue[0]
        name, (tx, ty) = self.points[i]
        done = sum(1 for r in self.read if r is not None)
        head = f"Level 2  ·  point {min(done + 1, len(self.points))} of {len(self.points)}" \
            if self.stage == "DOTS" else "Level 2  ·  one more look"
        c.create_text(self.sw // 2, 40, text=head + "  ·  [ESC] cancel", font=("Segoe UI", 15, "bold"), fill=CYAN)
        hint = ("Move your head until the pointer sits on the dot, then press Space"
                if i == 0 and pointer_enabled(self.controller) else "Look at the dot, then press Space")
        c.create_text(self.sw // 2, 68, text=hint, font=("Segoe UI", 11), fill=MUTED)
        if self.phase == "APPROACH":
            r = int(34 - 22 * min(1.0, el / self.APPROACH_S))
            c.create_oval(tx - r, ty - r, tx + r, ty + r, outline=CYAN, width=2)
        elif self.phase == "WAIT":
            c.create_oval(tx - 14, ty - 14, tx + 14, ty + 14, outline=CYAN, width=2)
        elif self.phase == "COLLECT":
            ext = int(360 * min(1.0, el / self.POST_S))
            c.create_oval(tx - 18, ty - 18, tx + 18, ty + 18, outline=MUTED, width=2)
            if ext > 0:
                c.create_arc(tx - 18, ty - 18, tx + 18, ty + 18, start=90, extent=-ext, outline=CYAN, width=3,
                             style="arc")
        elif self.phase == "FLASH":
            c.create_oval(tx - 18, ty - 18, tx + 18, ty + 18, outline=GREEN, width=3)
        col = GREEN if self.phase == "FLASH" else WHITE
        c.create_oval(tx - 5, ty - 5, tx + 5, ty + 5, fill=col, outline=CYAN if col == WHITE else GREEN, width=1)
        ly = ty + 34 if ty < self.sh - 80 else ty - 34
        c.create_text(tx, ly, text=name, font=("Segoe UI", 9), fill=MUTED)
        if self.msg:
            c.create_text(self.sw // 2, self.sh - 60, text=self.msg, font=("Segoe UI", 12, "bold"), fill=GOLD)
        self.pointer.draw(c, big=False)                    # only when "Show gaze pointer" is on
