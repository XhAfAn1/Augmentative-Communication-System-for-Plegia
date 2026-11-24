#!/usr/bin/env python3
"""
position_guide_overlay.py
=========================
Position Guide (2026-11-22): a manual aid to get back to the head position of the calibration after moving away.

Shows 5 fixed dots (centre + 4 edge middles) and the live gaze pointer, nothing else. The user looks at the dots and
moves the head until the pointer sits on them; no measuring, no instructions (user's choice).

READ ONLY: it only calls calibrator.predict(). It never changes the calibrator, never saves a file and never touches
the settings. Closes with [ESC] / [Enter] without any message.
"""

import time
import tkinter as tk
from typing import Callable, Optional

from binocular_calibration_overlay import GazePointer, use_palette, draw_gaze_pointer


class PositionGuideOverlay(tk.Toplevel):
    POINTS = [(0.5, 0.5), (0.12, 0.5), (0.88, 0.5), (0.5, 0.12), (0.5, 0.88)]

    def __init__(self, master, controller, calibrator, on_done: Optional[Callable[[str], None]] = None):
        super().__init__(master)
        self.controller, self.cal, self.on_done = controller, calibrator, on_done
        self.pal = use_palette(controller)
        self.attributes("-fullscreen", True)
        self.attributes("-topmost", True)
        self.configure(bg=self.pal["BG_COLOR"])
        self.sw, self.sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.canvas = tk.Canvas(self, width=self.sw, height=self.sh, bg=self.pal["BG_COLOR"],
                                highlightthickness=0, cursor="none")
        self.canvas.pack(fill="both", expand=True)
        self.bind("<Escape>", lambda e: self._finish())
        self.bind("<Return>", lambda e: self._finish())
        self.focus_force()
        self.seq = (-1, -1)
        self.pointer = GazePointer(controller, self.sw, self.sh)
        self._closed = False
        self._tick()

    def _tick(self):
        if self._closed or not self.winfo_exists():
            return
        sl, sr = self.controller.get_samples()
        if (sl.seq, sr.seq) != self.seq:
            self.seq = (sl.seq, sr.seq)
            lp = sl.pupil if sl.connected and sl.conf > 0.15 else None
            rp = sr.pupil if sr.connected and sr.conf > 0.15 else None
            pred, _, _ = self.cal.predict(lp, rp, sl.conf if lp else 0.0, sr.conf if rp else 0.0)
            self.pointer.update(pred)
        p, c = self.pal, self.canvas
        c.delete("all")
        c.create_text(self.sw // 2, 40, text="Position Guide  ·  [ESC] close", font=("Segoe UI", 15, "bold"),
                      fill=p["CYAN"])
        for fx, fy in self.POINTS:
            tx, ty = int(fx * self.sw), int(fy * self.sh)
            c.create_oval(tx - 14, ty - 14, tx + 14, ty + 14, outline=p["TEXT_MUTED"], width=2)
            c.create_oval(tx - 5, ty - 5, tx + 5, ty + 5, fill=p["TEXT_WHITE"], outline="")
        # the gaze pointer is the whole point of this screen, so it is shown even if "Show gaze pointer" is off
        if self.pointer.pos is not None and time.perf_counter() - self.pointer._last_t <= 0.35:
            draw_gaze_pointer(c, self.pointer.pos, self.pointer.trail, big=True)
        self.after(16, self._tick)

    def _finish(self):
        if self._closed:
            return
        self._closed = True
        if callable(self.on_done):
            self.on_done("")                                  # empty = no message in the app
        self.destroy()
