#!/usr/bin/env python3
"""
finetune_overlay.py - Level 2 calibration (fine-tuning), ~45 s. It does not replace the calibration: it UPDATES the
current map (the reference) a little, keeping all old and new data and weighting each data point by consistency.
  1. 9 dots at random positions (one per screen cell, so they land between the old grid dots)
  2. 2 vertical smooth-pursuit paths (the full calibration has only horizontal and diagonal ones)
  3. slip = the current map's median miss on the new dots (headset moved since calibration); new data is shifted
     by it into the reference frame
  4. [E1/E2] dots inconsistent with the current map / with all other data incl. the paths get one more look;
     every look is kept as its own data point
  5. [S2] every data point (old + new) gets a weight from its consistency with all others (soft_weights)
  6. [S3] refit on everything with those weights, kept near the current map (PRIOR_STRENGTH)
  7. safety gate: saved only if the new dots (each held out) are predicted at least as well as by the current map
     and the old dots do not get more than 10 % worse; otherwise the map is kept and only the slip is learned.
     The slip correction moves SLIP_LEARN_RATE of the way toward the new measurement each session.
"""
import os
import sys
import math
import time
import random
import shutil
import tkinter as tk
from typing import Optional, Callable, Dict, Any, List, Tuple
import numpy as np

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from binocular_calibrator import BinocularCalibrator
from binocular_calibration_reporter import generate_binocular_chart
from binocular_calibration_overlay import GazePointer, pointer_enabled

BG, CYAN, GOLD, GREEN, RED, WHITE, MUTED = "#080c14", "#00e5ff", "#e3b341", "#3fb950", "#f85149", "#ffffff", "#8b949e"
CALIB_FILE = os.path.join(_THIS_DIR, "binocular_calibration.json")
Point = Tuple[float, float]


class FineTuneOverlay(tk.Toplevel):
    APPROACH_S, COLLECT_S = 1.3, 1.1
    BREAK_S, TRACK_S = 1.3, 5.0
    MIN_SAMPLES = 8
    MAX_JITTER_CAM_PX = 3.0
    WORSE_TOLERANCE = 1.10          # old dots may not get more than 10 % worse

    def __init__(self, master, controller, on_done: Optional[Callable[[str], None]] = None):
        super().__init__(master)
        self.controller, self.on_done = controller, on_done
        self.attributes("-fullscreen", True)
        self.attributes("-topmost", True)
        self.configure(bg=BG)
        self.sw, self.sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.cv = tk.Canvas(self, width=self.sw, height=self.sh, bg=BG, highlightthickness=0, cursor="none")
        self.cv.pack(fill="both", expand=True)
        self.bind("<Escape>", lambda e: self._end("Fine-tuning cancelled"))
        self.focus_force()
        self._active = True

        self.old = BinocularCalibrator()
        ok = self.old.load(CALIB_FILE)
        self.old_pursuit = self.old.load_pursuit(CALIB_FILE)
        if not ok or not self.old.anchors or not self.old_pursuit:
            self.after(10, lambda: self._end("Fine-tuning needs a full 9-point calibration made with this version "
                                             "(the current one has no saved training data). Run a full calibration first."))
            return
        rnd = random.Random()
        cells = [(c, r) for r in range(3) for c in range(3)]
        rnd.shuffle(cells)
        self.dots = [(int((0.06 + (c + rnd.uniform(0.15, 0.85)) * 0.88 / 3) * self.sw),
                      int((0.06 + (r + rnd.uniform(0.15, 0.85)) * 0.88 / 3) * self.sh)) for c, r in cells]
        self.paths = [((int(0.3 * self.sw), int(0.06 * self.sh)), (int(0.3 * self.sw), int(0.94 * self.sh))),
                      ((int(0.7 * self.sw), int(0.94 * self.sh)), (int(0.7 * self.sw), int(0.06 * self.sh)))]
        self.new_l: List[Optional[Point]] = [None] * len(self.dots)
        self.new_r: List[Optional[Point]] = [None] * len(self.dots)
        self.records: List[Dict[str, Any]] = []
        self.dot_q: Dict[int, Dict[str, float]] = {}      # chart: accepted-frame share per new dot and eye
        self.dot_frames: Dict[int, List[Tuple[Optional[Point], Optional[Point]]]] = {}   # chart: fixation jitter
        self.rc_q: Dict[int, Dict[str, float]] = {}
        self.queue = list(range(len(self.dots)))   # dots still to show
        self.retries: Dict[int, int] = {}
        self.stage, self.phase, self.idx, self.t0 = "DOTS", "APPROACH", self.queue[0], time.time()
        self.buf = {"left": [], "right": []}
        self.seq = (-1, -1)
        self.msg = ""
        self.pointer = GazePointer(controller, self.sw, self.sh)   # live gaze through the current map (display only)
        self._tick()

    # ------------------------------------------------------------------ samples
    def _eyes(self):
        sl, sr = self.controller.get_samples()
        new = (sl.seq, sr.seq) != self.seq
        self._new_lr = (sl.seq != self.seq[0], sr.seq != self.seq[1])   # per eye: store each camera frame once
        self.seq = (sl.seq, sr.seq)
        lp = sl.pupil if sl.connected and sl.pupil is not None and sl.conf > 0.15 else None
        rp = sr.pupil if sr.connected and sr.pupil is not None and sr.conf > 0.15 else None
        return new, lp, (sl.conf if lp else 0.0), rp, (sr.conf if rp else 0.0)

    @staticmethod
    def _median(buf) -> Tuple[Optional[Point], float]:
        if len(buf) < FineTuneOverlay.MIN_SAMPLES:
            return None, 99.0
        a = BinocularCalibrator._tukey_filter(np.array(buf, float))
        m = np.median(a, axis=0)
        return (float(m[0]), float(m[1])), float(np.mean(np.hypot(*(a - m).T)))

    def _eyes_on(self):
        return [e for e in ("left", "right") if self.controller.is_eye_enabled(e)] \
            if hasattr(self.controller, "is_eye_enabled") else ["left", "right"]

    # ------------------------------------------------------------------ loop
    def _tick(self):
        if not self._active:
            return
        t = time.time()
        el = t - self.t0
        new, lp, lc, rp, rc = self._eyes()
        if new and pointer_enabled(self.controller):
            self.pointer.update(self.old.predict(lp, rp, lc, rc)[0])
        if self.stage in ("DOTS", "RECHECK"):
            if self.phase == "APPROACH" and el >= self.APPROACH_S:
                self.phase, self.t0, self.buf = "COLLECT", t, {"left": [], "right": [], "pair": []}
            elif self.phase == "COLLECT":
                if new:
                    if lp is not None and self._new_lr[0]:
                        self.buf["left"].append(lp)
                    if rp is not None and self._new_lr[1]:
                        self.buf["right"].append(rp)
                    self.buf["pair"].append((lp, rp))
                if el >= self.COLLECT_S:
                    self._finish_dot(t)
        elif self.stage == "PATHS":
            p0, p1 = self.paths[self.idx]
            if self.phase == "APPROACH" and el >= self.BREAK_S:
                self.phase, self.t0 = "TRACK", t
            elif self.phase == "TRACK":
                f = min(1.0, el / self.TRACK_S)
                tgt = (p0[0] + (p1[0] - p0[0]) * f, p0[1] + (p1[1] - p0[1]) * f)
                if new:
                    v = ((p1[0] - p0[0]) / self.TRACK_S, (p1[1] - p0[1]) / self.TRACK_S)
                    self.records.append({"path_idx": 100 + self.idx, "elapsed_sec": el, "target": tgt,
                                         "compensated_target": tgt, "target_velocity": v,
                                         "left_pupil": lp, "right_pupil": rp, "left_conf": lc, "right_conf": rc,
                                         "is_blink": lp is None and rp is None})
                if el >= self.TRACK_S:
                    self.idx += 1
                    self.phase, self.t0 = "APPROACH", t
                    if self.idx >= len(self.paths):
                        self.stage = "PROCESS"
                        self._render(0.0)
                        self.after(60, self._process)
                        return
        self._render(el)
        self.after(16, self._tick)

    def _finish_dot(self, t):
        if self.stage == "RECHECK":                                # store the re-check reading of item k
            k = self.idx
            self.rc[k] = tuple(self._median(self.buf[e])[0] if e in self._eyes_on() else None for e in ("left", "right"))
            self.rc_q[k] = self._quality()
            self.queue.pop(0)
            if self.queue:
                self.idx, self.phase, self.t0 = self.queue[0], "APPROACH", t
            else:
                self.stage = "PROCESS"
                self.after(60, self._process_after_recheck)
            return
        i = self.idx
        ok = True
        for eye in self._eyes_on():
            m, jit = self._median(self.buf[eye])
            if m is None or jit > self.MAX_JITTER_CAM_PX:
                ok = False
            (self.new_l if eye == "left" else self.new_r)[i] = m
        self.dot_q[i], self.dot_frames[i] = self._quality(), list(self.buf.get("pair", []))
        if not ok and self.retries.get(i, 0) < 1 and self.stage == "DOTS":
            self.retries[i] = 1
            self.queue.append(i)                                   # unstable: show it again at the end
        self.queue.pop(0)
        if self.queue:
            self.idx, self.phase, self.t0 = self.queue[0], "APPROACH", t
        else:
            self.stage, self.idx, self.phase, self.t0 = "PATHS", 0, "APPROACH", t

    def _quality(self) -> Dict[str, float]:
        """Share of the expected camera frames (30 fps x collect time) with an accepted pupil, per eye."""
        n = self.COLLECT_S * 30.0
        return {e: round(min(1.0, len(self.buf.get(e, [])) / n), 3) for e in ("left", "right")}

    # ------------------------------------------------------------------ analysis
    def _fused_raw(self, cal: BinocularCalibrator, pl, pr, models=None) -> Optional[Point]:
        """Mean of the available eyes' predictions, without the slip offset (same rule for old and new models)."""
        ps = []
        for eye, p in (("left", pl), ("right", pr)):
            if p is None:
                continue
            W, norm, tps = (models[eye] if models else
                            ((cal.w_left, cal.norm_left, cal.tps_left) if eye == "left" else (cal.w_right, cal.norm_right, cal.tps_right)))
            if W is None and tps is None:
                continue
            q = cal._predict_eye(p, W, norm, tps)
            if q is not None:
                ps.append(q)
        return None if not ps else (float(np.mean([q[0] for q in ps])), float(np.mean([q[1] for q in ps])))

    def _process(self):
        """Slip, consistency of the new dots [E1] and of all dots against the paths [E2] -> extra looks if needed."""
        old = self.old
        raw = [self._fused_raw(old, a, b) for a, b in zip(self.new_l, self.new_r)]
        miss = [(r[0] - d[0], r[1] - d[1]) for r, d in zip(raw, self.dots) if r is not None]
        if len(miss) < 4:
            return self._end("Fine-tuning failed: too little pupil signal on the new dots")
        self.slip = tuple(float(v) for v in np.median(np.array(miss), axis=0))
        res = [None if r is None else math.hypot(r[0] - self.slip[0] - d[0], r[1] - self.slip[1] - d[1])
               for r, d in zip(raw, self.dots)]
        vals = [v for v in res if v is not None]
        lim = max(BinocularCalibrator.ANCHOR_FLAG_MIN_PX, BinocularCalibrator.ANCHOR_FLAG_RATIO * float(np.median(vals)))
        e1_bad = [i for i, v in enumerate(res) if v is not None and v > lim]
        print(f"[Fine-tune] slip {self.slip[0]:+.0f}, {self.slip[1]:+.0f} px; new dots vs current model after slip: "
              f"{', '.join('-' if v is None else f'{v:.0f}' for v in res)} (limit {lim:.0f})")

        # all data in the OLD (reference) frame: new targets and paths shifted by the slip. Nothing is excluded.
        sx, sy = self.slip
        a = old.anchors
        self.n_old = len(a["targets"])
        self.tg = [tuple(t) for t in a["targets"]] + [(d[0] + sx, d[1] + sy) for d in self.dots]
        self.L = [None if p is None else tuple(p) for p in a["left"]] + list(self.new_l)
        self.R = [None if p is None else tuple(p) for p in a["right"]] + list(self.new_r)
        newrec = []
        for r in self.records:
            r2 = dict(r)
            r2["target"] = (r["target"][0] + sx, r["target"][1] + sy)
            r2["compensated_target"] = r2["target"]
            r2["raw_fused"] = self._fused_raw(old, r.get("left_pupil"), r.get("right_pupil"))
            newrec.append(r2)
        self.pursuit = list(self.old_pursuit) + newrec

        # E2 on all static dots (old + new): clearly inconsistent ones get one more look
        flagged_old, flagged_new = set(), set(e1_bad)
        for eye in self._eyes_on():
            S = self.L if eye == "left" else self.R
            loo = old.anchor_loo(S, self.tg, self.pursuit, eye)
            bad = old.flag_anchors(loo)
            if bad:
                print(f"[Fine-tune] path check {eye}: dots {bad} disagree ({', '.join(f'{loo[i]:.0f}' for i in bad)} px)")
            flagged_old.update(i for i in bad if i < self.n_old)
            flagged_new.update(i - self.n_old for i in bad if i >= self.n_old)
        self.recheck_list = [("new", i) for i in sorted(flagged_new)] + [("old", i) for i in sorted(flagged_old)]
        self.rc: Dict[int, Tuple[Optional[Point], Optional[Point]]] = {}
        if self.recheck_list:
            self.stage, self.phase, self.t0 = "RECHECK", "APPROACH", time.time()
            self.queue = list(range(len(self.recheck_list)))
            self.idx = self.queue[0]
            self._tick()
        else:
            self._process_after_recheck()

    def _recheck_target(self, k):
        kind, i = self.recheck_list[k]
        if kind == "new":
            return self.dots[i]
        t = self.old.anchors["targets"][i]                          # an old dot is shown where it was
        return int(t[0]), int(t[1])

    def _process_after_recheck(self):
        """[S2] every extra look becomes one more data point (its own target in the reference frame); nothing is
        replaced. Then every data point gets a weight from how consistent it is with all the others."""
        sx, sy = self.slip
        self.extra_q: List[Dict[str, float]] = []
        for k, (kind, i) in enumerate(getattr(self, "recheck_list", [])):
            rl, rr = getattr(self, "rc", {}).get(k, (None, None))
            if rl is None and rr is None:
                continue
            t = self.dots[i] if kind == "new" else self.old.anchors["targets"][i]
            self.tg.append((t[0] + sx, t[1] + sy))                  # seen now -> slip applies like the new dots
            self.L.append(rl if "left" in self._eyes_on() else None)
            self.R.append(rr if "right" in self._eyes_on() else None)
            self.extra_q.append(self.rc_q.get(k, {}))
            print(f"[Fine-tune] extra look at {kind} dot {i + 1} added as its own data point")
        # weights: old points keep the consistency weights from their calibration; new points count fully.
        # (Re-weighting across sessions was tested and hurt: new dots looked "inconsistent" only because the headset
        # sat differently, new-dot error 57 -> 66 px.)
        old_w = (self.old.anchors.get("weights") or {}) if self.old.anchors else {}
        self.weights = {}
        for eye in self._eyes_on():
            S = self.L if eye == "left" else self.R
            w = [x if x is not None else 1.0 for x in old_w.get(eye, [])][:self.n_old]
            self.weights[eye] = w + [1.0] * (len(S) - len(w))
        self._fit_and_gate()

    def _held_out(self, cal: BinocularCalibrator, j: int) -> Optional[Point]:
        """Fused raw prediction of data point j by a model (weights + reference as in cal) fitted without it."""
        models = {}
        for eye in ("left", "right"):
            S = list(self.L if eye == "left" else self.R)
            S[j] = None
            f = cal._fit_eye_aligned(S, self.tg, self.pursuit, eye)
            models[eye] = (None, (0, 0, 1, 1), None) if f is None else (f[0], f[1], None)
        return self._fused_raw(cal, self.L[j], self.R[j], models)

    def _fit_and_gate(self):
        """[S3] map v(n+1) = refit on all data with consistency weights, kept near the current map (reference).
        Saved if it predicts the new dots (each held out) at least as well as the current map and the old dots
        do not get more than 10 % worse; otherwise only the slip is learned."""
        old = self.old
        n_old = self.n_old
        new_idx = [n_old + i for i in range(len(self.dots)) if self.L[n_old + i] is not None or self.R[n_old + i] is not None]
        if len(new_idx) < 4:
            return self._apply_slip_only("too few usable new dots")
        sx, sy = self.slip
        tmp = BinocularCalibrator(old.screen_w, old.screen_h)
        tmp.anchor_w = self.weights
        tmp.prior = {"left": (old.w_left, old.norm_left), "right": (old.w_right, old.norm_right)}
        e_old, e_new, landings = [], [], []
        for j in new_idx:
            d = self.dots[j - n_old]
            r = self._fused_raw(old, self.L[j], self.R[j])
            q = self._held_out(tmp, j)
            if r is None or q is None:
                continue
            e_old.append(math.hypot(r[0] - sx - d[0], r[1] - sy - d[1]))
            e_new.append(math.hypot(q[0] - sx - d[0], q[1] - sy - d[1]))
            landings.append({"target_idx": j - n_old, "target": d, "gaze": (q[0] - sx, q[1] - sy), "error_px": e_new[-1]})
        o_before, o_after = [], []
        for j in range(n_old):
            t = self.tg[j]
            b, q = self._fused_raw(old, self.L[j], self.R[j]), None
            if b is None:
                continue
            S_old = {e: [None if p is None else tuple(p) for p in old.anchors[e]] for e in ("left", "right")}
            models = {}
            for eye in ("left", "right"):
                S = list(S_old[eye])
                S[j] = None
                f = old._fit_eye_aligned(S, [tuple(x) for x in old.anchors["targets"]], self.old_pursuit, eye)
                models[eye] = (None, (0, 0, 1, 1), None) if f is None else (f[0], f[1], None)
            b = self._fused_raw(old, self.L[j], self.R[j], models)
            q = self._held_out(tmp, j)
            if b is not None and q is not None:
                o_before.append(math.hypot(b[0] - t[0], b[1] - t[1]))
                o_after.append(math.hypot(q[0] - t[0], q[1] - t[1]))
        mo, mn = float(np.mean(e_old)), float(np.mean(e_new))
        ob, oa = float(np.median(o_before)), float(np.median(o_after))
        print(f"[Fine-tune] new dots: current map {mo:.0f} px -> updated map {mn:.0f} px; "
              f"old dots (held out) {ob:.0f} -> {oa:.0f} px")
        if mn <= mo and oa <= ob * self.WORSE_TOLERANCE:
            new = BinocularCalibrator(old.screen_w, old.screen_h)
            # chart/report: the validation points are the new dots, each predicted by a map fitted WITHOUT it
            if not new.fit(self.L, self.R, self.tg, pursuit_records=self.pursuit,
                           static_quality=self._quality_lists(), static_val_records=landings,
                           anchor_weights=self.weights, reference=old):
                return self._apply_slip_only("refit failed")
            new.offset = self._learned_offset()
            m = new.metrics
            self._add_jitter(new, m)
            m["finetune"] = {"new_dots_before_px": round(mo, 1), "new_dots_after_px": round(mn, 1),
                             "old_dots_before_px": round(ob, 1), "old_dots_after_px": round(oa, 1),
                             "slip_px": [round(sx, 1), round(sy, 1)], "offset_px": [round(v, 1) for v in new.offset]}
            alg = self.controller.get_pupil_algorithm() if hasattr(self.controller, "get_pupil_algorithm") else "ArcFit"
            folder = f"{time.strftime('%Y%m%d_%H%M%S')}_{alg}_finetune"
            d = os.path.join(_THIS_DIR, "data", "calibrations", folder)
            new.save_archive(d, alg, root_dir=_THIS_DIR)
            try:
                generate_binocular_chart(new.metrics, os.path.join(d, "calibration_chart.png"), new.pursuit_records)
                shutil.copy2(os.path.join(d, "calibration_chart.png"), os.path.join(_THIS_DIR, "binocular_calibration_chart.png"))
            except Exception as e:
                print(f"[Fine-tune] chart failed: {e}")
            self._end(f"Map updated: new dots {mo:.0f} -> {mn:.0f} px, old dots {ob:.0f} -> {oa:.0f} px, "
                      f"slip ({sx:+.0f}, {sy:+.0f}) px learned. Saved data/calibrations/{folder}")
        else:
            why = "it would not improve the new dots" if mn > mo else "it would make the old dots worse"
            self._apply_slip_only(f"map update skipped: {why} (new dots {mo:.0f} -> {mn:.0f} px, "
                                  f"old dots {ob:.0f} -> {oa:.0f} px)")

    def _quality_lists(self) -> Dict[str, List[float]]:
        """Accepted-frame share per data point (old points from their calibration, then new dots, then extra looks)."""
        out = {}
        for e in ("left", "right"):
            q = list(self.old.metrics.get(f"static_quality_{e}") or [])[:self.n_old]
            q += [0.0] * (self.n_old - len(q))
            q += [self.dot_q.get(i, {}).get(e, 0.0) for i in range(len(self.dots))]
            q += [x.get(e, 0.0) for x in getattr(self, "extra_q", [])]
            out[e] = q
        return out

    def _add_jitter(self, cal: BinocularCalibrator, m: Dict[str, Any]):
        """Screen-space fixation jitter on the new dots through the new map: mean distance of each frame's
        prediction from the dot's median prediction (per eye and fused), as in the full calibration report."""
        jl, jr, jf = [], [], []
        for frames in self.dot_frames.values():
            for k, out in ((0, jl), (1, jr), (None, jf)):
                pts = [self._fused_raw(cal, *(fr if k is None else ((fr[0], None) if k == 0 else (None, fr[1]))))
                       for fr in frames]
                pts = np.array([p for p in pts if p is not None], float)
                if len(pts) >= self.MIN_SAMPLES:
                    out.append(float(np.mean(np.hypot(*(pts - np.median(pts, axis=0)).T))))
        r2 = lambda v: round(float(v), 2)
        m.update({"jitters": [r2(v) for v in jf], "jitters_left": [r2(v) for v in jl], "jitters_right": [r2(v) for v in jr],
                  "mean_jitter": r2(np.mean(jf)) if jf else None, "mean_jitter_left": r2(np.mean(jl)) if jl else None,
                  "mean_jitter_right": r2(np.mean(jr)) if jr else None})

    def _learned_offset(self) -> Tuple[float, float]:
        """[S3] the slip correction moves SLIP_LEARN_RATE of the way toward the newly measured slip."""
        ox, oy = getattr(self.old, "offset", (0.0, 0.0))
        k = BinocularCalibrator.SLIP_LEARN_RATE
        return (ox + k * (-self.slip[0] - ox), oy + k * (-self.slip[1] - oy))

    def _apply_slip_only(self, why: str):
        self.old.offset = self._learned_offset()
        try:
            self.old.save(CALIB_FILE)
        except Exception as e:
            print(f"[Fine-tune] save failed: {e}")
        self._end(f"Map kept ({why}); slip learned, correction now ({self.old.offset[0]:+.0f}, {self.old.offset[1]:+.0f}) px")

    def _end(self, msg: str):
        if not self._active:
            return
        self._active = False
        print(f"[Fine-tune] {msg}")
        if callable(self.on_done):
            self.on_done(msg)
        self.destroy()

    # ------------------------------------------------------------------ drawing
    def _render(self, el):
        c = self.cv
        c.delete("all")
        if self.stage == "PROCESS":
            c.create_text(self.sw // 2, self.sh // 2, text="Checking and fitting the new data…", font=("Segoe UI", 20, "bold"), fill=CYAN)
            return
        if self.stage in ("DOTS", "RECHECK"):
            tx, ty = self.dots[self.idx] if self.stage == "DOTS" else self._recheck_target(self.idx)
            head = (f"Level 2 fine-tuning  ·  dot {len(self.dots) - len(self.queue) + 1} of {len(self.dots)}" if self.stage == "DOTS"
                    else f"Re-checking a dot that did not match  ·  {len(self.queue)} left")
            c.create_text(self.sw // 2, 40, text=head, font=("Segoe UI", 15, "bold"), fill=CYAN if self.stage == "DOTS" else GOLD)
            c.create_text(self.sw // 2, 68, text="Focus steadily on the dot  ·  [ESC] cancel", font=("Segoe UI", 11), fill=MUTED)
            col = CYAN if self.stage == "DOTS" else GOLD
            if self.phase == "APPROACH":
                r = 65 - int(48 * min(1.0, el / self.APPROACH_S))
                c.create_oval(tx - r, ty - r, tx + r, ty + r, outline=col, width=2)
            else:
                ext = int(360 * min(1.0, el / self.COLLECT_S))
                if ext > 0:
                    c.create_arc(tx - 24, ty - 24, tx + 24, ty + 24, start=90, extent=-ext, outline=col, width=3, style="arc")
            c.create_oval(tx - 8, ty - 8, tx + 8, ty + 8, fill=WHITE, outline=col, width=2)
        elif self.stage == "PATHS":
            p0, p1 = self.paths[self.idx]
            c.create_text(self.sw // 2, 40, text=f"Level 2 fine-tuning  ·  follow the moving dot ({self.idx + 1}/2)",
                          font=("Segoe UI", 15, "bold"), fill=GOLD)
            c.create_line(p0[0], p0[1], p1[0], p1[1], fill="#202c3d", width=2, dash=(6, 6))
            if self.phase == "APPROACH":
                tx, ty = p0
            else:
                f = min(1.0, el / self.TRACK_S)
                tx, ty = p0[0] + (p1[0] - p0[0]) * f, p0[1] + (p1[1] - p0[1]) * f
            c.create_oval(tx - 8, ty - 8, tx + 8, ty + 8, fill=GOLD, outline=WHITE, width=2)
        self.pointer.draw(c, big=True)
