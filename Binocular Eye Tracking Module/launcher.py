#!/usr/bin/env python3
"""
launcher.py
===========
Binocular Eye Tracking Module — Control Panel with Calibration & Raw Mouse Control.

Tabs:
  1. [👁 Sensors & Eye View]
       * Live Hardware Status: Left & Right camera connection health & 30 FPS telemetry.
       * Dynamic Pupil Algorithm Selection: 2-way synchronized with Eye View.
       * Primary Action: Show Dual Eye View.
  2. [🎯 Binocular Calibration]
       * 9-Point Dual-Eye Precision Calibration + 4-Point Unseen Validation.
       * 9-Panel Diagnostic Infographic generation & interactive thumbnail preview.
       * Open full-resolution chart in system image viewer.
  3. [🖱 Raw Mouse Control]
       * Live Raw Gaze Mouse Control with Global Hotkeys:
           [F5] = Toggle Mouse Control ON
           [F6] = Emergency Stop (Disable Immediately)
       * Mode Selection: Raw Unfiltered Movement vs. 1-Euro Filtered Smoothing.
       * Real-Time Telemetry: Screen (X, Y), Left Pupil, Right Pupil, Dual-Sensor mode.
"""

import os
import sys
from typing import Dict, Any, Optional, Tuple, List
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk

# Resolve local paths
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from binocular_controller import BinocularController
from eye_view_window import BinocularEyeViewWindow
from binocular_calibrator import BinocularCalibrator
from binocular_calibration_overlay import BinocularCalibrationOverlay, RecentreOverlay
from finetune_overlay import FineTuneOverlay
from gaze_engine import BinocularGazeEngine
from data.pupil_algorithm import (
    ALGORITHM_NAMES,
    ALGORITHM_COLORS,
    ALGORITHM_DISPLAY,
    to_display_name,
    to_canonical_name,
)

# Styling Palette
BG      = '#0d1117'
SURFACE = '#161b22'
SURF2   = '#21262d'
BORDER  = '#30363d'
CYAN    = '#00e5ff'
GREEN   = '#3fb950'
GOLD    = '#e3b341'
RED     = '#f85149'
TEXT    = '#f0f6fc'
SUBTEXT = '#8b949e'
DIM     = '#484f58'
BLACK   = '#000000'

FONT_TITLE = ('Segoe UI', 11, 'bold')
FONT_B     = ('Segoe UI', 10, 'bold')
FONT_SM    = ('Segoe UI', 9)
FONT_XS    = ('Segoe UI', 8)


def _fmt_px(v) -> str:
    return "n/a" if v is None else f"{v:.1f} px"


class EyeTrackerLauncherApp:
    POLL_MS = 100  # 10 Hz UI telemetry polling

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title('Binocular Eye Tracking Module  --  Control Panel')
        self.root.geometry('600x670')
        self.root.minsize(580, 580)
        self.root.resizable(True, True)
        self.root.configure(bg=BG)
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)

        # 1. Background Video Controller & Camera Streams
        self.controller = BinocularController()
        self.controller.start()

        # 2. Real-Time Gaze & Mouse Engine
        self.gaze_engine = BinocularGazeEngine(self.controller)
        self.gaze_engine.start()

        self._eye_win = None
        self._calib_win = None
        self._chart_photo = None

        self._build_menu()
        self._build_header()
        self._build_tabs()
        self._build_statusbar()

        root.bind('<Control-e>', lambda e: self._toggle_eye_view())
        # F5 / F6 are polled globally by the gaze engine (GetAsyncKeyState); binding them
        # here as well would toggle twice whenever this window has focus.

        self._poll()

    # =========================================================================
    # Menus
    # =========================================================================
    def _build_menu(self):
        mbar = tk.Menu(self.root, bg=SURF2, fg=TEXT, relief='flat', bd=0,
                       activebackground=BORDER, activeforeground=CYAN)
        self.root.config(menu=mbar)

        # File Menu
        file_menu = tk.Menu(mbar, tearoff=0, bg=SURF2, fg=TEXT, relief='flat',
                            activebackground=BORDER, activeforeground=TEXT, font=FONT_SM)
        file_menu.add_command(label='Show Eye View', command=self._toggle_eye_view, accelerator='Ctrl+E')
        file_menu.add_command(label='Calibrate Dual Eyes', command=self._start_calibration)
        file_menu.add_separator()
        file_menu.add_command(label='Exit', command=self._on_close, accelerator='Alt+F4')
        mbar.add_cascade(label='File', menu=file_menu)

        # Algorithm Menu (Dynamic selection)
        cur_alg = self.controller.get_pupil_algorithm()
        self._alg_menu_var = tk.StringVar(value=cur_alg)
        alg_menu = tk.Menu(mbar, tearoff=0, bg=SURF2, fg=TEXT, relief='flat',
                            activebackground=BORDER, activeforeground=TEXT, font=FONT_SM)
        for alg in ALGORITHM_NAMES + ["None"]:
            alg_menu.add_radiobutton(
                label=ALGORITHM_DISPLAY.get(alg, alg),
                variable=self._alg_menu_var,
                value=alg,
                command=lambda a=alg: self._on_algorithm_select(a)
            )
        mbar.add_cascade(label='Algorithm', menu=alg_menu)

        # Help Menu
        help_menu = tk.Menu(mbar, tearoff=0, bg=SURF2, fg=TEXT, relief='flat',
                            activebackground=BORDER, activeforeground=TEXT, font=FONT_SM)
        help_menu.add_command(label='About', command=self._about)
        mbar.add_cascade(label='Help', menu=help_menu)

    # =========================================================================
    # Header
    # =========================================================================
    def _build_header(self):
        hdr = tk.Frame(self.root, bg=SURFACE)
        hdr.pack(fill='x')

        canvas = tk.Canvas(hdr, height=52, bg=SURFACE, highlightthickness=0)
        canvas.pack(fill='x')

        canvas.create_oval(16, 12, 40, 36, fill=CYAN, outline='')
        canvas.create_text(28, 24, text='2E', font=('Segoe UI', 9, 'bold'), fill=BLACK)

        canvas.create_text(50, 16, text='Binocular Eye Tracking Module', anchor='w',
                           fill=TEXT, font=FONT_TITLE)
        canvas.create_text(50, 34, text='Dual-Sensor Alignment, Gaze Calibration & Mouse Control',
                           fill=SUBTEXT, font=FONT_XS, anchor='nw')

        tk.Frame(self.root, bg=BORDER, height=1).pack(fill='x')

    # =========================================================================
    # Tab Container
    # =========================================================================
    def _build_tabs(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('Dark.TNotebook', background=BG, borderwidth=0)
        style.configure('Dark.TNotebook.Tab', background=SURF2, foreground=TEXT,
                        padding=[14, 6], font=FONT_B, borderwidth=0)
        style.map('Dark.TNotebook.Tab',
                  background=[('selected', SURFACE)],
                  foreground=[('selected', CYAN)])

        self.notebook = ttk.Notebook(self.root, style='Dark.TNotebook')
        self.notebook.pack(fill='both', expand=True, padx=12, pady=(8, 4))

        # Tab 1: Sensors & Eye View
        tab_sensors = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(tab_sensors, text='👁  Sensors & Eye View')
        self._build_sensors_tab(tab_sensors)

        # Tab 2: Binocular Calibration
        tab_calib = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(tab_calib, text='🎯  Calibration')
        self._build_calibration_tab(tab_calib)

        # Tab 3: Ergonomic Mouse Control
        tab_mouse = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(tab_mouse, text='🖱  Mouse Control')
        self._build_mouse_tab(tab_mouse)

    # =========================================================================
    # Tab 1: Sensors & Eye View
    # =========================================================================
    def _build_sensors_tab(self, parent):
        wrap = tk.Frame(parent, bg=BG, padx=10, pady=8)
        wrap.pack(fill='both', expand=True)

        # Hardware Status Card
        tk.Label(wrap, text='HARDWARE SENSORS (SOLID 30 FPS)', fg=SUBTEXT, font=FONT_XS, bg=BG).pack(anchor='w', pady=(0, 3))
        card_hw = tk.Frame(wrap, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        card_hw.pack(fill='x', pady=(0, 8))

        inner_hw = tk.Frame(card_hw, bg=SURFACE, padx=12, pady=8)
        inner_hw.pack(fill='x')

        self._dots = {}
        self._dot_labels = {}
        for key, display in [('cam_left', 'Left Eye Camera'), ('cam_right', 'Right Eye Camera')]:
            row = tk.Frame(inner_hw, bg=SURFACE)
            row.pack(fill='x', pady=3)
            cv = tk.Canvas(row, width=12, height=12, bg=SURFACE, highlightthickness=0)
            cv.pack(side='left', padx=(0, 10))
            dot = cv.create_oval(1, 1, 11, 11, fill=DIM, outline='')
            self._dots[key] = (cv, dot)
            tk.Label(row, text=display, fg=SUBTEXT, font=FONT_SM, bg=SURFACE, width=16, anchor='w').pack(side='left')
            lbl = tk.Label(row, text='\u2014', fg=TEXT, font=FONT_SM, bg=SURFACE, anchor='w')
            lbl.pack(side='left', fill='x')
            self._dot_labels[key] = lbl

        # Algorithm Selection Card
        tk.Label(wrap, text='ACTIVE PUPIL DETECTION ALGORITHM', fg=SUBTEXT, font=FONT_XS, bg=BG).pack(anchor='w', pady=(0, 3))
        card_alg = tk.Frame(wrap, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        card_alg.pack(fill='x', pady=(0, 10))

        inner_alg = tk.Frame(card_alg, bg=SURFACE, padx=12, pady=10)
        inner_alg.pack(fill='x')

        row_a = tk.Frame(inner_alg, bg=SURFACE)
        row_a.pack(fill='x')
        tk.Label(row_a, text='Algorithm:', font=FONT_B, fg=CYAN, bg=SURFACE).pack(side='left', padx=(0, 8))

        cur_alg = self.controller.get_pupil_algorithm()
        self._alg_var = tk.StringVar(value=to_display_name(cur_alg))
        self._alg_combo = ttk.Combobox(
            row_a, textvariable=self._alg_var,
            values=list(ALGORITHM_DISPLAY.values()),
            state='readonly', font=FONT_SM
        )
        self._alg_combo.pack(side='left', fill='x', expand=True)
        self._alg_combo.bind('<<ComboboxSelected>>', lambda e: self._on_combo_select(self._alg_var.get()))

        # Show Dual Eye View Action
        self._action_btn = tk.Button(
            wrap,
            text='👁   Show Dual Eye View',
            command=self._toggle_eye_view,
            bg=CYAN,
            fg=BLACK,
            font=('Segoe UI', 11, 'bold'),
            relief='flat',
            bd=0,
            cursor='hand2',
            activebackground='#00b8d4',
            activeforeground=BLACK,
            padx=20,
            pady=10
        )
        self._action_btn.pack(fill='x', pady=6)

    # =========================================================================
    # Tab 2: Binocular Calibration
    # =========================================================================
    def _build_calibration_tab(self, parent):
        wrap = tk.Frame(parent, bg=BG, padx=10, pady=8)
        wrap.pack(fill='both', expand=True)

        # Status Summary Card
        card_st = tk.Frame(wrap, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        card_st.pack(fill='x', pady=(0, 6))
        inner_st = tk.Frame(card_st, bg=SURFACE, padx=12, pady=8)
        inner_st.pack(fill='x')

        self._calib_status_lbl = tk.Label(
            inner_st,
            text='Status: Uncalibrated  ·  Ready for Dual-Eye Calibration',
            font=FONT_B, fg=GOLD, bg=SURFACE, anchor='w'
        )
        self._calib_status_lbl.pack(fill='x')

        self._calib_details_lbl = tk.Label(
            inner_st,
            text='Click Start Calibration to run the 9-point sequence and generate chart.',
            font=FONT_XS, fg=SUBTEXT, bg=SURFACE, anchor='w'
        )
        self._calib_details_lbl.pack(fill='x', pady=(2, 0))

        # Action Buttons Row
        btns_row = tk.Frame(wrap, bg=BG)
        btns_row.pack(fill='x', pady=(0, 6))

        self._start_calib_btn = tk.Button(
            btns_row,
            text='🎯   Start 9-Point Calibration',
            command=self._start_calibration,
            bg=CYAN, fg=BLACK, font=('Segoe UI', 10, 'bold'),
            relief='flat', bd=0, cursor='hand2',
            activebackground='#00b8d4', activeforeground=BLACK,
            padx=16, pady=8
        )
        self._start_calib_btn.pack(side='left', fill='x', expand=True, padx=(0, 4))

        # Level 2: random dots + 2 vertical paths on top of the saved calibration (finetune_overlay.py)
        tk.Button(
            btns_row, text='🔧  Level 2 Fine-tune', command=self._start_finetune,
            bg=SURF2, fg=CYAN, font=('Segoe UI', 10, 'bold'), relief='flat', bd=0, cursor='hand2',
            activebackground=BORDER, activeforeground=TEXT, padx=12, pady=8
        ).pack(side='left', padx=(0, 4))

        # record raw eye frames during calibration, for offline replay (research/replay.py)
        self._rec_calib_var = tk.BooleanVar(value=bool(self.controller.cfg.get('record_calibration', False)))
        tk.Checkbutton(
            wrap, text='⏺  Record eye video during calibration (saved in recordings/)',
            variable=self._rec_calib_var, command=self._on_record_calib_toggle,
            font=FONT_XS, fg=SUBTEXT, bg=BG, activebackground=BG, activeforeground=TEXT,
            selectcolor=SURF2, bd=0
        ).pack(anchor='w', pady=(0, 4))

        # live gaze pointer in full calibration, Level 2 fine-tune and Quick Re-centre (one switch for all three)
        self._pointer_var = tk.BooleanVar(value=bool(self.controller.cfg.get('show_gaze_pointer', True)))
        tk.Checkbutton(
            wrap, text='◎  Show gaze pointer during calibration, fine-tune and re-centre',
            variable=self._pointer_var,
            command=lambda: self.controller.set_mouse_settings(show_gaze_pointer=bool(self._pointer_var.get())),
            font=FONT_XS, fg=SUBTEXT, bg=BG, activebackground=BG, activeforeground=TEXT,
            selectcolor=SURF2, bd=0
        ).pack(anchor='w', pady=(0, 4))

        tk.Button(
            btns_row, text='⊕  Quick Re-centre (8 s)', command=self._start_recentre,
            bg=SURF2, fg=CYAN, font=FONT_SM, relief='flat', bd=0, cursor='hand2',
            activebackground=BORDER, activeforeground=TEXT, padx=12, pady=8
        ).pack(side='right', padx=(4, 0))

        self._view_chart_btn = tk.Button(
            btns_row,
            text='📊   Open Full Chart',
            command=self._open_diagnostic_chart,
            bg=SURF2, fg=TEXT, font=FONT_SM,
            relief='flat', bd=0, cursor='hand2',
            activebackground=BORDER, activeforeground=TEXT,
            padx=14, pady=8
        )
        self._view_chart_btn.pack(side='right', padx=(4, 0))

        # Chart Preview Card
        card_preview = tk.Frame(wrap, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        card_preview.pack(fill='both', expand=True)

        self._chart_lbl = tk.Label(
            card_preview,
            text='No calibration chart generated yet.\nClick Start Calibration to begin.',
            font=FONT_SM, fg=SUBTEXT, bg=SURFACE, justify='center'
        )
        self._chart_lbl.pack(fill='both', expand=True, padx=4, pady=4)

        self._refresh_calibration_ui()

    def _refresh_calibration_ui(self):
        calib = self.gaze_engine.calibrator
        if calib.is_calibrated:
            m = calib.metrics
            grade = m.get('grade', 'N/A')
            self._calib_status_lbl.config(
                text=f"Status: Calibrated  ·  Grade: {grade}",
                fg=GREEN if grade.startswith(('A', 'B')) else GOLD
            )
            self._calib_details_lbl.config(
                text=(f"Mean Gaze Error: {_fmt_px(m.get('mean_error_px'))}  ·  "
                      f"Pursuit RMS: {_fmt_px(m.get('pursuit_rmse'))}  ·  "
                      f"Jitter: {_fmt_px(m.get('mean_jitter'))}  ·  {m.get('calibrated_at', '')}")
            )
        else:
            self._calib_status_lbl.config(
                text='Status: Uncalibrated  ·  Ready for Dual-Eye Calibration',
                fg=GOLD
            )
            self._calib_details_lbl.config(
                text='Click Start Calibration to run the 9-point sequence and generate chart.'
            )

        # Load chart thumbnail if exists
        chart_path = os.path.join(_THIS_DIR, "binocular_calibration_chart.png")
        if os.path.exists(chart_path):
            try:
                img = Image.open(chart_path)
                img.thumbnail((440, 240), Image.Resampling.LANCZOS)
                self._chart_photo = ImageTk.PhotoImage(img)
                self._chart_lbl.config(image=self._chart_photo, text='')
            except Exception:
                pass

    def _on_record_calib_toggle(self):
        self.controller.set_mouse_settings(record_calibration=bool(self._rec_calib_var.get()))

    def _start_calibration(self):
        if self._calib_win is not None and self._calib_win.winfo_exists():
            self._calib_win.lift()
            return

        self._sbar_var.set("Calibration in progress  ·  Focus steadily on the animated target dots...")
        self._calib_win = BinocularCalibrationOverlay(
            self.root,
            self.controller,
            on_complete=self._on_calibration_completed,
            on_cancel=self._on_calibration_cancelled
        )

    def _start_finetune(self):
        """Level 2 calibration: refines the saved calibration; saved only if it measurably improves."""
        if self._calib_win is not None and self._calib_win.winfo_exists():
            self._calib_win.lift()
            return
        self._sbar_var.set("Level 2 fine-tuning  ·  follow the dots...")

        def done(msg):
            self._calib_win = None
            self.gaze_engine.calibrator = self._load_calibration()
            self._refresh_calibration_ui()
            self._sbar_var.set(msg)
        self._calib_win = FineTuneOverlay(self.root, self.controller, on_done=done)

    @staticmethod
    def _load_calibration() -> BinocularCalibrator:
        """Loaded first, then handed to the gaze engine, so the mouse never sees a half-loaded calibration."""
        cal = BinocularCalibrator()
        cal.load(os.path.join(_THIS_DIR, "binocular_calibration.json"))
        return cal

    def _start_recentre(self):
        """[R1] 5-dot re-centre: corrects headset slip without a full calibration."""
        if not self.gaze_engine.calibrator.is_calibrated:
            self._sbar_var.set("Calibrate first, then use Quick Re-centre")
            return
        RecentreOverlay(self.root, self.controller, self.gaze_engine.calibrator,
                        on_done=lambda msg: self._sbar_var.set(msg))

    def _on_calibration_completed(self, metrics: Dict[str, Any], chart_path: str):
        self._calib_win = None
        self.gaze_engine.calibrator = self._load_calibration()
        self._refresh_calibration_ui()
        grade = metrics.get('grade', 'N/A')
        self._sbar_var.set(f"Calibration Completed! Grade: {grade}  ·  Mean Error: {_fmt_px(metrics.get('mean_error_px'))}  ·  Chart saved")

    def _on_calibration_cancelled(self):
        self._calib_win = None
        self._sbar_var.set("Calibration cancelled  ·  Ready")

    def _open_diagnostic_chart(self):
        chart_path = os.path.join(_THIS_DIR, "binocular_calibration_chart.png")
        if os.path.exists(chart_path):
            try:
                os.startfile(chart_path)
            except Exception as e:
                messagebox.showinfo("Diagnostic Chart", f"Chart saved at:\n{chart_path}")
        else:
            messagebox.showinfo("No Chart", "No calibration chart found yet. Please run calibration first.")

    # =========================================================================
    # Tab 3: Mouse Control (Identical architecture to Eye Tracking Module)
    # =========================================================================
    def _build_mouse_tab(self, parent):
        wrap = tk.Frame(parent, bg=BG, padx=10, pady=8)
        wrap.pack(fill='both', expand=True)

        # 1. Master Toggle & Hotkey Card
        card_toggle = tk.Frame(wrap, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        card_toggle.pack(fill='x', pady=(0, 6))
        inner_toggle = tk.Frame(card_toggle, bg=SURFACE, padx=12, pady=8)
        inner_toggle.pack(fill='x')

        self._mouse_btn = tk.Button(
            inner_toggle,
            text='▶   Enable Eye Mouse Control (F5)',
            command=self._toggle_mouse_control,
            bg=CYAN, fg=BLACK, font=('Segoe UI', 11, 'bold'),
            relief='flat', bd=0, cursor='hand2',
            activebackground='#00b8d4', activeforeground=BLACK,
            padx=20, pady=8
        )
        self._mouse_btn.pack(fill='x')

        hotkey_banner = tk.Label(
            inner_toggle,
            text='⌨  Hotkeys: [F5] Toggle Mouse ON/OFF    [F6] Emergency Stop    [Spacebar] Click',
            font=FONT_SM, fg=CYAN, bg=SURFACE
        )
        hotkey_banner.pack(pady=(4, 0))

        # 2. Movement Mode Selection Card
        card_mode = tk.Frame(wrap, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        card_mode.pack(fill='x', pady=(0, 6))
        inner_mode = tk.Frame(card_mode, bg=SURFACE, padx=12, pady=6)
        inner_mode.pack(fill='x')

        tk.Label(inner_mode, text='MOUSE MOVEMENT MODE', font=FONT_XS, fg=SUBTEXT, bg=SURFACE).pack(anchor='w', pady=(0, 2))
        self._mouse_mode_var = tk.StringVar(value=self.gaze_engine.mouse_mode)

        row_m = tk.Frame(inner_mode, bg=SURFACE)
        row_m.pack(fill='x')

        tk.Radiobutton(
            row_m, text='GazeSmoother (Classic EyeTracker Module)',
            variable=self._mouse_mode_var, value=BinocularGazeEngine.MODE_SMOOTHER,
            font=FONT_SM, fg=TEXT, bg=SURFACE, activebackground=SURFACE,
            activeforeground=CYAN, selectcolor=SURF2, bd=0,
            command=self._on_mouse_mode_changed
        ).pack(side='left', padx=(0, 10))

        tk.Radiobutton(
            row_m, text='1-Euro Filter',
            variable=self._mouse_mode_var, value=BinocularGazeEngine.MODE_FILTERED,
            font=FONT_SM, fg=TEXT, bg=SURFACE, activebackground=SURFACE,
            activeforeground=CYAN, selectcolor=SURF2, bd=0,
            command=self._on_mouse_mode_changed
        ).pack(side='left', padx=(0, 10))

        tk.Radiobutton(
            row_m, text='Raw Direct',
            variable=self._mouse_mode_var, value=BinocularGazeEngine.MODE_RAW,
            font=FONT_SM, fg=TEXT, bg=SURFACE, activebackground=SURFACE,
            activeforeground=CYAN, selectcolor=SURF2, bd=0,
            command=self._on_mouse_mode_changed
        ).pack(side='left')

        # 3. Ergonomic Tuning Card (Sliders matching Eye Tracking Module)
        card_tune = tk.Frame(wrap, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        card_tune.pack(fill='x', pady=(0, 6))
        inner_tune = tk.Frame(card_tune, bg=SURFACE, padx=12, pady=6)
        inner_tune.pack(fill='x')

        row_th = tk.Frame(inner_tune, bg=SURFACE)
        row_th.pack(fill='x', pady=(0, 4))
        tk.Label(row_th, text='EYETRACKER MODULE CURSOR TUNING', font=FONT_XS, fg=SUBTEXT, bg=SURFACE).pack(side='left')

        btn_reset = tk.Button(
            row_th, text='↺ Reset to EyeTracker Defaults', font=FONT_XS, fg=CYAN, bg=SURF2,
            activebackground=BORDER, activeforeground=TEXT, relief='flat', bd=0, cursor='hand2',
            command=self._reset_mouse_tuning
        )
        btn_reset.pack(side='right')

        # Slider 1: Smoothing Alpha
        cur_alpha = self.gaze_engine.alpha
        self._lbl_alpha_val = tk.Label(
            inner_tune,
            text=f"Smoothing Alpha: {cur_alpha:.2f}  (Lower = smoother, higher = faster response)",
            font=FONT_SM, fg=TEXT, bg=SURFACE, anchor='w'
        )
        self._lbl_alpha_val.pack(fill='x')
        self._scale_alpha = tk.Scale(
            inner_tune, from_=0.01, to=0.50, resolution=0.01, orient='horizontal',
            bg=SURFACE, fg=TEXT, troughcolor=SURF2, activebackground=CYAN,
            highlightthickness=0, bd=0, showvalue=False,
            command=self._on_alpha_changed
        )
        self._scale_alpha.set(cur_alpha)
        self._scale_alpha.pack(fill='x', pady=(0, 3))

        # Slider 2: Cursor Gain
        cur_gain = self.gaze_engine.gain
        self._lbl_gain_val = tk.Label(
            inner_tune,
            text=f"Cursor Gain: {cur_gain:.1f}×  (1.0× = True 1:1 Calibrated Gaze)",
            font=FONT_SM, fg=TEXT, bg=SURFACE, anchor='w'
        )
        self._lbl_gain_val.pack(fill='x')
        self._scale_gain = tk.Scale(
            inner_tune, from_=0.5, to=5.0, resolution=0.1, orient='horizontal',
            bg=SURFACE, fg=TEXT, troughcolor=SURF2, activebackground=CYAN,
            highlightthickness=0, bd=0, showvalue=False,
            command=self._on_gain_changed
        )
        self._scale_gain.set(cur_gain)
        self._scale_gain.pack(fill='x', pady=(0, 3))

        # Slider 3: Monitor DPI (Effective Gain scaling)
        cur_dpi = self.gaze_engine.dpi
        eff_gain = cur_gain * (cur_dpi / 96.0)
        self._lbl_dpi_val = tk.Label(
            inner_tune,
            text=f"Monitor DPI Scale: {int(cur_dpi)} DPI  (Effective Gain: {eff_gain:.2f}× · Zero Distortion at 1.00×)",
            font=FONT_SM, fg=TEXT, bg=SURFACE, anchor='w'
        )
        self._lbl_dpi_val.pack(fill='x')
        self._scale_dpi = tk.Scale(
            inner_tune, from_=30, to=192, resolution=1, orient='horizontal',
            bg=SURFACE, fg=TEXT, troughcolor=SURF2, activebackground=CYAN,
            highlightthickness=0, bd=0, showvalue=False,
            command=self._on_dpi_changed
        )
        self._scale_dpi.set(int(cur_dpi))
        self._scale_dpi.pack(fill='x', pady=(0, 3))

        # Slider 4: Blink Grace Period (ms)
        cur_grace = self.gaze_engine.grace_ms
        self._lbl_grace_val = tk.Label(
            inner_tune,
            text=f"Blink Grace Period: {int(cur_grace)} ms  (Freezes cursor position during blinks)",
            font=FONT_SM, fg=TEXT, bg=SURFACE, anchor='w'
        )
        self._lbl_grace_val.pack(fill='x')
        self._scale_grace = tk.Scale(
            inner_tune, from_=50, to=600, resolution=10, orient='horizontal',
            bg=SURFACE, fg=TEXT, troughcolor=SURF2, activebackground=CYAN,
            highlightthickness=0, bd=0, showvalue=False,
            command=self._on_grace_changed
        )
        self._scale_grace.set(int(cur_grace))
        self._scale_grace.pack(fill='x', pady=(0, 3))

        # Spacebar Click Checkbox
        self._spacebar_click_var = tk.BooleanVar(value=self.gaze_engine.spacebar_click)
        self._chk_spacebar = tk.Checkbutton(
            inner_tune,
            text='⌨ Spacebar Click (Press Spacebar anywhere to left-click at current cursor)',
            variable=self._spacebar_click_var,
            font=FONT_SM, fg=TEXT, bg=SURFACE, activebackground=SURFACE,
            activeforeground=CYAN, selectcolor=SURF2, bd=0,
            command=self._on_spacebar_click_toggled
        )
        self._chk_spacebar.pack(anchor='w', pady=(2, 2))

        # 4. Live Telemetry Card
        card_telem = tk.Frame(wrap, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        card_telem.pack(fill='both', expand=True, pady=(0, 4))
        inner_telem = tk.Frame(card_telem, bg=SURFACE, padx=12, pady=6)
        inner_telem.pack(fill='both', expand=True)

        tk.Label(inner_telem, text='REAL-TIME GAZE & CURSOR TELEMETRY', font=FONT_XS, fg=SUBTEXT, bg=SURFACE).pack(anchor='w', pady=(0, 2))

        row_t1 = tk.Frame(inner_telem, bg=SURFACE)
        row_t1.pack(fill='x', pady=1)
        self._lbl_gaze_coords = tk.Label(row_t1, text='Screen Cursor: (0, 0)', font=FONT_B, fg=GREEN, bg=SURFACE, anchor='w')
        self._lbl_gaze_coords.pack(side='left', fill='x', expand=True)

        self._lbl_dynamics_state = tk.Label(row_t1, text='Status: Ready  ·  Eff Gain: 1.00×', font=FONT_SM, fg=CYAN, bg=SURFACE, anchor='e')
        self._lbl_dynamics_state.pack(side='right')

        row_t2 = tk.Frame(inner_telem, bg=SURFACE)
        row_t2.pack(fill='x', pady=1)
        self._lbl_pupil_l = tk.Label(row_t2, text='Left Pupil: (px: —, py: —)', font=FONT_SM, fg=CYAN, bg=SURFACE, anchor='w')
        self._lbl_pupil_l.pack(side='left', fill='x', expand=True)

        self._lbl_pupil_r = tk.Label(row_t2, text='Right Pupil: (px: —, py: —)', font=FONT_SM, fg=GOLD, bg=SURFACE, anchor='e')
        self._lbl_pupil_r.pack(side='right')

        self._lbl_gaze_mode = tk.Label(inner_telem, text='Signal Mode: No Signal', font=FONT_SM, fg=TEXT, bg=SURFACE, anchor='w')
        self._lbl_gaze_mode.pack(fill='x', pady=1)

        fail_safe_lbl = tk.Label(
            inner_telem,
            text='Safety note: If cursor moves uncontrollably, hit F6 or move cursor to top-left corner (0,0).',
            font=FONT_XS, fg=RED, bg=SURFACE, anchor='w'
        )
        fail_safe_lbl.pack(anchor='w', pady=(2, 0))

    def _on_alpha_changed(self, val_str):
        try:
            val = float(val_str)
            self.gaze_engine.set_mouse_params(alpha=val)
            self._lbl_alpha_val.config(text=f"Smoothing Alpha: {val:.2f}  (Lower = smoother & inertial, higher = faster)")
        except Exception:
            pass

    def _on_gain_changed(self, val_str):
        try:
            val = float(val_str)
            self.gaze_engine.set_mouse_params(gain=val)
            eff = val * (self.gaze_engine.dpi / 96.0)
            self._lbl_gain_val.config(text=f"Cursor Gain: {val:.1f}×  (1.0× = True 1:1 Calibrated Gaze)")
            self._lbl_dpi_val.config(text=f"Monitor DPI Scale: {int(self.gaze_engine.dpi)} DPI  (Effective Gain: {eff:.2f}× · Zero Distortion at 1.00×)")
        except Exception:
            pass

    def _on_dpi_changed(self, val_str):
        try:
            val = float(val_str)
            self.gaze_engine.set_mouse_params(dpi=val)
            eff = self.gaze_engine.gain * (val / 96.0)
            self._lbl_dpi_val.config(text=f"Monitor DPI Scale: {int(val)} DPI  (Effective Gain: {eff:.2f}× · Zero Distortion at 1.00×)")
        except Exception:
            pass

    def _on_grace_changed(self, val_str):
        try:
            val = int(float(val_str))
            self.gaze_engine.set_mouse_params(grace_ms=val)
            self._lbl_grace_val.config(text=f"Blink Grace Period: {val} ms  (Freezes cursor position during blinks)")
        except Exception:
            pass

    def _on_spacebar_click_toggled(self):
        val = bool(self._spacebar_click_var.get())
        self.gaze_engine.set_mouse_params(spacebar_click=val)

    def _on_mouse_mode_changed(self):
        mode = self._mouse_mode_var.get()
        self.gaze_engine.set_mouse_mode(mode)
        is_smoother = (mode == BinocularGazeEngine.MODE_SMOOTHER)
        state = 'normal' if is_smoother else 'disabled'
        self._scale_alpha.config(state=state)
        self._scale_gain.config(state=state)
        self._scale_dpi.config(state=state)
        self._scale_grace.config(state=state)

    def _reset_mouse_tuning(self):
        self.gaze_engine.reset_mouse_params()
        self._scale_alpha.set(0.15)
        self._scale_gain.set(1.0)
        self._scale_dpi.set(96)
        self._scale_grace.set(240)
        self._spacebar_click_var.set(True)
        self._lbl_alpha_val.config(text="Smoothing Alpha: 0.15  (Lower = smoother, higher = faster response)")
        self._lbl_gain_val.config(text="Cursor Gain: 1.0×  (1.0× = True 1:1 Calibrated Gaze)")
        self._lbl_dpi_val.config(text="Monitor DPI Scale: 96 DPI  (Effective Gain: 1.00× · Zero Distortion)")
        self._lbl_grace_val.config(text="Blink Grace Period: 240 ms  (Freezes cursor position during blinks)")

    def _toggle_mouse_control(self):
        new_state = self.gaze_engine.toggle_mouse()
        self._update_mouse_btn(new_state)

    def _update_mouse_btn(self, is_enabled: bool):
        if is_enabled:
            self._mouse_btn.config(
                text='⏹   Disable Eye Mouse Control (F6)',
                bg=RED, activebackground='#d32f2f', fg=TEXT
            )
            self._sbar_var.set("Mouse Control ACTIVE  ·  Press F6 to stop instantly  ·  Spacebar to click")
        else:
            self._mouse_btn.config(
                text='▶   Enable Eye Mouse Control (F5)',
                bg=CYAN, activebackground='#00b8d4', fg=BLACK
            )
            self._sbar_var.set("Mouse Control Disabled  ·  Ready")

    # =========================================================================
    # Common Actions & Status Bar
    # =========================================================================
    def _on_combo_select(self, label_val: str):
        can_alg = to_canonical_name(label_val)
        self._on_algorithm_select(can_alg)

    def _on_algorithm_select(self, alg: str):
        can_alg = to_canonical_name(alg)
        self.controller.set_pupil_algorithm(can_alg)
        disp_text = to_display_name(can_alg)
        self._alg_var.set(disp_text)
        self._alg_menu_var.set(can_alg)
        self._sbar_var.set(f"Pupil Algorithm set to {can_alg}  ·  Active across all tabs")

    def _toggle_eye_view(self):
        if self._eye_win is not None and self._eye_win.winfo_exists():
            self._close_eye_view()
        else:
            self._show_eye_view()

    def _show_eye_view(self):
        if self._eye_win is not None and self._eye_win.winfo_exists():
            self._eye_win.lift()
            return
        self._eye_win = BinocularEyeViewWindow(self.root, self.controller, on_close_callback=self._on_eye_win_closed)
        cur_alg = self.controller.get_pupil_algorithm()
        self._sbar_var.set(f"Eye View opened  ·  Tracking with {cur_alg}")
        self._action_btn.config(text='⏹   Close Eye View', bg=RED, activebackground='#d32f2f')

    def _close_eye_view(self):
        if self._eye_win is not None and self._eye_win.winfo_exists():
            self._eye_win.destroy()
            self._eye_win = None
            self._on_eye_win_closed()

    def _on_eye_win_closed(self):
        self._eye_win = None
        self._sbar_var.set("Eye View closed  ·  Ready")
        self._action_btn.config(text='👁   Show Dual Eye View', bg=CYAN, activebackground='#00b8d4')

    def _build_statusbar(self):
        tk.Frame(self.root, bg=BORDER, height=1).pack(fill='x')
        bar = tk.Frame(self.root, bg=SURFACE)
        bar.pack(fill='x', side='bottom')
        self._sbar_var = tk.StringVar(value='Ready  ·  Dual-Sensor Eye Tracking System Online')
        tk.Label(bar, textvariable=self._sbar_var, bg=SURFACE, fg=SUBTEXT, font=FONT_SM,
                 anchor='w', padx=10, pady=4).pack(fill='x')

    # =========================================================================
    # Polling Loop (10 Hz)
    # =========================================================================
    def _poll(self):
        st = self.controller.get_status()

        # Left Camera Status
        l_conn = st["left"]["connected"]
        l_fps = st["left"]["fps"]
        l_cid = st["left"]["camera_id"]
        cv, dot = self._dots['cam_left']
        l_on = st["left"].get("enabled", True)
        cv.itemconfig(dot, fill=GREEN if l_conn else (RED if l_on else SUBTEXT))
        self._dot_labels['cam_left'].config(
            text=(f"Camera {l_cid} (Online · {l_fps:.0f} FPS · det {st['left']['det_fps']:.0f} Hz)" if l_conn
                  else f"Camera {l_cid} (Disconnected)" if l_on else "Disabled (single-eye mode)"),
            fg=GREEN if l_conn else (RED if l_on else SUBTEXT)
        )

        # Right Camera Status
        r_conn = st["right"]["connected"]
        r_fps = st["right"]["fps"]
        r_cid = st["right"]["camera_id"]
        cv, dot = self._dots['cam_right']
        r_on = st["right"].get("enabled", True)
        cv.itemconfig(dot, fill=GREEN if r_conn else (RED if r_on else SUBTEXT))
        self._dot_labels['cam_right'].config(
            text=(f"Camera {r_cid} (Online · {r_fps:.0f} FPS · det {st['right']['det_fps']:.0f} Hz)" if r_conn
                  else f"Camera {r_cid} (Disconnected)" if r_on else "Disabled (single-eye mode)"),
            fg=GREEN if r_conn else (RED if r_on else SUBTEXT)
        )

        # Reconcile algorithm if changed externally
        cur_ctrl_alg = st.get("pupil_algorithm", "ArcFit")
        if cur_ctrl_alg != self._alg_menu_var.get():
            self._alg_menu_var.set(cur_ctrl_alg)
            self._alg_var.set(to_display_name(cur_ctrl_alg))

        # Update Gaze Telemetry in Mouse Tab
        telem = self.gaze_engine.get_telemetry()
        cur_mouse_on = telem.get("mouse_enabled", False)

        # Reconcile button text with global F5/F6 hotkey state
        if cur_mouse_on and 'Enable' in self._mouse_btn.cget('text'):
            self._update_mouse_btn(True)
        elif not cur_mouse_on and 'Disable' in self._mouse_btn.cget('text'):
            self._update_mouse_btn(False)

        c_pos = telem.get("cursor_pos")
        c_state = telem.get("cursor_state", "Ready")
        eff_gain = telem.get("effective_gain", 1.5)
        lp = telem.get("left_pupil")
        rp = telem.get("right_pupil")
        mode = telem.get("mode", "No Signal")

        if telem.get("failsafe_tripped") and 'FAIL-SAFE' not in self._sbar_var.get():
            self._sbar_var.set("FAIL-SAFE: physical mouse moved to the top-left corner  ·  Mouse Control disabled")

        self._lbl_gaze_coords.config(
            text=f"Screen Cursor: ({c_pos[0]}, {c_pos[1]})  ·  {telem.get('fps', 0):.0f} FPS" if c_pos else "Screen Cursor: (No Signal)"
        )

        dyn_col = GREEN if ("Tracking" in c_state or "Active" in c_state) else (GOLD if "Blink" in c_state else SUBTEXT)
        self._lbl_dynamics_state.config(
            text=f"Status: {c_state}  ·  Eff Gain: {eff_gain:.2f}×",
            fg=dyn_col
        )

        self._lbl_pupil_l.config(text=f"Left Pupil: (px: {lp[0]:.0f}, py: {lp[1]:.0f})" if lp else "Left Pupil: (No Signal)")
        self._lbl_pupil_r.config(text=f"Right Pupil: (px: {rp[0]:.0f}, py: {rp[1]:.0f})" if rp else "Right Pupil: (No Signal)")

        mode_col = GREEN if 'Fused' in mode else (CYAN if 'Left' in mode else (GOLD if 'Right' in mode else RED))
        self._lbl_gaze_mode.config(text=f"Signal Mode: {mode}", fg=mode_col)

        self.root.after(self.POLL_MS, self._poll)

    def _about(self):
        messagebox.showinfo(
            "About",
            "Binocular Eye Tracking Module\n"
            "Dual-Sensor Precision Eye Tracking, 9-Point Calibration & System Mouse Control."
        )

    def _on_close(self):
        self.gaze_engine.stop()
        self._close_eye_view()
        try:
            if self._calib_win is not None and self._calib_win.winfo_exists():
                self._calib_win.destroy()
        except tk.TclError:
            pass
        self.controller.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
    app = EyeTrackerLauncherApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
