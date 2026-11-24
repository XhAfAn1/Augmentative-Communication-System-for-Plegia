#!/usr/bin/env python3
"""
ui_round.py
===========
Small rounded widgets (plain tkinter, drawn on canvases) so the tracker's Tk windows look like the AAC app's
Flutter menus: rounded cards, buttons, switches, sliders and drop-downs. No extra packages.

Behaviour matches the tk widgets they replace:
  * Switch   - like tk.Checkbutton: toggles its BooleanVar, then calls command(); redraws when the var is set in code
  * Slider   - like tk.Scale(resolution=1): command(value) only when the user drags/clicks and the value changes;
               redraws when the variable is set in code
  * Dropdown - like a readonly ttk.Combobox: picking an item sets the StringVar, then calls command(value)
"""

import tkinter as tk
import tkinter.font as tkfont
from typing import Callable, List, Optional

FONT = "Segoe UI"


def round_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kw):
    """Rounded rectangle as a smoothed polygon (Tk has no rounded rectangle)."""
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


def _parent_bg(parent) -> str:
    try:
        return parent.cget("bg")
    except tk.TclError:
        return "#ffffff"


class Card(tk.Canvas):
    """Rounded card. Put children into `card.inner` (a Frame); the card's height follows its content."""

    def __init__(self, parent, fill="#ffffff", border="#e1e8f2", radius=14, pad=12):
        super().__init__(parent, bg=_parent_bg(parent), highlightthickness=0, bd=0, height=2 * pad)
        self.fill, self.border, self.radius, self.pad = fill, border, radius, pad
        self.inner = tk.Frame(self, bg=fill)
        self._win = self.create_window(pad, pad, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._fit_height)
        self.bind("<Configure>", self._redraw)

    def _fit_height(self, _e=None):
        h = self.inner.winfo_reqheight() + 2 * self.pad
        if int(self.cget("height")) != h:
            self.config(height=h)

    def _redraw(self, e):
        self.itemconfig(self._win, width=max(1, e.width - 2 * self.pad))
        self.delete("bg")
        round_rect(self, 1, 1, e.width - 2, e.height - 2, self.radius, fill=self.fill, outline=self.border,
                   width=1, tags="bg")
        self.tag_lower("bg")


class RoundButton(tk.Canvas):
    """Rounded flat button with hover colour. set_style() changes text / colours (e.g. Record -> Stop)."""

    def __init__(self, parent, text: str, command: Optional[Callable] = None, bg="#e8eff8", fg="#1a2e52",
                 hover="#d6e3f3", font=(FONT, 9), radius=8, padx=12, pady=5, width: Optional[int] = None,
                 border: Optional[str] = None):
        self._font = tkfont.Font(font=font)
        self.padx, self.pady, self.radius, self.fixed_w = padx, pady, radius, width
        h = self._font.metrics("linespace") + 2 * pady
        super().__init__(parent, bg=_parent_bg(parent), highlightthickness=0, bd=0, height=h, cursor="hand2")
        self.command, self.text, self.bg_col, self.fg_col, self.hover_col, self.border = command, text, bg, fg, hover, border
        self._hover, self.enabled = False, True
        self._resize_to_text()
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", self._click)

    def _resize_to_text(self):
        w = self.fixed_w or (self._font.measure(self.text) + 2 * self.padx)
        self.config(width=w)

    def _set_hover(self, on):
        self._hover = on
        self._draw()

    def _click(self, e):
        if self.enabled and 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height() and self.command:
            self.command()

    def set_style(self, text=None, bg=None, fg=None, hover=None):
        new = (text if text is not None else self.text, bg or self.bg_col, fg or self.fg_col, hover or self.hover_col)
        if new == (self.text, self.bg_col, self.fg_col, self.hover_col):
            return                                    # called every frame by status updates: nothing to redraw
        if text is not None and text != self.text:
            self.text = text
            self._resize_to_text()
        self.bg_col, self.fg_col = bg or self.bg_col, fg or self.fg_col
        self.hover_col = hover or self.hover_col
        self._draw()

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4:
            w, h = int(self.cget("width")), int(self.cget("height"))
        fill = self.hover_col if (self._hover and self.enabled) else self.bg_col
        round_rect(self, 1, 1, w - 2, h - 2, self.radius, fill=fill, outline=self.border or fill)
        self.create_text(w / 2, h / 2, text=self.text, font=self._font,
                         fill=self.fg_col if self.enabled else "#9aa7b8")


class Switch(tk.Canvas):
    """Material-style switch (blue track when on), bound to a BooleanVar."""
    W, H = 38, 22

    def __init__(self, parent, variable: tk.BooleanVar, command: Optional[Callable] = None,
                 on="#1565c0", off="#c9d6e8"):
        super().__init__(parent, width=self.W, height=self.H, bg=_parent_bg(parent), highlightthickness=0, bd=0,
                         cursor="hand2")
        self.var, self.command, self.on, self.off = variable, command, on, off
        self._trace = variable.trace_add("write", lambda *a: self._draw())
        self.bind("<ButtonRelease-1>", self._click)
        self.bind("<Destroy>", self._untrace)
        self._draw()

    def _untrace(self, _e=None):
        try:
            self.var.trace_remove("write", self._trace)
        except (tk.TclError, ValueError):
            pass

    def _click(self, _e=None):
        self.var.set(not bool(self.var.get()))
        if self.command:
            self.command()

    def _draw(self):
        self.delete("all")
        val = bool(self.var.get())
        round_rect(self, 1, 1, self.W - 1, self.H - 1, self.H / 2, fill=self.on if val else self.off, outline="")
        r = (self.H - 8) / 2
        cx = self.W - 4 - r if val else 4 + r
        self.create_oval(cx - r, self.H / 2 - r, cx + r, self.H / 2 + r, fill="#ffffff", outline="")


def labeled_switch(parent, text: str, variable: tk.BooleanVar, command=None, fg="#1a2e52", font=(FONT, 9)):
    """A Switch with its label to the right (clicking the label toggles too). Returns the frame."""
    f = tk.Frame(parent, bg=_parent_bg(parent))
    sw = Switch(f, variable, command)
    sw.pack(side="left")
    lbl = tk.Label(f, text=text, font=font, fg=fg, bg=_parent_bg(parent), cursor="hand2")
    lbl.pack(side="left", padx=(6, 0))
    lbl.bind("<ButtonRelease-1>", sw._click)
    return f


class Slider(tk.Canvas):
    """Flutter-like slider: thin rounded track, blue active part, round blue thumb. Integer steps."""
    H = 22

    def __init__(self, parent, from_: int, to: int, variable, command: Optional[Callable] = None, length=160,
                 active="#1565c0", inactive="#dce5f1"):
        super().__init__(parent, width=length, height=self.H, bg=_parent_bg(parent), highlightthickness=0, bd=0,
                         cursor="hand2")
        self.lo, self.hi, self.var, self.command = from_, to, variable, command
        self.active, self.inactive = active, inactive
        self._trace = variable.trace_add("write", lambda *a: self._draw())
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._drag)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<Destroy>", self._untrace)

    def _untrace(self, _e=None):
        try:
            self.var.trace_remove("write", self._trace)
        except (tk.TclError, ValueError):
            pass

    R = 8      # thumb radius = left/right margin of the track

    def _drag(self, e):
        w = max(1, self.winfo_width() - 2 * self.R)
        frac = min(1.0, max(0.0, (e.x - self.R) / w))
        val = int(round(self.lo + frac * (self.hi - self.lo)))
        try:
            cur = int(round(float(self.var.get())))
        except (tk.TclError, ValueError):
            cur = None
        if val != cur:
            self.var.set(val)
            if self.command:
                self.command(val)

    def _draw(self):
        self.delete("all")
        w = self.winfo_width()
        if w < 4:
            w = int(self.cget("width"))
        try:
            v = float(self.var.get())
        except (tk.TclError, ValueError):
            v = self.lo
        frac = 0.0 if self.hi == self.lo else min(1.0, max(0.0, (v - self.lo) / (self.hi - self.lo)))
        y, x0, x1 = self.H / 2, self.R, w - self.R
        x = x0 + frac * (x1 - x0)
        round_rect(self, x0, y - 2, x1, y + 2, 2, fill=self.inactive, outline="")
        if x > x0 + 1:
            round_rect(self, x0, y - 2, x, y + 2, 2, fill=self.active, outline="")
        self.create_oval(x - self.R, y - self.R, x + self.R, y + self.R, fill=self.active, outline="")


class Dropdown(RoundButton):
    """Rounded button showing the StringVar's value + '▾'; a click opens a menu of `values`."""

    def __init__(self, parent, variable: tk.StringVar, values: List[str], command: Optional[Callable] = None,
                 width=120, font=(FONT, 9), **kw):
        self.var, self.values, self.on_pick = variable, list(values), command
        super().__init__(parent, self._label(), command=self._open, width=width, font=font,
                         bg="#ffffff", hover="#f0f5fc", border="#c9d6e8", **kw)
        self._trace = variable.trace_add("write", lambda *a: self.set_style(text=self._label()))
        self.bind("<Destroy>", self._untrace, add="+")

    def _untrace(self, _e=None):
        try:
            self.var.trace_remove("write", self._trace)
        except (tk.TclError, ValueError):
            pass

    def _label(self) -> str:
        return f"{self.var.get()}  ▾"

    def _resize_to_text(self):            # fixed width: long names are cut with "…"
        self.config(width=self.fixed_w or 120)

    def _draw(self):
        w = self.winfo_width() if self.winfo_width() > 4 else int(self.cget("width"))
        full, base = self.text, str(self.var.get())
        while self._font.measure(self.text) > w - 2 * self.padx and len(base) > 1:
            base = base[:-1]
            self.text = f"{base}…  ▾"
        super()._draw()
        self.text = full

    def set_values(self, values: List[str]):
        self.values = list(values)

    def set_enabled(self, on: bool):
        self.enabled = bool(on)
        self.config(cursor="hand2" if on else "arrow")
        self._draw()

    def _open(self):
        m = tk.Menu(self, tearoff=0, font=self._font)
        for v in self.values:
            m.add_command(label=v, command=lambda v=v: self._pick(v))
        m.tk_popup(self.winfo_rootx(), self.winfo_rooty() + self.winfo_height())

    def _pick(self, v: str):
        self.var.set(v)
        if self.on_pick:
            self.on_pick(v)
