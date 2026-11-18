#!/usr/bin/env python3
"""
arc_pupil.py - ArcFit: reflection-aware pupil detector with smooth tracking.

Per frame (640x480 camera pixels):
  1. Glints      bright compact spots are found and painted dark, so a reflection inside or on the
                 edge of the pupil neither punches a hole in it nor bends its outline.
  2. Segment     the pupil is the darkest compact blob. Several thresholds above the darkest level
                 are tried; each candidate blob's outline is fitted with an ellipse and the blob that
                 is most ellipse-like (and, when tracking, closest to the last position and of the
                 learned size) wins. Searches a window around the last position, else the full frame.
  3. Refine      72 rays from the blob centre; each finds where the brightness crosses halfway between
                 the pupil's own darkness and its own iris (sub-pixel). Rays near glints, on straight
                 runs (lid edge, lashes) or leaving the image / cage are dropped.
  4. Ellipse     direct fit when the arc is clean, RANSAC otherwise; when most of the edge is hidden the
                 learned pupil shape is kept and only centre and size are solved. Falls back to the
                 segmentation ellipse if refinement fails.
  5. Stabilise   one bad frame is bridged, weak implausible jumps are ignored, and the output goes
                 through a speed-adaptive filter: steady at rest, no lag when moving.

The boundary cage is handled here (wants_cage = True): it limits the search, it does not paint pixels.

detect(frame, cage=None) -> (rotated_rect | None, latency_ms, confidence)
"""

import math
import time
from typing import Optional, Tuple, Dict, Any

import cv2
import numpy as np

W, H = 640, 480
N_RAYS = 72
WEAK_HOLD = 4                      # frames a weak, far-away result is ignored (blink guard)
MAX_COAST = 1                      # frames a lost pupil is held before reporting "not found"
# [D1] smallest believable pupil (sqrt(a*b) of the semi-axes, camera px). On this headset the real pupil
# measured 40-75 px in every recording; eyelash / lid-crease blobs that the tracker used to lock onto were
# 9-27 px. Anything smaller is not a pupil. Set to 0 to disable.
MIN_SIZE = 28.0
# [D2] the black lens vignette in the image corners forms a big dark blob along the frame/cage edge that beat
# the pupil when looking up. A blob with more than this share of its outline on that edge is not a pupil.
MAX_EDGE_SHARE = 0.15
# [D3] narrowest believable pupil (minor/major axis). Looking far to the side the pupil is seen at a steep
# angle and gets narrow (was 0.40, which rejected the real pupil at the far corners).
MIN_ASPECT = 0.30
DELTAS = (8, 14, 22, 32, 45, 60)   # thresholds above the darkest level tried by the segmentation


def _fit_ellipse(pts: np.ndarray):
    """cv2.fitEllipseDirect needs a contiguous float32 (n, 2) array."""
    return cv2.fitEllipseDirect(np.ascontiguousarray(pts, dtype=np.float32).reshape(-1, 2))


class _Kalman:
    """
    Constant-velocity Kalman filter for one coordinate (position, velocity).
    q: how freely the eye may accelerate (px²/s³) - higher follows faster, lower is steadier.
    r: measurement noise (px) of a fully trusted frame; a frame with trust weight w counts as r / w,
       so uncertain frames (glints on the edge) pull the output less.
    A measurement far outside the predicted range (> gate standard deviations) is a saccade:
    the filter jumps straight to it, so fast eye movements are never smoothed away.
    """

    def __init__(self, q: float = 1000.0, r: float = 0.8, gate: float = 6.0):
        self.q, self.r, self.gate = q, r, gate
        self.gate_r = r          # noise used for the saccade test; stays fixed when r is raised for smoothing
        self.reset()

    def reset(self):
        self.x = None
        self.v = 0.0
        self.P = None
        self.t = None

    def __call__(self, z: float, t: float, w: float = 1.0) -> float:
        R = (self.r / max(w, 0.05)) ** 2
        if self.x is None:
            self.x, self.v, self.t = z, 0.0, t
            self.P = np.array([[R, 0.0], [0.0, 1e4]])
            return z
        dt = min(max(t - self.t, 1e-3), 0.2)
        self.t = t
        # predict
        self.x += self.v * dt
        F = np.array([[1.0, dt], [0.0, 1.0]])
        Q = self.q * np.array([[dt ** 3 / 3.0, dt ** 2 / 2.0], [dt ** 2 / 2.0, dt]])
        P = F @ self.P @ F.T + Q
        # update
        S = P[0, 0] + R
        innov = z - self.x
        S_gate = min(S, P[0, 0] + (self.gate_r / max(w, 0.05)) ** 2)
        if innov * innov > self.gate * self.gate * S_gate:  # saccade: follow immediately
            self.x, self.v = z, 0.0
            self.P = np.array([[R, 0.0], [0.0, 1e4]])
            return z
        K = P[:, 0] / S
        self.x += K[0] * innov
        self.v += K[1] * innov
        self.P = P - np.outer(K, P[0, :])
        return self.x


class ArcFitDetector:
    NAME = "ArcFit"
    COLOR = (160, 255, 80)
    wants_cage = True

    def __init__(self):
        ang = np.linspace(0.0, 2.0 * np.pi, N_RAYS, endpoint=False)
        self.cos = np.cos(ang).astype(np.float32)
        self.sin = np.sin(ang).astype(np.float32)
        self.rng = np.random.default_rng(20261116)
        self.k3 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        self.k5 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self.k7 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        # output filter, tuned on a real recording (research/tune_filter.py): vs the old 1-Euro
        # ~30 % less jitter while fixating and ~60 % less lag while moving
        self.fx = _Kalman(q=60.0, r=0.4)
        self.fy = _Kalman(q=60.0, r=0.4)
        self.shape_gain = 0.5       # EMA gain for the ellipse axes/angle (set_smoothing)
        self._cage_key = "unset"
        self._valid = None          # uint8 HxW, 1 where the pupil may be searched
        self._valid_in = None       # eroded copy: contour points outside it touch the region border
        self._valid_f = None
        self.reset()

    def set_smoothing(self, level: float):
        """Pupil smoothing 0..10 (Eye View slider). 3 = the tuned default (Kalman r 0.4 px, shape EMA 0.5).
        Each step up multiplies the centre's measurement noise r by 1.5 (steadier centre, a bit more lag on
        small movements) and divides the circle's size/angle EMA gain by 1.5. Saccades still jump at once:
        the saccade test keeps using the default noise."""
        f = 1.5 ** (float(level) - 3.0)
        self.fx.r = self.fy.r = 0.4 * f
        self.shape_gain = min(1.0, 0.5 / f)

    def reset(self):
        self.prev: Optional[Tuple[float, float]] = None   # last measured centre (search hint)
        self.miss = 0
        self.coast = 0
        self.weak_far = 0
        self.last_constrained = False
        self.fast_ok = False                               # last frame clean enough for the fast path
        self.raw_e = None                                  # last fitted (unsmoothed) ellipse
        self.size: Optional[float] = None                  # sqrt(a*b) of the semi-axes, px
        self.shape: Optional[np.ndarray] = None            # normalised shape (m11, m12, m22), det 1
        self.stale = 0
        self.glint_vec: Optional[np.ndarray] = None
        self.pdark: Optional[float] = None                 # this pupil's dark level
        self.piris: Optional[float] = None                 # its iris level
        self.out_rect = None
        self.axes_s: Optional[np.ndarray] = None
        self.ang_s: Optional[float] = None
        self.fx.reset()
        self.fy.reset()
        self.last_extra: Dict[str, Any] = {}

    # ------------------------------------------------------------------ search region
    def _set_cage(self, cage):
        key = None if cage is None else tuple(int(v) for v in cage)
        if key == self._cage_key:
            return
        self._cage_key = key
        v = np.zeros((H, W), np.uint8)
        if key is None:
            v[:, :] = 1
        else:
            bx, by, rx, ry = key
            cv2.ellipse(v, (bx, by), (rx, ry), 0, 0, 360, 1, -1)
        v[:2, :] = 0
        v[H - 2:, :] = 0
        v[:, :2] = 0
        v[:, W - 2:] = 0
        self._valid = v
        self._valid_in = cv2.erode(v, self.k7, iterations=1)
        self._valid_f = v.astype(np.float32)

    # ------------------------------------------------------------------ 1. glints
    def _glints(self, gray: np.ndarray, box, centre):
        """
        Bright compact spots -> (left, right, core mask or None, ignore mask or None).
        While tracking only spots near the pupil count (bright skin texture is not a glint).
        The ignore zone around each glint matches its real size: radius = glint radius + 6 px.
        """
        x0, y0, x1, y1 = box
        roi = gray[y0:y1, x0:x1]
        if roi.size == 0:
            return None, None, None, None
        top = float(roi.max())
        thr = max(140.0, float(np.percentile(roi, 50)) + 0.6 * (top - float(np.percentile(roi, 50))))
        _, mask = cv2.threshold(roi, thr, 255, cv2.THRESH_BINARY)
        n, lab, st, cen = cv2.connectedComponentsWithStats(mask, 8)
        if n <= 1:
            return None, None, None, None
        near = (1.6 * self.size + 20.0) if (self.size and self.prev is not None) else 1e9
        keep = np.zeros(n, np.uint8)
        cands = []
        for i in range(1, n):
            bx, by, bw, bh, area = st[i]
            if area < 5 or area > 1500 or max(bw, bh) > 3.0 * min(bw, bh) or area < 0.35 * bw * bh:
                continue
            g = np.array([x0 + cen[i][0], y0 + cen[i][1]])
            d = math.hypot(g[0] - centre[0], g[1] - centre[1])
            if d > near:
                continue
            keep[i] = 255
            cands.append((d, g, math.sqrt(area / math.pi)))
        if not keep.any():
            return None, None, None, None
        clamp = np.zeros((H, W), np.uint8)
        clamp[y0:y1, x0:x1] = cv2.dilate(keep[lab], self.k5, iterations=1)
        excl = np.zeros((H, W), np.uint8)
        for _, g, rg in cands:
            cv2.circle(excl, (int(round(g[0])), int(round(g[1]))), int(round(rg + 6.0)), 255, -1)
        excl |= clamp

        left = right = None
        cands.sort(key=lambda c: c[0])
        best = None
        for i in range(min(4, len(cands))):
            for j in range(i + 1, min(4, len(cands))):
                a, b = cands[i][1], cands[j][1]
                if a[0] > b[0]:
                    a, b = b, a
                v = b - a
                sep = float(np.hypot(v[0], v[1]))
                if sep < 6.0 or sep > 110.0:
                    continue
                score = cands[i][0] + cands[j][0]
                if self.glint_vec is not None:
                    score += 3.0 * float(np.hypot(*(v - self.glint_vec)))
                if best is None or score < best[0]:
                    best = (score, a, b)
        if best is not None:
            left, right = best[1], best[2]
            v = right - left
            self.glint_vec = v if self.glint_vec is None else 0.9 * self.glint_vec + 0.1 * v
        return left, right, clamp, excl

    # ------------------------------------------------------------------ 2. segmentation
    def _segment(self, blur: np.ndarray, box, hint, excl: Optional[np.ndarray] = None, tracking: bool = False):
        """Darkest ellipse-like blob in box -> (centre, ellipse, dark_level) or None."""
        x0, y0, x1, y1 = box
        roi = blur[y0:y1, x0:x1]
        v = self._valid[y0:y1, x0:x1]
        vin = self._valid_in[y0:y1, x0:x1]
        exm = excl[y0:y1, x0:x1] if excl is not None else None
        vals = roi[v > 0]
        if vals.size < 500:
            return None
        if tracking and self.pdark is not None and self.piris is not None and self.piris - self.pdark > 10.0:
            # thresholds between this pupil's own darkness and its iris: stable from frame to frame
            span = self.piris - self.pdark
            levels = [self.pdark + f * span for f in (0.25, 0.35, 0.45, 0.55, 0.65)]
        else:
            dark0 = float(np.percentile(vals, 0.5))
            levels = [dark0 + d for d in DELTAS]
        if self.size:
            amin, amax = 0.20 * math.pi * self.size ** 2, 2.8 * math.pi * self.size ** 2
        else:
            amin, amax = 250.0, 0.30 * vals.size
        reach = float(max(x1 - x0, y1 - y0))
        best = None
        for level in levels:
            m = ((roi < level) & (v > 0)).astype(np.uint8)
            m = cv2.morphologyEx(m, cv2.MORPH_OPEN, self.k5)       # cut thin links to lashes and shadows
            n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
            for i in range(1, n):
                bx, by, bw, bh, area = st[i]
                if area < amin or area > amax or max(bw, bh) > 3.5 * min(bw, bh):
                    continue
                comp = (lab[by:by + bh, bx:bx + bw] == i).astype(np.uint8)
                cnts, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
                if not cnts:
                    continue
                c = (max(cnts, key=len)[:, 0, :] + np.array([bx, by])).astype(np.float32)
                if len(c) < 20:
                    continue
                ce = c.astype(int)
                if (vin[ce[:, 1], ce[:, 0]] == 0).mean() > MAX_EDGE_SHARE:  # runs along the frame/cage edge:
                    continue                                                 # vignette corner, not a pupil
                # a pupil is convex: outline that dips inward (grey reflection, lash) is not pupil edge
                hull = cv2.convexHull(c.reshape(-1, 1, 2)).reshape(-1, 2).astype(np.float32)
                hull_area = float(cv2.contourArea(hull))
                c = c[self._hull_dist(c, hull) < 2.0]
                if len(c) < 20:
                    continue
                ci = c.astype(int)
                keep = vin[ci[:, 1], ci[:, 0]] > 0                    # drop outline along the image/cage border
                if exm is not None:
                    keep &= exm[ci[:, 1], ci[:, 0]] == 0              # and next to a glint
                c = c[keep]
                if len(c) < 20:
                    continue
                step = max(1, len(c) // 90)
                p = np.c_[c[::step], np.arange(len(c[::step]), dtype=np.float32)]
                p = self._drop_straight(p)                             # drop lid / lash straight edges
                if len(p) < 15:
                    continue
                try:
                    e = _fit_ellipse(p[:, :2])
                except cv2.error:
                    continue
                if not self._ok_ellipse(e):
                    continue
                ew, eh = e[1]
                fit = float(np.median(self._resid(p[:, :2], e)))
                fill = hull_area / max(1.0, math.pi * ew * eh / 4.0)
                if fill > 1.25 or fill < 0.35:
                    continue
                score = -abs(1.0 - min(fill, 1.0)) - 0.15 * fit
                ex, ey = e[0][0] + x0, e[0][1] + y0
                if hint is not None:
                    score -= 0.5 * math.hypot(ex - hint[0], ey - hint[1]) / reach
                if self.size:
                    score -= 0.6 * abs(math.sqrt(ew * eh) / 2.0 - self.size) / self.size
                else:
                    score += 0.08 * math.log(max(area, 1.0) / 1000.0)    # the pupil is the largest dark ellipse
                if best is None or score > best[0]:
                    best = (score, (ex, ey), ((ex, ey), e[1], e[2]), level)
        return None if best is None else best[1:]

    # ------------------------------------------------------------------ 3. sub-pixel boundary
    def _edges(self, img32: np.ndarray, clamp: Optional[np.ndarray], cx: float, cy: float, s_est: float):
        """-> (points (n,3) x,y,ray, dark, contrast, rays that can see an edge)."""
        empty = np.zeros((0, 3), np.float32)
        rmin, rmax = max(3.0, 0.35 * s_est), min(200.0, 1.9 * s_est + 12.0)
        rs = np.arange(rmin, rmax, 1.0, dtype=np.float32)
        nr = len(rs)
        if nr < 10:
            return empty, 0.0, 0.0, 0
        mx = (cx + np.outer(self.cos, rs)).astype(np.float32)
        my = (cy + np.outer(self.sin, rs)).astype(np.float32)
        prof = cv2.remap(img32, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        vis = cv2.remap(self._valid_f, mx, my, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)
        vis = np.minimum.accumulate(vis > 0.5, axis=1)
        if clamp is not None:
            gl = cv2.remap(clamp.astype(np.float32), mx, my, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)
        else:
            gl = None

        # pupil level from the inner part, iris level from just outside the expected edge
        k_in = int(np.clip(0.75 * s_est - rmin, 1, nr - 1))
        k_out0 = int(np.clip(1.15 * s_est - rmin, 0, nr - 2))
        k_out1 = int(np.clip(1.45 * s_est - rmin, k_out0 + 1, nr))
        dark = float(np.median(prof[:, :k_in]))
        ring = prof[:, k_out0:k_out1][vis[:, k_out0:k_out1]]
        if ring.size < 30:
            return empty, dark, 0.0, 0
        iris = float(np.percentile(ring, 40))
        contrast = iris - dark
        if contrast < 10.0:
            return empty, dark, contrast, 0
        avail = int(vis[:, min(k_out0, nr - 1)].sum())
        mid = dark + 0.5 * contrast

        above = prof >= mid
        ok_run = vis.copy()
        if gl is not None:
            ok_run &= gl < 0.5
        # first upward crossing of mid (between samples k-1 and k) that is a real pupil edge:
        # the next 3 samples stay above dark + 0.35*contrast, and within 7 samples the profile climbs
        # to near iris brightness. A grey haze or reflection inside the pupil forms a plateau that
        # never reaches iris level, so it is skipped and the search continues to the true edge.
        pad = np.pad(prof, ((0, 0), (0, 7)), mode="edge")
        fwd_min = np.min(np.stack([pad[:, i:i + nr] for i in range(4)]), axis=0)
        fwd_max = np.max(np.stack([pad[:, i:i + nr] for i in range(8)]), axis=0)
        cand = np.zeros_like(above)
        cand[:, 1:] = above[:, 1:] & ~above[:, :-1]
        cand &= ok_run & (fwd_min > dark + 0.35 * contrast) & (fwd_max >= dark + 0.75 * contrast)
        cand[:, 0] = False
        has = cand.any(axis=1)
        k = np.argmax(cand, axis=1)
        idx = np.nonzero(has)[0]
        if len(idx) < 8:
            return empty, dark, contrast, avail
        k = k[idx]
        p0, p1 = prof[idx, k - 1], prof[idx, k]
        frac = np.clip((mid - p0) / np.maximum(p1 - p0, 1e-3), 0.0, 1.0)
        r = rs[k - 1] + frac
        x = cx + self.cos[idx] * r
        y = cy + self.sin[idx] * r
        if clamp is not None:                                          # crossing right next to a glint
            xi = np.clip(np.rint(x + 2.0 * self.cos[idx]).astype(int), 0, W - 1)
            yi = np.clip(np.rint(y + 2.0 * self.sin[idx]).astype(int), 0, H - 1)
            good = clamp[yi, xi] == 0
            x, y, idx = x[good], y[good], idx[good]
        pts = np.stack([x, y, idx.astype(np.float32)], axis=1).astype(np.float32)
        pts = self._drop_dents(pts, cx, cy, s_est)
        return self._drop_straight(pts), dark, contrast, avail

    @staticmethod
    def _hull_dist(p: np.ndarray, hull: np.ndarray) -> np.ndarray:
        """Distance of each point to the convex hull outline."""
        a = hull
        b = np.roll(hull, -1, axis=0)
        ab = b - a
        L2 = np.maximum((ab ** 2).sum(axis=1), 1e-9)
        ap = p[:, None, :] - a[None, :, :]
        t = np.clip((ap * ab[None]).sum(axis=2) / L2[None], 0.0, 1.0)
        q = a[None] + t[..., None] * ab[None]
        return np.sqrt(((p[:, None, :] - q) ** 2).sum(axis=2)).min(axis=1)

    @staticmethod
    def _drop_dents(pts: np.ndarray, cx: float, cy: float, s_est: float, half: int = 4) -> np.ndarray:
        """Removes ray edges that sit clearly inside their neighbours (a reflection or lash inside the pupil)."""
        if len(pts) < 10:
            return pts
        R = np.full(N_RAYS, np.nan, np.float32)
        ray = pts[:, 2].astype(int)
        r = np.hypot(pts[:, 0] - cx, pts[:, 1] - cy)
        R[ray] = r
        offs = np.r_[np.arange(-half, 0), np.arange(1, half + 1)]
        nb = R[(ray[:, None] + offs[None, :]) % N_RAYS]
        med = np.full(len(nb), np.nan, np.float32)
        has_nb = ~np.isnan(nb).all(axis=1)          # rays with no neighbouring edge points are kept as they are
        if has_nb.any():
            med[has_nb] = np.nanmedian(nb[has_nb], axis=1)
        keep = np.isnan(med) | (r >= med - (2.0 + 0.10 * s_est))
        return pts[keep] if keep.sum() >= 8 else pts

    @staticmethod
    def _drop_straight(p: np.ndarray, win: int = 9) -> np.ndarray:
        """Removes points on straight runs (lid edge, lashes): a pupil arc bends, a line does not."""
        n = len(p)
        if n < win + 3:
            return p
        ids = (np.arange(n)[:, None] + np.arange(win)[None, :]) % n
        q = p[ids]
        span = (q[:, -1, 2] - q[:, 0, 2]) % max(N_RAYS, int(p[:, 2].max()) + 1)
        xy = q[:, :, :2]
        d = xy - xy.mean(axis=1, keepdims=True)
        sxx = (d[:, :, 0] ** 2).mean(axis=1)
        syy = (d[:, :, 1] ** 2).mean(axis=1)
        sxy = (d[:, :, 0] * d[:, :, 1]).mean(axis=1)
        tr, det = sxx + syy, sxx * syy - sxy * sxy
        lam_min = np.maximum(0.0, 0.5 * (tr - np.sqrt(np.maximum(0.0, tr * tr - 4.0 * det))))
        chord = np.hypot(xy[:, -1, 0] - xy[:, 0, 0], xy[:, -1, 1] - xy[:, 0, 1])
        flat = (span <= win + 2) & (chord > 14.0) & (np.sqrt(lam_min) < 0.012 * chord)
        if not flat.any():
            return p
        drop = np.zeros(n, bool)
        drop[ids[flat].ravel()] = True
        return p[~drop] if (~drop).sum() >= 8 else p

    # ------------------------------------------------------------------ 4. ellipse
    @staticmethod
    def _resid(xy: np.ndarray, e) -> np.ndarray:
        (cx, cy), (w, h), ang = e
        th = math.radians(ang)
        dx, dy = xy[:, 0] - cx, xy[:, 1] - cy
        ct, st = math.cos(th), math.sin(th)
        a, b = max(w, 1e-3) * 0.5, max(h, 1e-3) * 0.5
        rho = np.hypot((dx * ct + dy * st) / a, (-dx * st + dy * ct) / b)
        return np.abs(rho - 1.0) * min(a, b)

    @staticmethod
    def _sresid(xy: np.ndarray, e) -> np.ndarray:
        """Signed distance-like residual: > 0 outside the ellipse, < 0 inside (px)."""
        (cx, cy), (w, h), ang = e
        th = math.radians(ang)
        dx, dy = xy[:, 0] - cx, xy[:, 1] - cy
        ct, st = math.cos(th), math.sin(th)
        a, b = max(w, 1e-3) * 0.5, max(h, 1e-3) * 0.5
        rho = np.hypot((dx * ct + dy * st) / a, (-dx * st + dy * ct) / b)
        return (rho - 1.0) * min(a, b)

    @staticmethod
    def _ok_ellipse(e) -> bool:
        (cx, cy), (w, h), _ = e
        lo, hi = min(w, h), max(w, h)
        if math.sqrt(max(w * h, 0.0)) / 2.0 < MIN_SIZE:
            return False
        return 12.0 < lo and hi < 320.0 and lo / hi > MIN_ASPECT and -60 <= cx < W + 60 and -60 <= cy < H + 60

    @staticmethod
    def _shape_of(e) -> Tuple[np.ndarray, float]:
        (_, _), (w, h), ang = e
        a, b = 0.5 * w, 0.5 * h
        th = math.radians(ang)
        ct, st = math.cos(th), math.sin(th)
        s2 = a * b
        ia, ib = s2 / (a * a), s2 / (b * b)
        return np.array([ia * ct * ct + ib * st * st, (ia - ib) * ct * st, ia * st * st + ib * ct * ct]), math.sqrt(s2)

    @staticmethod
    def _rect_from(c, s: float, m: np.ndarray):
        M = np.array([[m[0], m[1]], [m[1], m[2]]])
        lam, vec = np.linalg.eigh(M)
        a, b = s / math.sqrt(max(lam[0], 1e-9)), s / math.sqrt(max(lam[1], 1e-9))
        ang = math.degrees(math.atan2(vec[1, 0], vec[0, 0]))
        return (float(c[0]), float(c[1])), (2.0 * a, 2.0 * b), ang % 180.0

    @staticmethod
    def _cs_fit(xy: np.ndarray, m: np.ndarray, c, s: float, iters: int = 4):
        """Gauss-Newton on centre and size with the shape fixed -> (centre, size, residual_px, JtJ)."""
        M = np.array([[m[0], m[1]], [m[1], m[2]]])
        c = np.array(c, np.float64)
        JtJ, r = np.eye(3), np.zeros(len(xy))
        for it in range(iters):
            d = xy - c
            Md = d @ M
            q = np.sqrt(np.maximum(1e-9, np.einsum("ij,ij->i", Md, d)))
            r = q / s - 1.0
            J = np.empty((len(xy), 3))
            J[:, :2] = -Md / (q[:, None] * s)
            J[:, 2] = -q / (s * s)
            JtJ = J.T @ J
            if it == iters - 1:
                break
            try:
                step = np.linalg.solve(JtJ + 1e-9 * np.eye(3), -J.T @ r)
            except np.linalg.LinAlgError:
                break
            c = c + step[:2]
            s = max(4.0, s + step[2])
        return c, s, r * s, JtJ

    def _fit_free(self, xy: np.ndarray, s_ref: float):
        n = len(xy)
        if n < 10:
            return None
        try:
            e = _fit_ellipse(xy)
            if self._ok_ellipse(e):
                r = self._resid(xy, e)
                if (r < 1.5).mean() >= 0.85:                        # clean arc: no sampling needed
                    return e, r < 1.5
        except cv2.error:
            pass
        cands = []
        for _ in range(40):
            try:
                e = _fit_ellipse(xy[self.rng.choice(n, 6, replace=False)])
            except cv2.error:
                continue
            if self._ok_ellipse(e):
                sr = self._sresid(xy, e)
                support = int((np.abs(sr) < 2.0).sum())
                outside = int((sr > 2.5).sum())
                # the true pupil edge is the OUTERMOST edge: haze/reflections inside the pupil only
                # add edges inside it, so an ellipse that leaves edge points outside is penalised
                cands.append((support - 1.5 * outside, support, self._shape_of(e)[1], e))
        if not cands:
            return None
        best_score = max(c[0] for c in cands)
        if max(c[1] for c in cands) < 9 or best_score < 6:
            return None
        good = [c for c in cands if c[0] >= best_score - 0.15 * max(c[1] for c in cands)]
        e = min(good, key=lambda c: abs(c[2] - s_ref))[3]
        inl = self._resid(xy, e) < 2.0
        try:
            e2 = _fit_ellipse(xy[inl])
            if self._ok_ellipse(e2):
                e, inl = e2, self._resid(xy, e2) < 1.8
        except cv2.error:
            pass
        return e, inl

    def _fit_constrained(self, xy: np.ndarray, hint, m: np.ndarray, s0: float):
        """Known shape and size: two boundary points give candidate centres in closed form."""
        n = len(xy)
        if n < 8:
            return None
        try:
            L = np.linalg.cholesky(np.array([[m[0], m[1]], [m[1], m[2]]])).T
        except np.linalg.LinAlgError:
            return None
        xy64 = xy.astype(np.float64)
        u = xy64 @ L.T
        i = self.rng.integers(0, n, 40)
        j = (i + self.rng.integers(max(2, n // 6), max(3, n // 2), 40)) % n
        mid = 0.5 * (u[i] + u[j])
        dv = u[j] - u[i]
        h = 0.5 * np.hypot(dv[:, 0], dv[:, 1])
        perp = np.stack([-dv[:, 1], dv[:, 0]], axis=1) / np.maximum(2.0 * h, 1e-6)[:, None]
        cs, rad = [], []
        for scale in (0.9, 1.0, 1.1):
            r = s0 * scale
            ok = h < r
            off = np.sqrt(np.maximum(0.0, r * r - h * h))[:, None] * perp
            cs += [mid[ok] + off[ok], mid[ok] - off[ok]]
            rad += [np.full(int(ok.sum()), r)] * 2
        w = np.concatenate(cs)
        if len(w) == 0:
            return None
        rad = np.concatenate(rad)
        dist = np.hypot(u[None, :, 0] - w[:, 0:1], u[None, :, 1] - w[:, 1:2])
        counts = (np.abs(dist - rad[:, None]) < 2.0).sum(axis=1)
        outside = ((dist - rad[:, None]) > 2.5).sum(axis=1)       # edge points left outside the pupil
        hw = np.asarray(hint, np.float64) @ L.T
        far = np.hypot(w[:, 0] - hw[0], w[:, 1] - hw[1]) / max(s0, 1.0)
        best = int(np.argmax(counts - 1.5 * outside - 0.5 * np.minimum(far, 2.0)))
        if counts[best] < 8:
            return None
        c, size = np.linalg.solve(L, w[best]), float(rad[best])
        inl = np.abs(dist[best] - rad[best]) < 2.0
        for _ in range(2):
            c, size, _, _ = self._cs_fit(xy64[inl], m, c, size)
            inl2 = np.abs(self._cs_fit(xy64, m, c, size, iters=1)[2]) < 1.8
            if inl2.sum() < 8:
                break
            inl = inl2
        if abs(size - s0) / s0 > 0.25:
            return None
        _, _, r_px, JtJ = self._cs_fit(xy64[inl], m, c, size, iters=1)
        return c, size, inl, r_px, JtJ

    def _solve(self, pts: np.ndarray, hint, avail: int, seg_e):
        """-> (centre, size, shape, residual_px, JtJ, constrained, coverage) or None."""
        if len(pts) < 8:
            return None
        xy = pts[:, :2]
        denom = float(max(avail, 24))
        m_seg, s_seg = self._shape_of(seg_e)
        s_ref = self.size or s_seg
        free = self._fit_free(xy, s_ref)
        if free is not None:
            e, inl = free
            m, s = self._shape_of(e)
            cov = min(1.0, len(np.unique(pts[inl, 2])) / denom)
            _, _, r_px, JtJ = self._cs_fit(xy[inl].astype(np.float64), m, e[0], s, iters=1)
            rms = float(np.sqrt(np.mean(r_px ** 2))) if len(r_px) else 9.0
            size_ok = abs(s - s_ref) / s_ref < 0.35
            need = 0.62 if self.last_constrained else 0.5   # hysteresis: no flipping between fit modes
            if cov >= need and (size_ok or (cov >= 0.7 and rms < 1.0)):
                return np.array(e[0], np.float64), s, m, r_px, JtJ, False, cov
        m_p = self.shape if self.shape is not None else m_seg
        con = self._fit_constrained(xy, hint, m_p, s_ref)
        if con is not None:
            c, s, inl, r_px, JtJ = con
            cov = min(1.0, len(np.unique(pts[inl, 2])) / denom)
            if cov >= 0.25:
                return c, s, m_p, r_px, JtJ, True, cov
        return None

    # ------------------------------------------------------------------ 5. output
    def _miss(self, t0: float, glints=None):
        self.miss += 1
        self.stale += 1
        if self.miss >= 2:
            self.prev = None
        if self.stale > 45:                       # nothing confirmed for ~1.5 s: forget the profile
            self.size = self.shape = None
            self.pdark = self.piris = None
            self.stale = 0
        self.last_extra = {"glints": glints, "cov": None, "coverage": 0.0, "held": False}
        lat = (time.perf_counter() - t0) * 1000.0
        if self.out_rect is not None and self.coast < MAX_COAST:
            self.coast += 1                        # bridge one bad frame instead of flickering
            self.last_extra["held"] = True
            return self.out_rect, lat, 0.2
        self.out_rect = None
        self.fx.reset()
        self.fy.reset()
        self.axes_s, self.ang_s = None, None
        return None, lat, 0.0

    def _refine(self, img32: np.ndarray, excl, cx: float, cy: float, seg_e):
        """Sub-pixel edge rays + ellipse fit from (cx, cy), re-centred up to twice -> (sol, dark, contrast)."""
        s_ref = self.size or self._shape_of(seg_e)[1]
        sol, dark, contrast = None, 0.0, 0.0
        for _ in range(2):
            pts, dark, contrast, avail = self._edges(img32, excl, cx, cy, s_ref)
            sol = self._solve(pts, (cx, cy), avail, seg_e)
            if sol is None:
                break
            moved = math.hypot(sol[0][0] - cx, sol[0][1] - cy)
            cx, cy = float(sol[0][0]), float(sol[0][1])
            if moved < 1.0:
                break
        return sol, dark, contrast

    def detect(self, frame: np.ndarray, cage=None, t: Optional[float] = None):
        """t: frame time in seconds for the smoother (replay passes the recorded time; live uses the clock)."""
        t0 = time.perf_counter()
        self.rng = np.random.default_rng(20261116)  # same image -> same answer (no sampling flicker)
        if frame is None or frame.size == 0:
            return self._miss(t0)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        if gray.shape[0] != H or gray.shape[1] != W:
            gray = cv2.resize(gray, (W, H), interpolation=cv2.INTER_AREA)
        self._set_cage(cage)

        # search window: around the last position when tracking, else the whole frame
        tracking = self.prev is not None and self.size is not None
        if tracking:
            reach = int(2.2 * self.size + 50.0)
            box = (max(0, int(self.prev[0]) - reach), max(0, int(self.prev[1]) - reach),
                   min(W, int(self.prev[0]) + reach), min(H, int(self.prev[1]) + reach))
            centre = self.prev
        else:
            box, centre = (0, 0, W, H), (W / 2.0, H / 2.0)

        # glints are not painted over: the outline next to them is simply not trusted (excl zone)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        img32 = blur.astype(np.float32)
        gl, gr, clamp, excl = self._glints(gray, box, centre)

        # FAST PATH: the pupil was found cleanly last frame -> skip the blob search and measure the edge
        # directly from the last position and shape. Falls back to the full search if it does not hold up.
        sol, contrast, dark, seg_e = None, 0.0, 0.0, None
        if tracking and self.fast_ok and self.raw_e is not None:
            sol, dark, contrast = self._refine(img32, excl, self.prev[0], self.prev[1], self.raw_e)
            if sol is not None and (sol[6] < 0.45 or float(np.sqrt(np.mean(sol[3] ** 2))) > 1.5):
                sol = None                         # weak result: do the full search instead
            seg_e = self.raw_e
        if sol is None:
            seg = self._segment(blur, box, self.prev if tracking else None, excl, tracking)
            if seg is None and tracking:           # moved out of the window: search the whole frame
                box = (0, 0, W, H)
                gl, gr, clamp, excl = self._glints(gray, box, (W / 2.0, H / 2.0))
                seg = self._segment(blur, box, None, excl, False)
            if seg is None:
                self.fast_ok = False
                return self._miss(t0, [None if q is None else (float(q[0]), float(q[1])) for q in (gl, gr)])
            (cx, cy), seg_e, _ = seg
            sol, dark, contrast = self._refine(img32, excl, cx, cy, seg_e)
        glints_out = [None if q is None else (float(q[0]), float(q[1])) for q in (gl, gr)]

        if sol is not None:
            c, s, m, r_px, JtJ, constrained, coverage = sol
            rms = float(np.sqrt(np.mean(r_px ** 2))) if len(r_px) else 9.0
            conf = float(np.clip(coverage / 0.75, 0.0, 1.0) * np.clip(1.6 - rms, 0.3, 1.0) * min(1.0, contrast / 25.0))
            if constrained:
                conf *= 0.9
        else:                                       # refinement failed: the segmentation ellipse is still usable
            c = np.array(seg_e[0], np.float64)
            m, s = self._shape_of(seg_e)
            constrained, coverage, rms, JtJ = True, 0.0, 2.0, None
            conf = 0.35
        conf = max(conf, 0.3)                       # a segmented pupil is a real detection

        # a weak measurement far from where the pupil just was is a blink (the search lands on the lid or
        # lashes) or a bad frame: hold the last output for up to WEAK_HOLD frames (~one blink)
        if self.out_rect is not None and conf < 0.4 and self.weak_far < WEAK_HOLD:
            if math.hypot(c[0] - self.out_rect[0][0], c[1] - self.out_rect[0][1]) > 0.8 * (self.size or s):
                self.weak_far += 1
                self.last_extra = {"glints": glints_out, "cov": None, "coverage": 0.0, "held": True}
                return self.out_rect, (time.perf_counter() - t0) * 1000.0, 0.2

        # ---- accepted: update the eye profile
        self.prev = (float(c[0]), float(c[1]))
        self.raw_e = self._rect_from(c, s, m)
        self.fast_ok = sol is not None and coverage >= 0.45 and rms < 1.5
        self.miss = 0
        self.coast = 0
        self.weak_far = 0
        self.last_constrained = bool(constrained)
        if not constrained and coverage >= 0.6 and rms < 1.5:
            if self.size is None or self.shape is None or abs(s - self.size) / self.size > 0.35:
                self.size, self.shape = s, m
            else:
                self.size = 0.7 * self.size + 0.3 * s
                sh = 0.7 * self.shape + 0.3 * m
                self.shape = sh / math.sqrt(max(1e-9, sh[0] * sh[2] - sh[1] * sh[1]))
            if contrast > 10.0:                     # this pupil's own dark and iris levels
                iris = dark + contrast
                self.pdark = dark if self.pdark is None else 0.8 * self.pdark + 0.2 * dark
                self.piris = iris if self.piris is None else 0.8 * self.piris + 0.2 * iris
            self.stale = 0
        elif self.size is None:
            self.size = s                           # provisional, replaced by the first clean fit
        elif constrained and coverage >= 0.4 and rms < 1.0:
            self.size = 0.9 * self.size + 0.1 * s

        # ---- smooth the published ellipse (steady at rest, no lag on fast movement)
        (_, _), (w, h), ang = self._rect_from(c, s, m)
        if w < h:
            w, h, ang = h, w, (ang + 90.0) % 180.0
        tw = float(np.clip(conf / 0.8, 0.3, 1.0))  # trust weight: low-confidence frames move the output less
        tf = t0 if t is None else t
        sx, sy = self.fx(float(c[0]), tf, tw), self.fy(float(c[1]), tf, tw)
        axes = np.array([w, h])
        g = self.shape_gain * tw
        self.axes_s = axes if self.axes_s is None else (1.0 - g) * self.axes_s + g * axes
        if self.ang_s is None:
            self.ang_s = ang
        else:
            d = (ang - self.ang_s + 90.0) % 180.0 - 90.0
            self.ang_s = (self.ang_s + g * d) % 180.0
        self.out_rect = ((sx, sy), (float(self.axes_s[0]), float(self.axes_s[1])), float(self.ang_s))

        cov_t = None
        if JtJ is not None:
            try:
                cv_ = (max(rms, 0.25) / max(s, 1.0)) ** 2 * np.linalg.inv(JtJ + 1e-9 * np.eye(3))[:2, :2]
                cov_t = (float(cv_[0, 0]), float(cv_[0, 1]), float(cv_[1, 1]))
            except np.linalg.LinAlgError:
                pass
        self.last_extra = {
            "glints": glints_out, "cov": cov_t, "coverage": float(coverage), "rms": rms,
            "constrained": bool(constrained), "size": float(s), "raw_centre": (float(c[0]), float(c[1])),
            "held": False,
        }
        return self.out_rect, (time.perf_counter() - t0) * 1000.0, conf
