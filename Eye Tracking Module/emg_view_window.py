#!/usr/bin/env python3
"""
emg_view_window.py
==================
EMG View: live muscle-sensor signal and the click settings, in the same rounded style as the Eye View.

  * big live value (as in the Arduino Serial Monitor) + scrolling 5 s graph: raw signal (grey), effort (blue),
    threshold, release level (dashed), clicks (green), ignored twitches (grey), long squeezes (orange)
  * EMG click on/off, port (Auto or a COM port), Auto-calibrate
  * Threshold, Release level, Smoothing, Minimum hold, Lockout, Baseline tracking, Long squeeze (+ its time)
All detection runs in emg_sensor.EmgSensor; this window only shows it and changes the saved settings.
"""

import tkinter as tk

from ui_round import Card, RoundButton, Slider, Dropdown, labeled_switch, round_rect
from eye_view_window import (BG_MAIN, BG_SURFACE, BORDER_COL, BTN_BG, BTN_HOVER, CYAN, GOLD, GREEN, RED,
                             TEXT_MAIN, TEXT_MUTED, FONT_SM, FONT_UI, FONT_UI_B, FONT_BTN)
from emg_sensor import DEFAULTS, serial_ports

RAW_COL = "#b9c6d8"
GRID_COL = "#edf1f7"
IGNORED_COL = "#9aa7b8"
PILL = {"ok": ("#e3f3e5", GREEN), "warn": ("#fdeee0", GOLD), "bad": ("#fde7e7", RED), "off": (BTN_BG, TEXT_MUTED)}

# key, title, low, high, step, unit
SLIDERS = (
    ("emg_threshold", "Threshold", 10, 800, 5, ""),
    ("emg_release", "Release level", 0, 600, 5, ""),
    ("emg_smooth_ms", "Smoothing", 20, 500, 10, " ms"),
    ("emg_hold_ms", "Minimum hold", 0, 300, 10, " ms"),
    ("emg_lockout_ms", "Lockout", 0, 1500, 50, " ms"),
)


class EmgViewWindow(tk.Toplevel):
    FRAME_MS = 33
    SIDE = 16
    GRAPH_S = 5.0
    GRAPH_H = 240
    WIN_W = 900                        # about the Eye View's width

    def __init__(self, master, controller, sensor, on_close_callback=None):
        super().__init__(master)
        self.controller, self.sensor, self.on_close_callback = controller, sensor, on_close_callback
        self.title("EMG View — Muscle Sensor Click")
        self.configure(bg=BG_MAIN)
        self.resizable(True, False)
        self._active = True
        self._tick = 0
        self._ymax = 200.0
        self._now = 0.0
        self._cal_seq = sensor.cal_seq
        cfg = controller.cfg
        g = lambda k: cfg.get(k, DEFAULTS[k])
        self.vars = {k: tk.IntVar(value=int(g(k))) for k, *_ in SLIDERS}
        self.vars["emg_long_ms"] = tk.IntVar(value=int(g("emg_long_ms")))
        self.labels = {}
        self.enabled_var = tk.BooleanVar(value=bool(g("emg_enabled")))
        self.baseline_var = tk.BooleanVar(value=bool(g("emg_baseline")))
        self.long_var = tk.BooleanVar(value=bool(g("emg_long_enabled")))
        port = str(g("emg_port") or "auto")
        self.port_var = tk.StringVar(value="Auto" if port.lower() == "auto" else port)
        sensor.add_viewer()                       # the port opens while this window is open (test mode)
        self._build_ui()
        self.update_idletasks()                   # same as the Eye View's _fit_window: cards measure their content
        for c in self._cards:
            c._fit_height()
        self.update_idletasks()
        self.geometry(f"{self.WIN_W}x{self.winfo_reqheight()}")
        self.minsize(self.WIN_W - 160, self.winfo_reqheight())
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(self.FRAME_MS, self._loop)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        P = self.SIDE
        # graph card: live value, status, click indicator, graph
        card = Card(self, fill=BG_SURFACE, border=BORDER_COL, radius=14, pad=14)
        card.pack(fill="x", padx=P, pady=(P, 10))
        top = tk.Frame(card.inner, bg=BG_SURFACE)
        top.pack(fill="x")
        self.value_lbl = tk.Label(top, text="—", font=("Segoe UI", 30, "bold"), fg=TEXT_MAIN, bg=BG_SURFACE,
                                  width=5, anchor="w")
        self.value_lbl.pack(side="left")
        col = tk.Frame(top, bg=BG_SURFACE)
        col.pack(side="left", padx=(4, 0))
        tk.Label(col, text="Live value", font=FONT_SM, fg=TEXT_MUTED, bg=BG_SURFACE).pack(anchor="w")
        self.effort_lbl = tk.Label(col, text="Effort —", font=FONT_UI_B, fg=CYAN, bg=BG_SURFACE)
        self.effort_lbl.pack(anchor="w")
        self.click_pill = RoundButton(top, "Click", None, bg=BTN_BG, fg=TEXT_MUTED, hover=BTN_BG, font=FONT_BTN,
                                      radius=14, padx=16, pady=6)
        self.click_pill.pack(side="right")
        self.status_pill = RoundButton(top, "Sensor off", None, bg=BTN_BG, fg=TEXT_MUTED, hover=BTN_BG,
                                       font=FONT_BTN, radius=14, padx=14, pady=6)
        self.status_pill.pack(side="right", padx=(0, 10))
        for pill in (self.click_pill, self.status_pill):
            pill.config(cursor="arrow")             # read-only pills

        self.graph = tk.Canvas(card.inner, height=self.GRAPH_H, bg=BG_SURFACE, highlightthickness=0, bd=0)
        self.graph.pack(fill="x", pady=(12, 6))
        self.graph.bind("<Configure>", lambda e: self._draw_static())
        legend = tk.Frame(card.inner, bg=BG_SURFACE)
        legend.pack(fill="x")
        for txt, colr in (("Raw", RAW_COL), ("Effort", CYAN), ("Threshold", GOLD), ("Release", GREEN)):
            tk.Label(legend, text="●", font=FONT_SM, fg=colr, bg=BG_SURFACE).pack(side="left")
            tk.Label(legend, text=txt, font=FONT_SM, fg=TEXT_MUTED, bg=BG_SURFACE).pack(side="left", padx=(2, 12))
        self.hint_lbl = tk.Label(legend, text="", font=FONT_SM, fg=GOLD, bg=BG_SURFACE)
        self.hint_lbl.pack(side="right")

        # bar: EMG click switch, port, auto-calibrate
        bar = tk.Frame(self, bg=BG_MAIN)
        bar.pack(fill="x", padx=P, pady=(0, 10))
        labeled_switch(bar, "EMG click", self.enabled_var, self._on_enabled, fg=TEXT_MAIN,
                       font=FONT_UI_B).pack(side="left")
        tk.Label(bar, text="Port", font=FONT_UI, fg=TEXT_MUTED, bg=BG_MAIN).pack(side="left", padx=(28, 8))
        self.port_dd = Dropdown(bar, self.port_var, ["Auto"] + serial_ports(), command=self._on_port, width=110,
                                font=FONT_UI, pady=5)
        self.port_dd.pack(side="left")
        self.cal_btn = RoundButton(bar, "Auto-calibrate", self._on_calibrate, bg=BTN_BG, fg=TEXT_MAIN,
                                   hover=BTN_HOVER, font=FONT_BTN, radius=10, padx=14, pady=6)
        self.cal_btn.pack(side="right")
        self.cal_lbl = tk.Label(bar, text="", font=FONT_UI, fg=TEXT_MUTED, bg=BG_MAIN)
        self.cal_lbl.pack(side="right", padx=12)

        # settings card
        sc = Card(self, fill=BG_SURFACE, border=BORDER_COL, radius=14, pad=16)
        sc.pack(fill="x", padx=P, pady=(0, P))
        self._cards = [card, sc]
        grid = tk.Frame(sc.inner, bg=BG_SURFACE)
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=1, uniform="col")
        grid.columnconfigure(1, weight=1, uniform="col")
        for i, (key, title, lo, hi, step, unit) in enumerate(SLIDERS):
            r, c = i // 2, i % 2
            self._slider_row(grid, key, title, lo, hi, step, unit).grid(
                row=r, column=c, sticky="ew", padx=(0, 20) if c == 0 else (20, 0), pady=5)
        tk.Frame(sc.inner, bg=BORDER_COL, height=1).pack(fill="x", pady=12)
        row = tk.Frame(sc.inner, bg=BG_SURFACE)
        row.pack(fill="x")
        row.columnconfigure(0, weight=1, uniform="col")
        row.columnconfigure(1, weight=1, uniform="col")
        labeled_switch(row, "Baseline tracking", self.baseline_var,
                       lambda: self._save(emg_baseline=bool(self.baseline_var.get())),
                       fg=TEXT_MAIN, font=FONT_UI).grid(row=0, column=0, sticky="w")
        labeled_switch(row, "Long squeeze: gaze mouse on/off", self.long_var,
                       lambda: self._save(emg_long_enabled=bool(self.long_var.get())),
                       fg=TEXT_MAIN, font=FONT_UI).grid(row=0, column=1, sticky="w", padx=(20, 0))
        self._slider_row(row, "emg_long_ms", "Long squeeze", 500, 3000, 100, " ms").grid(
            row=1, column=1, sticky="ew", padx=(20, 0), pady=(10, 0))
        for k in self.vars:
            self._update_label(k)

    def _slider_row(self, parent, key, title, lo, hi, step, unit):
        f = tk.Frame(parent, bg=BG_SURFACE)
        tk.Label(f, text=title, font=FONT_UI, fg=TEXT_MAIN, bg=BG_SURFACE, width=12, anchor="w").pack(side="left")
        st = dict(bg=BTN_BG, fg=TEXT_MAIN, hover=BTN_HOVER, font=FONT_BTN, radius=12, padx=0, pady=3, width=26)
        RoundButton(f, "−", lambda: self._step(key, -step, lo, hi), **st).pack(side="left")
        Slider(f, lo, hi, self.vars[key], lambda v: self._changed(key), length=120, active=CYAN).pack(
            side="left", fill="x", expand=True, padx=6)
        RoundButton(f, "+", lambda: self._step(key, step, lo, hi), **st).pack(side="left")
        lbl = tk.Label(f, text="", font=FONT_UI, fg=CYAN, bg=BG_SURFACE, width=8, anchor="e")
        lbl.pack(side="left", padx=(6, 0))
        self.labels[key] = (lbl, unit)
        return f

    # ------------------------------------------------------------------ settings
    def _save(self, **kw):
        self.controller.set_mouse_settings(**kw)

    def _update_label(self, key):
        lbl, unit = self.labels[key]
        lbl.config(text=f"{self.vars[key].get()}{unit}")

    def _step(self, key, d, lo, hi):
        v = self.vars[key]
        v.set(max(lo, min(hi, v.get() + d)))
        self._changed(key)

    def _changed(self, key):
        thr, rel = self.vars["emg_threshold"], self.vars["emg_release"]
        if key == "emg_threshold" and rel.get() > thr.get() - 5:        # release must stay below the threshold
            rel.set(max(0, thr.get() - 5))
        if key == "emg_release" and rel.get() > thr.get() - 5:
            thr.set(rel.get() + 5)
        kw = {key: int(self.vars[key].get())}
        if key in ("emg_threshold", "emg_release"):
            kw.update(emg_threshold=int(thr.get()), emg_release=int(rel.get()))
            for k in ("emg_threshold", "emg_release"):
                self._update_label(k)
        self._update_label(key)
        self._save(**kw)

    def _on_enabled(self):
        self._save(emg_enabled=bool(self.enabled_var.get()))

    def _on_port(self, v):
        self._save(emg_port="auto" if v == "Auto" else v)
        self.sensor.reconnect()

    def _on_calibrate(self):
        if self.sensor.calibration_phase()[0]:
            self.sensor.cancel_calibration()
        elif self.sensor.conn == "connected":
            self.sensor.start_calibration()

    # ------------------------------------------------------------------ graph
    def _draw_static(self):
        g = self.graph
        g.delete("static")
        w, h = g.winfo_width(), self.GRAPH_H
        round_rect(g, 0, 0, w - 1, h - 1, 10, fill="#fbfcfe", outline=GRID_COL, tags="static")
        for i in range(1, 5):
            y = h * i / 5
            g.create_line(8, y, w - 8, y, fill=GRID_COL, tags="static")
        g.tag_lower("static")
        if not g.find_withtag("raw"):
            g.create_line(0, 0, 0, 0, fill=RAW_COL, width=1, tags="raw")
            g.create_line(0, 0, 0, 0, fill=CYAN, width=2, tags="env")
            g.create_line(0, 0, 0, 0, fill=GOLD, width=2, tags="thr")
            g.create_line(0, 0, 0, 0, fill=GREEN, width=2, dash=(6, 4), tags="rel")
            for tag, colr in (("thr_t", GOLD), ("rel_t", GREEN)):
                g.create_text(0, 0, text="", font=FONT_SM, fill=colr, anchor="se", tags=tag)
            g.create_text(10, 8, text="", font=FONT_SM, fill=TEXT_MUTED, anchor="nw", tags="ymax")

    def _draw_graph(self):
        g = self.graph
        w, h = g.winfo_width(), self.GRAPH_H
        if w < 50:
            return
        pts, marks, now = self.sensor.graph(self.GRAPH_S)
        self._now = now
        thr, rel, _ = self.sensor.lines()
        top = max([thr * 1.3, 100.0] + [p[1] for p in pts])
        # the scale grows at once and shrinks slowly (no jumping)
        self._ymax = top if top > self._ymax else self._ymax * 0.98 + top * 0.02
        m, x0, x1 = 6, 8, w - 8
        ys = lambda v: h - m - (min(v, self._ymax) / self._ymax) * (h - 2 * m)
        xs = lambda t: x1 - (now - t) / self.GRAPH_S * (x1 - x0)
        if len(pts) >= 2:
            raw, env = [], []
            for t, r, e in pts:
                x = xs(t)
                raw += (x, ys(r))
                env += (x, ys(e))
            g.coords("raw", *raw)
            g.coords("env", *env)
        else:
            g.coords("raw", 0, 0, 0, 0)
            g.coords("env", 0, 0, 0, 0)
        g.coords("thr", x0, ys(thr), x1, ys(thr))
        g.coords("rel", x0, ys(rel), x1, ys(rel))
        g.coords("thr_t", x1 - 4, ys(thr) - 2)
        g.itemconfig("thr_t", text=f"{thr:.0f}")
        g.coords("rel_t", x1 - 4, ys(rel) - 2)
        g.itemconfig("rel_t", text=f"{rel:.0f}")
        g.itemconfig("ymax", text=f"{self._ymax:.0f}")
        g.delete("mark")
        for t, kind in marks:
            x = xs(t)
            colr = {"click": GREEN, "long": GOLD}.get(kind, IGNORED_COL)
            g.create_line(x, m, x, h - m, fill=colr, width=2 if kind != "ignored" else 1,
                          dash=() if kind != "ignored" else (2, 3), tags="mark")
            if kind != "ignored":
                g.create_oval(x - 4, m, x + 4, m + 8, fill=colr, outline="", tags="mark")

    # ------------------------------------------------------------------ loop
    def _loop(self):
        if not self._active:
            return
        try:
            self._tick += 1
            s = self.sensor
            self._draw_graph()
            if self._tick % 3 == 0:                    # numbers 10x per second (readable)
                self.value_lbl.config(text="—" if s.raw is None or s.conn != "connected" else f"{s.raw:.0f}")
                self.effort_lbl.config(text="Effort —" if s.conn != "connected" else f"Effort {s.env:.0f}")
                kind = ("ok" if s.conn == "connected" and not s.guard else
                        "off" if s.conn == "off" else
                        "bad" if s.conn in ("busy", "no_pyserial") else "warn")
                bg, fg = PILL[kind]
                self.status_pill.set_style(text=s.text(), bg=bg, fg=fg, hover=bg)
                self._update_hint()
                self._update_calibration()
            # click indicator: green for 300 ms after a click / long squeeze
            age = self._now - s.last_click_t
            lit = age < 0.3
            self.click_pill.set_style(text=f"Click  {s.clicks}", bg=PILL["ok"][0] if lit else BTN_BG,
                                      fg=GREEN if lit else TEXT_MUTED, hover=PILL["ok"][0] if lit else BTN_BG)
            # keep the switch in step with the app's Settings switch
            en = bool(self.controller.cfg.get("emg_enabled", False))
            if bool(self.enabled_var.get()) != en:
                self.enabled_var.set(en)
            if self._tick % 60 == 0:                   # ports plugged in / out
                self.port_dd.set_values(["Auto"] + serial_ports())
        except tk.TclError:
            return
        self.after(self.FRAME_MS, self._loop)

    def _update_hint(self):
        cfg = self.controller.cfg
        if not bool(cfg.get("mouse_spacebar_click", True)):
            txt, colr = "Spacebar Select is off: EMG clicks do nothing", GOLD
        elif not bool(cfg.get("emg_enabled", False)):
            txt, colr = "Test mode: clicks only show here", TEXT_MUTED
        else:
            txt, colr = "A squeeze presses Space", TEXT_MUTED
        self.hint_lbl.config(text=txt, fg=colr)

    def _update_calibration(self):
        s = self.sensor
        phase, left = s.calibration_phase()
        if phase:
            self.cal_lbl.config(text=("Relax…" if phase == "relax" else "Squeeze 3 times…") + f"  {left:.0f} s",
                                fg=CYAN)
            self.cal_btn.set_style(text="Cancel", bg=RED, fg="#ffffff", hover="#b71c1c")
            return
        self.cal_btn.set_style(text="Auto-calibrate", bg=BTN_BG, fg=TEXT_MAIN, hover=BTN_HOVER)
        if self.cal_btn.enabled != (s.conn == "connected"):
            self.cal_btn.enabled = s.conn == "connected"
            self.cal_btn._draw()
        if s.cal_seq != self._cal_seq:                 # a calibration just finished
            self._cal_seq = s.cal_seq
            self.cal_lbl.config(text=s.cal_msg, fg=GREEN if s.cal_ok else RED)
            if s.cal_ok:
                cfg = self.controller.cfg
                for k in ("emg_threshold", "emg_release"):
                    self.vars[k].set(int(cfg.get(k, DEFAULTS[k])))
                    self._update_label(k)

    # ------------------------------------------------------------------ lifecycle
    def _on_close(self):
        if callable(self.on_close_callback):
            self.on_close_callback()
        self.destroy()

    def destroy(self):
        if self._active:
            self._active = False
            self.sensor.cancel_calibration()
            self.sensor.remove_viewer()
        super().destroy()
