#!/usr/bin/env python3
"""
eye_view_window.py
==================
Side-by-side Eye View window showing both cameras with the live pupil
detection drawn on top.

  * Displays the frame and pupil ellipse from the controller's shared
    EyeSamples (the window itself never runs a detector).
  * Independent elliptical eyeball boundary cage per eye (X/Y, base radius,
    horizontal/vertical squeeze) with sliders, step buttons, click & drag,
    mouse wheel and keyboard shortcuts.
  * Camera selection with mutual exclusion, in-memory flips, algorithm picker
    kept in sync with the launcher.
  * Persistent storage in binocular_settings.json (via the controller).
"""

import os
import sys
import re
import tkinter as tk
from typing import Tuple
import cv2
import numpy as np
from PIL import Image, ImageTk

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from data.pupil_algorithm import ALGORITHM_COLORS, ALGORITHM_DISPLAY, to_display_name, to_canonical_name, FRAME_W, FRAME_H
from binocular_controller import SQUEEZE_MIN, SQUEEZE_MAX
from ui_round import Card, RoundButton, Slider, Dropdown, labeled_switch

# Light style of the AAC app's menus (frontend lib/core/theme/app_colors.dart + core/widgets/ui_kit.dart)
BG_MAIN    = "#ecf2fb"   # app background
BG_SURFACE = "#ffffff"   # cards / tabs
BG_CARD2   = "#f7fafd"   # small cards inside a tab
BORDER_COL = "#e1e8f2"   # card borders
TROUGH     = "#c9d6e8"   # slider tracks
BTN_BG     = "#e8eff8"   # flat buttons
BTN_HOVER  = "#d6e3f3"
SELECTED_BG = "#dbe8fb"  # selected eye (tinted app blue)
CYAN       = "#1565c0"   # main accent (app primary blue): values, selected tab
LEFT_ACCENT  = "#00838f" # left eye  (dark cyan = the cyan cage in the left camera image)
RIGHT_ACCENT = "#b26a00" # right eye (dark amber = the amber cage in the right camera image)
GOLD       = "#e65100"   # warnings
GREEN      = "#2e7d32"
RED        = "#c62828"
TEXT_MAIN  = "#1a2e52"   # app navy
TEXT_MUTED = "#5f6b7a"
IMAGE_BG   = "#1a2e52"   # behind the camera images
# camera-image header strip (BGR): app keyboard navy
BGR_HDR, BGR_HDR_LINE, BGR_CARD = (82, 46, 26), (110, 59, 36), (64, 36, 20)

FONT_HDR   = ("Segoe UI", 9, "bold")
FONT_BODY  = ("Segoe UI", 9)
FONT_SM    = ("Segoe UI", 8)
FONT_XS    = ("Segoe UI", 7)
FONT_UI    = ("Segoe UI", 10)
FONT_UI_B  = ("Segoe UI", 10, "bold")
FONT_BTN   = ("Segoe UI", 9, "bold")

AVAILABLE_CAMERAS = [0, 1, 2]

BGR_CYAN = (255, 229, 0)
BGR_GOLD = (65, 179, 227)

VIEW_W, VIEW_H = 400, 300
SCALE = VIEW_W / float(FRAME_W)   # 0.625

X_MIN, X_MAX = 20, FRAME_W - 20
Y_MIN, Y_MAX = 20, FRAME_H - 20
R_MIN, R_MAX = 25, 220


def draw_crosshair(img: np.ndarray, cx: float, cy: float, color: Tuple[int, int, int], size: int = 8):
    icx, icy = int(round(cx)), int(round(cy))
    cv2.circle(img, (icx, icy), 2, color, -1, cv2.LINE_AA)
    cv2.circle(img, (icx, icy), 6, color, 1, cv2.LINE_AA)
    cv2.line(img, (icx - size, icy), (icx - 3, icy), color, 1, cv2.LINE_AA)
    cv2.line(img, (icx + 3, icy), (icx + size, icy), color, 1, cv2.LINE_AA)
    cv2.line(img, (icx, icy - size), (icx, icy - 3), color, 1, cv2.LINE_AA)
    cv2.line(img, (icx, icy + 3), (icx, icy + size), color, 1, cv2.LINE_AA)


class BinocularEyeViewWindow(tk.Toplevel):
    FRAME_MS = 33  # cameras deliver 30 FPS; polling faster only redraws identical frames

    def __init__(self, master, controller, on_close_callback=None):
        super().__init__(master)
        self.controller = controller
        self.on_close_callback = on_close_callback

        self.title("Eye View — Binocular Cameras, Eyeball Boundary & Real-Time Pupil Tracking")
        # resizable: the two camera images grow/shrink with the window (4:3), the controls keep their size
        self.resizable(True, True)
        self.view_w, self.view_h = VIEW_W, VIEW_H
        self.configure(bg=BG_MAIN)

        self._active = True
        self._left_photo = None
        self._right_photo = None
        self._last_seq = {"left": -1, "right": -1}
        self._last_img = {"left": None, "right": None}

        self.left_combo = None
        self.right_combo = None
        self.eye_on = {e: tk.BooleanVar(value=self.controller.is_eye_enabled(e)) for e in ("left", "right")}

        self.active_alg = self.controller.get_pupil_algorithm()
        self.alg_display_var = tk.StringVar(value=to_display_name(self.active_alg))
        self.controller.add_algorithm_listener(self._on_algorithm_changed)

        cfg = self.controller.cfg
        self.left_cam_var = tk.StringVar(value=f"Camera {cfg.get('camera_left_id', 0)}")
        self.right_cam_var = tk.StringVar(value=f"Camera {cfg.get('camera_right_id', 1)}")

        self.left_flip_h = tk.BooleanVar(value=cfg.get("flip_left_h", False))
        self.left_flip_v = tk.BooleanVar(value=cfg.get("flip_left_v", True))
        self.right_flip_h = tk.BooleanVar(value=cfg.get("flip_right_h", True))
        self.right_flip_v = tk.BooleanVar(value=cfg.get("flip_right_v", False))

        self.left_b_enabled = tk.BooleanVar(value=cfg.get("boundary_left_enabled", True))
        self.left_bx = tk.IntVar(value=cfg.get("boundary_left_x", 320))
        self.left_by = tk.IntVar(value=cfg.get("boundary_left_y", 240))
        self.left_br = tk.IntVar(value=cfg.get("boundary_left_r", 110))
        self.left_sq_h = tk.IntVar(value=cfg.get("boundary_left_squeeze_h", 100))
        self.left_sq_v = tk.IntVar(value=cfg.get("boundary_left_squeeze_v", 100))

        self.right_b_enabled = tk.BooleanVar(value=cfg.get("boundary_right_enabled", True))
        self.right_bx = tk.IntVar(value=cfg.get("boundary_right_x", 320))
        self.right_by = tk.IntVar(value=cfg.get("boundary_right_y", 240))
        self.right_br = tk.IntVar(value=cfg.get("boundary_right_r", 110))
        self.right_sq_h = tk.IntVar(value=cfg.get("boundary_right_squeeze_h", 100))
        self.right_sq_v = tk.IntVar(value=cfg.get("boundary_right_squeeze_v", 100))

        self.labels = {}
        self.scales = {}
        self.alg_combos = []

        self._build_ui()
        self._refresh_camera_dropdowns()
        self._fit_window()

        self.bind("<Key>", self._on_key_press)
        self.bind("<Configure>", self._on_resize)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(self.FRAME_MS, self._render_loop)

    # ------------------------------------------------------------------ resizing
    SIDE = 16                          # window margin
    H_EXTRA = 2 * SIDE + 12 + 2 * 2 * 8   # margins + gap between the camera cards + their inner padding
    CONTROLS_H = 400                   # height of everything except the camera images; measured in _fit_window

    def _scale(self) -> float:
        return self.view_w / float(FRAME_W)

    def _on_resize(self, event):
        if event.widget is not self:
            return
        w = min((event.width - self.H_EXTRA) // 2, int((event.height - self.CONTROLS_H) * 4 / 3))
        w = max(200, w)
        h = w * 3 // 4
        if (w, h) != (self.view_w, self.view_h):
            self.view_w, self.view_h = w, h
            for lbl in (self.left_canvas, self.right_canvas):
                lbl.config(width=w, height=h)

    def _fit_window(self):
        """Default window size = what the controls need + the two camera images at 400x300."""
        self.update_idletasks()
        for c in self._cards:
            c._fit_height()
        self.update_idletasks()
        req_h = self.winfo_reqheight()
        self.CONTROLS_H = req_h - VIEW_H
        w = max(self.winfo_reqwidth(), 2 * VIEW_W + self.H_EXTRA)
        self.geometry(f"{w}x{req_h}")
        self.minsize(min(w, 760), self.CONTROLS_H + 150)

    # ------------------------------------------------------------------ algorithm sync
    def _on_algorithm_changed(self, alg_name: str):
        can_alg = to_canonical_name(alg_name)
        self.active_alg = can_alg
        self.alg_display_var.set(to_display_name(can_alg))

    def _on_alg_combo_select(self, event=None):
        self.controller.set_pupil_algorithm(to_canonical_name(self.alg_display_var.get()))

    # ------------------------------------------------------------------ UI build (rounded, AAC app style)
    def _build_ui(self):
        P = self.SIDE
        self._cards = []
        # the two camera images, each in a rounded card
        video = tk.Frame(self, bg=BG_MAIN)
        video.pack(fill="x", padx=P, pady=(P, 10))
        self._blank = ImageTk.PhotoImage(Image.new("RGB", (VIEW_W, VIEW_H), IMAGE_BG))
        for eye, side, pad in (("left", "left", (0, 6)), ("right", "right", (6, 0))):
            card = Card(video, fill=BG_SURFACE, border=BORDER_COL, radius=14, pad=8)
            card.pack(side=side, padx=pad, expand=True, fill="x")
            self._cards.append(card)
            lbl = tk.Label(card.inner, image=self._blank, bg=BG_SURFACE, bd=0, width=VIEW_W, height=VIEW_H,
                           cursor="crosshair")
            lbl.pack()
            lbl.bind("<Button-1>", lambda e, eye=eye: self._on_canvas_drag(eye, e))
            lbl.bind("<B1-Motion>", lambda e, eye=eye: self._on_canvas_drag(eye, e))
            lbl.bind("<MouseWheel>", lambda e, eye=eye: self._on_canvas_wheel(eye, e))
            setattr(self, f"{eye}_canvas", lbl)

        # recording (saves 10 s of raw frames for offline replay, research/replay.py) + pupil smoothing
        bar = tk.Frame(self, bg=BG_MAIN)
        bar.pack(fill="x", padx=P, pady=(0, 12))
        self.rec_btn = RoundButton(bar, "⏺  Record 10 s", self._toggle_record, bg=BTN_BG, fg=TEXT_MAIN,
                                   hover=BTN_HOVER, font=FONT_BTN, radius=10, padx=14, pady=6)
        self.rec_btn.pack(side="left")
        self.rec_lbl = tk.Label(bar, text="Look around, hold still, blink while recording.",
                                font=FONT_SM, fg=TEXT_MUTED, bg=BG_MAIN)
        self.rec_lbl.pack(side="left", padx=12)
        # pupil smoothing (ArcFit): steadier centre and circle; 3 = default
        self.smooth_var = tk.IntVar(value=int(round(float(self.controller.cfg.get("pupil_smoothing", 3)))))
        Slider(bar, 0, 10, self.smooth_var, self._on_smoothing, length=150, active=CYAN).pack(side="right")
        self.smooth_lbl = tk.Label(bar, text="", font=FONT_UI, fg=TEXT_MAIN, bg=BG_MAIN, width=26, anchor="e")
        self.smooth_lbl.pack(side="right", padx=(0, 10))
        self._on_smoothing(self.smooth_var.get(), save=False)

        # eye selector (instead of tabs) + pupil detection algorithm (one setting for both eyes)
        row = tk.Frame(self, bg=BG_MAIN)
        row.pack(fill="x", padx=P, pady=(0, 10))
        self._tab_btns = {}
        for eye, txt in (("left", "Left eye"), ("right", "Right eye")):
            b = RoundButton(row, txt, lambda e=eye: self._show_eye(e), font=FONT_BTN, radius=16, padx=20, pady=6)
            b.pack(side="left", padx=(0, 8))
            self._tab_btns[eye] = b
        combo_alg = Dropdown(row, self.alg_display_var, list(ALGORITHM_DISPLAY.values()),
                             command=lambda v: self._on_alg_combo_select(), width=250, font=FONT_UI, pady=5)
        combo_alg.pack(side="right")
        self.alg_combos.append(combo_alg)
        tk.Label(row, text="Pupil detection", font=FONT_UI, fg=TEXT_MUTED, bg=BG_MAIN).pack(side="right", padx=(0, 8))

        # one settings card per eye; only the selected eye's card is shown
        self._panels = {}
        for eye, accent, args in (("left", LEFT_ACCENT, (self.left_cam_var, self.left_flip_h, self.left_flip_v,
                                                         self.left_b_enabled, self.left_bx, self.left_by, self.left_br,
                                                         self.left_sq_h, self.left_sq_v)),
                                  ("right", RIGHT_ACCENT, (self.right_cam_var, self.right_flip_h, self.right_flip_v,
                                                           self.right_b_enabled, self.right_bx, self.right_by,
                                                           self.right_br, self.right_sq_h, self.right_sq_v))):
            card = Card(self, fill=BG_SURFACE, border=BORDER_COL, radius=14, pad=16)
            self._cards.append(card)
            self._build_tab_content(card.inner, eye, accent, *args)
            self._panels[eye] = card
        self._show_eye("left")

    def _show_eye(self, eye: str):
        self._tab_eye = eye
        for e, card in self._panels.items():
            if e == eye:
                card.pack(fill="x", padx=self.SIDE, pady=(0, self.SIDE))
            else:
                card.pack_forget()
        for e, b in self._tab_btns.items():
            if e == eye:
                b.set_style(bg=SELECTED_BG, fg=CYAN, hover=SELECTED_BG)
            else:
                b.set_style(bg=BTN_BG, fg=TEXT_MUTED, hover=BTN_HOVER)

    def _slider_row(self, parent, eye, key, title, var, lo, hi):
        f = tk.Frame(parent, bg=BG_SURFACE)
        tk.Label(f, text=title, font=FONT_UI, fg=TEXT_MAIN, bg=BG_SURFACE, width=8, anchor="w").pack(side="left")
        step = dict(bg=BTN_BG, fg=TEXT_MAIN, hover=BTN_HOVER, font=FONT_BTN, radius=12, padx=0, pady=3, width=26)
        RoundButton(f, "−", lambda: self._step_val(eye, key, -5), **step).pack(side="left")
        scale = Slider(f, lo, hi, var, lambda v: self._on_boundary_change(eye), length=120, active=CYAN)
        scale.pack(side="left", fill="x", expand=True, padx=6)
        RoundButton(f, "+", lambda: self._step_val(eye, key, +5), **step).pack(side="left")
        lbl = tk.Label(f, text="", font=FONT_UI, fg=CYAN, bg=BG_SURFACE, width=12, anchor="e")
        lbl.pack(side="left", padx=(6, 0))
        self.labels[f"{eye}_{key}"] = lbl
        self.scales[f"{eye}_{key}"] = scale
        return f

    def _build_tab_content(self, parent, eye: str, accent: str, cam_var, flip_h_var, flip_v_var,
                           b_enabled_var, bx_var, by_var, br_var, sq_h_var, sq_v_var):
        bg = BG_SURFACE
        # camera row: single-eye mode switch (off = this camera closed, the other eye tracks alone), camera, flips
        row1 = tk.Frame(parent, bg=bg)
        row1.pack(fill="x")
        labeled_switch(row1, "Use this eye", self.eye_on[eye], lambda: self._on_eye_toggle(eye),
                       fg=accent, font=FONT_UI_B).pack(side="left")
        tk.Label(row1, text="Camera", font=FONT_UI, fg=TEXT_MUTED, bg=bg).pack(side="left", padx=(32, 8))
        combo = Dropdown(row1, cam_var, [], command=lambda v: self._on_cam_select(eye, v), width=120,
                         font=FONT_UI, pady=5)
        combo.pack(side="left")
        if eye == "left":
            self.left_combo = combo
        else:
            self.right_combo = combo
        flip = lambda: self._on_flip_toggle(eye, flip_h_var.get(), flip_v_var.get())
        labeled_switch(row1, "Flip vertical", flip_v_var, flip, fg=TEXT_MAIN, font=FONT_UI).pack(side="right")
        labeled_switch(row1, "Flip horizontal", flip_h_var, flip, fg=TEXT_MAIN, font=FONT_UI).pack(side="right",
                                                                                                padx=(0, 24))

        tk.Frame(parent, bg=BORDER_COL, height=1).pack(fill="x", pady=14)

        # eyeball boundary cage: the pupil is only searched inside it
        row2 = tk.Frame(parent, bg=bg)
        row2.pack(fill="x")
        labeled_switch(row2, "Boundary cage", b_enabled_var, lambda: self._on_boundary_change(eye),
                       fg=TEXT_MAIN, font=FONT_UI_B).pack(side="left")
        tk.Label(row2, text="the pupil is only searched inside it", font=FONT_SM, fg=TEXT_MUTED,
                 bg=bg).pack(side="left", padx=(10, 0))
        small = dict(bg=BTN_BG, fg=TEXT_MAIN, hover=BTN_HOVER, font=FONT_BTN, radius=10, padx=12, pady=4)
        RoundButton(row2, "⟲  Reset", lambda: self._reset_all(eye), **small).pack(side="right")
        RoundButton(row2, "◯  Make circle", lambda: self._reset_circle(eye), **small).pack(side="right", padx=(0, 8))

        grid = tk.Frame(parent, bg=bg)
        grid.pack(fill="x", pady=(12, 0))
        grid.columnconfigure(0, weight=1, uniform="col")
        grid.columnconfigure(1, weight=1, uniform="col")
        for r, c, key, title, var, lo, hi in ((0, 0, "x", "Centre X", bx_var, X_MIN, X_MAX),
                                              (1, 0, "y", "Centre Y", by_var, Y_MIN, Y_MAX),
                                              (0, 1, "r", "Size", br_var, R_MIN, R_MAX),
                                              (1, 1, "sq_h", "Width", sq_h_var, SQUEEZE_MIN, SQUEEZE_MAX),
                                              (2, 1, "sq_v", "Height", sq_v_var, SQUEEZE_MIN, SQUEEZE_MAX)):
            self._slider_row(grid, eye, key, title, var, lo, hi).grid(
                row=r, column=c, sticky="ew", padx=(0, 20) if c == 0 else (20, 0), pady=5)

        tk.Label(parent, text="Drag on a camera picture to move the cage  ·  mouse wheel: size  ·  keys: W A S D move, "
                              "+ / − size, [ ] width, { } height, C circle, R reset",
                 font=FONT_SM, fg=TEXT_MUTED, bg=bg).pack(anchor="w", pady=(12, 0))

        self._update_labels(eye)

    # ------------------------------------------------------------------ boundary handlers
    def _vars(self, eye: str):
        return (getattr(self, f"{eye}_b_enabled"), getattr(self, f"{eye}_bx"), getattr(self, f"{eye}_by"),
                getattr(self, f"{eye}_br"), getattr(self, f"{eye}_sq_h"), getattr(self, f"{eye}_sq_v"))

    def _var_for(self, eye: str, param: str):
        return getattr(self, f"{eye}_b{param}" if param in ("x", "y", "r") else f"{eye}_{param}")

    def _step_val(self, eye: str, param: str, delta: int):
        var = self._var_for(eye, param)
        lo, hi = {"x": (X_MIN, X_MAX), "y": (Y_MIN, Y_MAX), "r": (R_MIN, R_MAX)}.get(param, (SQUEEZE_MIN, SQUEEZE_MAX))
        var.set(max(lo, min(hi, var.get() + delta)))
        self._on_boundary_change(eye)

    def _reset_circle(self, eye: str):
        getattr(self, f"{eye}_sq_h").set(100)
        getattr(self, f"{eye}_sq_v").set(100)
        self._on_boundary_change(eye)

    def _reset_all(self, eye: str):
        getattr(self, f"{eye}_bx").set(FRAME_W // 2)
        getattr(self, f"{eye}_by").set(FRAME_H // 2)
        getattr(self, f"{eye}_br").set(110)
        getattr(self, f"{eye}_sq_h").set(100)
        getattr(self, f"{eye}_sq_v").set(100)
        self._on_boundary_change(eye)

    def _update_labels(self, eye: str):
        _, bx, by, br, sq_h, sq_v = self._vars(eye)
        rx = max(10, int(round(br.get() * sq_h.get() / 100.0)))
        ry = max(10, int(round(br.get() * sq_v.get() / 100.0)))
        texts = {"x": f"{bx.get()} px", "y": f"{by.get()} px", "r": f"{br.get()} px",
                 "sq_h": f"{sq_h.get()}% ({rx} px)", "sq_v": f"{sq_v.get()}% ({ry} px)"}
        for k, txt in texts.items():
            lbl = self.labels.get(f"{eye}_{k}")
            if lbl:
                lbl.config(text=txt)

    def _on_boundary_change(self, eye: str):
        en, bx, by, br, sq_h, sq_v = self._vars(eye)
        self._update_labels(eye)
        self.controller.set_boundary(eye, bx.get(), by.get(), br.get(), sq_h.get(), sq_v.get(), en.get())

    def _on_canvas_drag(self, eye: str, event):
        sc = self._scale()
        getattr(self, f"{eye}_bx").set(max(X_MIN, min(X_MAX, int(round(event.x / sc)))))
        getattr(self, f"{eye}_by").set(max(Y_MIN, min(Y_MAX, int(round(event.y / sc)))))
        self._on_boundary_change(eye)

    def _on_canvas_wheel(self, eye: str, event):
        self._step_val(eye, "r", 5 if event.delta > 0 else -5)

    def _on_key_press(self, event):
        eye = self._tab_eye
        k = event.keysym.lower()
        actions = {
            "w": ("y", -5), "up": ("y", -5), "s": ("y", +5), "down": ("y", +5),
            "a": ("x", -5), "left": ("x", -5), "d": ("x", +5), "right": ("x", +5),
            "plus": ("r", +5), "equal": ("r", +5), "minus": ("r", -5), "underscore": ("r", -5),
            "bracketleft": ("sq_h", -5), "bracketright": ("sq_h", +5),
            "braceleft": ("sq_v", -5), "braceright": ("sq_v", +5),
        }
        if k in actions:
            self._step_val(eye, *actions[k])
        elif k == "c":
            self._reset_circle(eye)
        elif k == "r":
            self._reset_all(eye)

    # ------------------------------------------------------------------ cameras & flips
    def _on_eye_toggle(self, eye: str):
        want = bool(self.eye_on[eye].get())
        if not self.controller.set_eye_enabled(eye, want):
            self.eye_on[eye].set(True)
            self.rec_lbl.config(text="At least one eye must stay enabled.", fg=RED)
            return
        other = "right" if eye == "left" else "left"
        if self.controller.is_eye_enabled(other):
            self.rec_lbl.config(text="Both eyes in use.", fg=TEXT_MUTED)
        else:
            self.rec_lbl.config(text=f"Single-eye mode: {other} eye only. Recalibrate for best accuracy.", fg=GOLD)
        self._refresh_camera_dropdowns()

    def _refresh_camera_dropdowns(self):
        lid = int(self.controller.cfg.get("camera_left_id", 0))
        rid = int(self.controller.cfg.get("camera_right_id", 1))
        l_on, r_on = self.controller.is_eye_enabled("left"), self.controller.is_eye_enabled("right")
        # the other eye's camera is offered as "(swap)" (same as the app's Sensors tab): picking it swaps the two
        # cameras between the eyes; a disabled eye's camera index is simply free
        def labels(other_cam: int, other_on: bool, own: int):
            return [f"Camera {c} (swap)" if (other_on and c == other_cam and c != own) else f"Camera {c}"
                    for c in AVAILABLE_CAMERAS]
        if self.left_combo:
            self.left_combo.set_values(labels(rid, r_on, lid))
            self.left_cam_var.set(f"Camera {lid}")
            self.left_combo.set_enabled(l_on)
        if self.right_combo:
            self.right_combo.set_values(labels(lid, l_on, rid))
            self.right_cam_var.set(f"Camera {rid}")
            self.right_combo.set_enabled(r_on)

    def _on_cam_select(self, eye: str, val_str: str):
        m = re.search(r'\d+', val_str)
        dev_id = int(m.group(0)) if m else 0
        lid = int(self.controller.cfg.get("camera_left_id", 0))
        rid = int(self.controller.cfg.get("camera_right_id", 1))

        other = "right" if eye == "left" else "left"
        own, other_cam = (lid, rid) if eye == "left" else (rid, lid)
        if dev_id == own:
            return self._refresh_camera_dropdowns()
        if dev_id == other_cam and self.controller.is_eye_enabled(other):
            self.controller.set_camera(other, own)       # swap: the other eye gets this eye's camera
            self.rec_lbl.config(text=f"Cameras swapped: left eye = camera {dev_id if eye == 'left' else own}, "
                                     f"right eye = camera {own if eye == 'left' else dev_id}.", fg=TEXT_MUTED)
        self.controller.set_camera(eye, dev_id)
        self._refresh_camera_dropdowns()

    def _on_flip_toggle(self, eye: str, fh: bool, fv: bool):
        self.controller.set_flip(eye, fh, fv)

    # ------------------------------------------------------------------ render loop
    def _render_loop(self):
        if not self._active:
            return
        try:
            for eye, canvas, attr in (("left", self.left_canvas, "_left_photo"), ("right", self.right_canvas, "_right_photo")):
                smp = self.controller.get_sample(eye)
                cam_id = self.controller.cfg.get(f"camera_{eye}_id", 0)
                if not self.controller.is_eye_enabled(eye):
                    img = self._placeholder_card(f"{eye.upper()} EYE", cam_id, disabled=True)
                elif not smp.connected or smp.frame is None:
                    img = self._placeholder_card(f"{eye.upper()} EYE", cam_id)
                else:
                    # Redraw on every tick (sliders may have moved) but only re-read the frame when it changed
                    img = self._decorate_frame(eye, smp, cam_id)
                if img.shape[1] != self.view_w:                # placeholder cards are drawn at the default size
                    img = cv2.resize(img, (self.view_w, self.view_h))
                photo = ImageTk.PhotoImage(image=Image.fromarray(img))
                setattr(self, attr, photo)
                canvas.config(image=photo)
            self._update_record_status()
        except tk.TclError:
            return
        self.after(self.FRAME_MS, self._render_loop)

    # ------------------------------------------------------------------ recording
    def _on_smoothing(self, val, save=True):
        level = int(float(val))
        self.smooth_lbl.config(text=f"Pupil smoothing: {level}" + ("  (default)" if level == 3 else ""))
        if save:
            self.controller.set_pupil_smoothing(level)

    def _toggle_record(self):
        rec = self.controller.recorder
        if rec is not None and rec.active:
            self.controller.stop_recording()
        else:
            self._rec_name = self.controller.start_recording("eyeview", duration_s=10.0).name

    def _update_record_status(self):
        rec = self.controller.recorder
        if rec is not None and rec.active:
            left = rec.seconds_left()
            self.rec_btn.set_style(text="⏹  Stop", bg=RED, fg="#ffffff", hover="#b71c1c")
            self.rec_lbl.config(text=f"Recording… {left:.1f} s left  ·  frames L {rec.count['left']}  R {rec.count['right']}",
                                fg=RED)
        elif getattr(self, "_rec_name", None):
            self.rec_btn.set_style(text="⏺  Record 10 s", bg=BTN_BG, fg=TEXT_MAIN, hover=BTN_HOVER)
            self.rec_lbl.config(text=f"Saved: recordings/{self._rec_name}", fg=GREEN)

    def _decorate_frame(self, eye: str, smp, cam_id: int) -> np.ndarray:
        en, bx_v, by_v, br_v, sq_h_v, sq_v_v = self._vars(eye)
        b_enabled = en.get()
        bx, by, br, sq_h, sq_v = bx_v.get(), by_v.get(), br_v.get(), sq_h_v.get(), sq_v_v.get()
        fh = getattr(self, f"{eye}_flip_h").get()
        fv = getattr(self, f"{eye}_flip_v").get()
        accent_bgr = BGR_CYAN if eye == "left" else BGR_GOLD
        label = "LEFT EYE" if eye == "left" else "RIGHT EYE"

        SCALE = self._scale()
        frame = cv2.resize(smp.frame, (self.view_w, self.view_h))
        h, w = frame.shape[:2]

        rx = max(10, int(round(br * sq_h / 100.0)))
        ry = max(10, int(round(br * sq_v / 100.0)))
        if b_enabled:
            dcx, dcy = int(round(bx * SCALE)), int(round(by * SCALE))
            drx, dry = max(6, int(round(rx * SCALE))), max(6, int(round(ry * SCALE)))
            mask = np.zeros((h, w), dtype=np.uint8)
            cv2.ellipse(mask, (dcx, dcy), (drx, dry), 0, 0, 360, 255, -1)
            outside = (mask == 0)
            frame[outside] = (frame[outside] * 0.72).astype(np.uint8)
            cv2.ellipse(frame, (dcx, dcy), (drx, dry), 0, 0, 360, accent_bgr, 2, cv2.LINE_AA)
            tl = 7
            cv2.line(frame, (dcx - drx - tl, dcy), (dcx - drx + tl, dcy), accent_bgr, 1, cv2.LINE_AA)
            cv2.line(frame, (dcx + drx - tl, dcy), (dcx + drx + tl, dcy), accent_bgr, 1, cv2.LINE_AA)
            cv2.line(frame, (dcx, dcy - dry - tl), (dcx, dcy - dry + tl), accent_bgr, 1, cv2.LINE_AA)
            cv2.line(frame, (dcx, dcy + dry - tl), (dcx, dcy + dry + tl), accent_bgr, 1, cv2.LINE_AA)
            cv2.circle(frame, (dcx, dcy), 2, accent_bgr, -1, cv2.LINE_AA)

        alg_color = ALGORITHM_COLORS.get(self.active_alg, (50, 255, 50))
        ell = smp.ellipse
        if ell is not None:
            (pcx, pcy), (pw, ph), pang = ell
            if pw > 0 and ph > 0:
                cv2.ellipse(frame, ((pcx * SCALE, pcy * SCALE), (pw * SCALE, ph * SCALE), pang), alg_color, 2, cv2.LINE_AA)
            draw_crosshair(frame, pcx * SCALE, pcy * SCALE, alg_color, size=8)

        cv2.rectangle(frame, (0, 0), (w, 32), BGR_HDR, -1)
        cv2.line(frame, (0, 32), (w, 32), BGR_HDR_LINE, 1)
        cv2.circle(frame, (14, 16), 5, (80, 255, 120), -1, cv2.LINE_AA)
        cv2.putText(frame, f"{label} (Cam {cam_id})", (26, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.46, (255, 255, 255), 1, cv2.LINE_AA)

        if self.active_alg != "None":
            if ell is not None:
                hud_str = f"{smp.cam_fps:.0f} FPS · {self.active_alg} {smp.latency_ms:.1f}ms"
                hud_col = (80, 255, 120)
            else:
                hud_str = f"{smp.cam_fps:.0f} FPS · {self.active_alg} (Searching)"
                hud_col = (120, 180, 255)
        else:
            hud_str = f"{smp.cam_fps:.0f} FPS · detection off"
            hud_col = (160, 160, 160)
        cv2.putText(frame, hud_str, (w - 215, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.38, hud_col, 1, cv2.LINE_AA)

        tags = [t for t, on in (("H", fh), ("V", fv)) if on]
        flip_str = f"FLIP:{'+'.join(tags)}" if tags else "NORMAL"
        if ell is not None:
            cv2.putText(frame, f"PUPIL: ({ell[0][0]:.0f}, {ell[0][1]:.0f}) · {smp.conf * 100:.0f}%", (10, h - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.38, alg_color, 1, cv2.LINE_AA)
        elif self.active_alg != "None":
            cv2.putText(frame, f"{self.active_alg}: SEARCHING", (10, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (80, 160, 255), 1, cv2.LINE_AA)
        else:
            cv2.putText(frame, f"{FRAME_W}x{FRAME_H} · {flip_str}", (10, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (160, 160, 160), 1, cv2.LINE_AA)

        if b_enabled:
            shape_str = f"R={br}px" if (sq_h == 100 and sq_v == 100) else f"Rx={rx} Ry={ry}"
            tag_col = (0, 230, 255) if max(rx, ry) <= 140 else (60, 60, 255)
            cv2.putText(frame, f"CAGE: ({bx},{by}) {shape_str}", (w - 215, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.38, tag_col, 1, cv2.LINE_AA)
        else:
            cv2.putText(frame, "CAGE: DISABLED", (w - 130, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (120, 120, 120), 1, cv2.LINE_AA)

        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    def _placeholder_card(self, label: str, cam_id: int, disabled: bool = False) -> np.ndarray:
        card = np.full((VIEW_H, VIEW_W, 3), BGR_CARD, dtype=np.uint8)
        cv2.rectangle(card, (0, 0), (VIEW_W, 32), BGR_HDR, -1)
        if disabled:
            cv2.circle(card, (14, 16), 5, (120, 120, 120), -1, cv2.LINE_AA)
            cv2.putText(card, f"{label} - Disabled", (26, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (160, 160, 160), 1, cv2.LINE_AA)
            cv2.putText(card, "EYE DISABLED", (118, 140), cv2.FONT_HERSHEY_SIMPLEX, 0.70, (150, 150, 150), 2, cv2.LINE_AA)
            cv2.putText(card, "Tracking uses the other eye only", (88, 175), cv2.FONT_HERSHEY_SIMPLEX, 0.44, (140, 140, 140), 1, cv2.LINE_AA)
            cv2.putText(card, "Tick 'Use this eye' in the tab below to turn it on", (40, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (140, 140, 140), 1, cv2.LINE_AA)
            return card
        cv2.circle(card, (14, 16), 5, (60, 60, 255), -1, cv2.LINE_AA)
        cv2.putText(card, f"{label} (Cam {cam_id}) - Offline", (26, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (160, 160, 160), 1, cv2.LINE_AA)
        cv2.putText(card, "NO SIGNAL", (135, 140), cv2.FONT_HERSHEY_SIMPLEX, 0.70, (60, 60, 255), 2, cv2.LINE_AA)
        cv2.putText(card, "Select active index in tab below", (85, 175), cv2.FONT_HERSHEY_SIMPLEX, 0.44, (140, 140, 140), 1, cv2.LINE_AA)
        return card

    # ------------------------------------------------------------------ lifecycle
    def _on_close(self):
        self._active = False
        self.controller.remove_algorithm_listener(self._on_algorithm_changed)
        if callable(self.on_close_callback):
            self.on_close_callback()
        self.destroy()

    def destroy(self):
        self._active = False
        self.controller.remove_algorithm_listener(self._on_algorithm_changed)
        super().destroy()
