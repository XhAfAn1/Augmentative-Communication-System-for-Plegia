#!/usr/bin/env python3
"""
binocular_calibrator.py
=======================
Dual-Sensor Binocular Calibration & Gaze Estimation Engine.

Core Capabilities:
  1. Per-eye bivariate degree-2 polynomial mapping (pupil px -> screen px) on
     normalised pupil coordinates (zero-mean, unit-variance per eye) so the
     normal equations are well conditioned and ridge regularisation is fair.
  2. Weighted ridge least squares: static fixation anchors weigh 8x more than
     smooth-pursuit frames.
  3. Static points with no usable detection for an eye are simply skipped for
     that eye (never replaced by a placeholder).
  4. Confidence-weighted binocular fusion with single-eye fallback.
  5. Honest metrics: every number comes from measured data; anything that was
     not measured is reported as None instead of a made-up default.
  6. Persistence: binocular_calibration.json (model + metrics) and
     pursuit_telemetry.json (every pursuit frame).
"""

import os
import sys
import math
import time
import json
from typing import Optional, Tuple, Dict, Any, List
import numpy as np

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

Point = Tuple[float, float]


# =============================================================================
# [S1] Learned grid shape: soft nudge of the 9 static points (map v1)
# =============================================================================
# The 9 calibration dots form a 3x3 grid on screen; in camera space their pupil positions form the same grid,
# shifted, scaled, tilted (affine) and gently curved (the eye is a ball seen at an angle). The curve repeats
# from session to session: predicted from the other sessions each point lands within 3-4 camera px, while a
# detection error is 8-25 px. So every point has an expected position = robust affine grid fit + learned
# curve, and is pulled toward it: not at all within NUDGE_FREE_PX, up to NUDGE_MAX of the way at NUDGE_FULL_PX.
# The curve is stored per eye in data/grid_shape.json (divided by the grid scale) and learned with a running
# average after every full calibration. Nothing is deleted or replaced.
GRID_SHAPE_FILE = os.path.join(_THIS_DIR, "data", "grid_shape.json")
GRID_UNIT = np.array([[x, y] for y in (-1.0, 0.0, 1.0) for x in (-1.0, 0.0, 1.0)])   # grid order TL..BR
NUDGE_FREE_PX, NUDGE_FULL_PX, NUDGE_MAX = 4.0, 12.0, 0.85
SHAPE_LEARN_RATE = 0.2


def load_grid_shape() -> Dict[str, Any]:
    try:
        with open(GRID_SHAPE_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return {e: np.array(d[e], float) for e in ("left", "right") if d.get(e) is not None} | {"n": d.get("n", 0)}
    except Exception:
        return {"n": 0}


def save_grid_shape(shape: Dict[str, Any]):
    try:
        os.makedirs(os.path.dirname(GRID_SHAPE_FILE), exist_ok=True)
        out = {e: shape[e].tolist() for e in ("left", "right") if e in shape}
        out["n"] = int(shape.get("n", 0))
        with open(GRID_SHAPE_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)
        os.replace(GRID_SHAPE_FILE + ".tmp", GRID_SHAPE_FILE)
    except Exception as e:
        print(f"[Calibrator] could not save grid shape: {e}")


def grid_fit(points: List[Optional[Point]], shape: Optional[np.ndarray], robust: bool = True):
    """Robust affine grid (+ learned curve) through the 9 points -> (expected (9,2), deviation (9,), scale) or None."""
    P = np.array([(np.nan, np.nan) if p is None else p for p in points], float)
    ok = np.isfinite(P[:, 0])
    if len(P) != 9 or ok.sum() < 6:
        return None
    X = np.c_[np.ones(9), GRID_UNIT]
    sh = np.zeros((9, 2)) if shape is None else shape
    w, scale = np.ones(9), 1.0
    E = d = None
    for it in range(5):
        Y = P - sh * scale
        sw = np.sqrt(w[ok])[:, None]
        W = np.linalg.lstsq(X[ok] * sw, Y[ok] * sw, rcond=None)[0]
        scale = math.sqrt(abs(np.linalg.det(W[1:3])))
        E = X @ W + sh * scale
        d = np.hypot(*(P - E).T)
        if robust:
            w = 1.0 / (1.0 + (np.nan_to_num(d, nan=99.0) / 6.0) ** 2)      # outliers barely move the grid
    return E, d, scale


def nudge_grid(points: List[Optional[Point]], shape: Optional[np.ndarray]):
    """[S1] -> (nudged points, deviation per point in camera px). Points within NUDGE_FREE_PX are untouched."""
    g = grid_fit(points, shape)
    if g is None:
        return list(points), [None] * len(points)
    E, d, _ = g
    out = []
    for p, e, di in zip(points, E, d):
        if p is None:
            out.append(None)
            continue
        k = NUDGE_MAX * min(1.0, max(0.0, (di - NUDGE_FREE_PX) / (NUDGE_FULL_PX - NUDGE_FREE_PX)))
        out.append((p[0] + k * (e[0] - p[0]), p[1] + k * (e[1] - p[1])))
    return out, [None if p is None else float(x) for p, x in zip(points, d)]


def grid_shape_of(points: List[Optional[Point]], shape: Optional[np.ndarray]) -> Optional[np.ndarray]:
    """This session's curve (residual of the robust affine grid, divided by the grid scale); None if incomplete."""
    if any(p is None for p in points) or len(points) != 9:
        return None
    g = grid_fit(points, None, robust=False)                     # the curve = residual of a plain affine grid
    if g is None:
        return None
    E, d, scale = g
    return (np.array(points, float) - E) / max(scale, 1e-6)


def _rayleigh_hit_rate(radius_px: float, mean_err_px: float) -> float:
    """
    Probability that a 2-D Gaussian gaze landing falls within radius_px of the
    target, given the mean radial error. For an isotropic Gaussian the mean
    radial error is sigma * sqrt(pi / 2).
    """
    sigma = max(1e-3, mean_err_px / math.sqrt(math.pi / 2.0))
    return 100.0 * (1.0 - math.exp(-0.5 * (radius_px / sigma) ** 2))


def _tps_kernel(r: np.ndarray) -> np.ndarray:
    """Biharmonic thin plate spline radial basis kernel: U(r) = r^2 * ln(r)."""
    r_safe = np.maximum(r, 1e-9)
    out = (r_safe ** 2) * np.log(r_safe)
    out[r < 1e-9] = 0.0
    return out


class ThinPlateSpline2D:
    """
    2D Regularized Thin Plate Spline (TPS) coordinate mapping engine.
    Maps 2D pupil coordinates to 2D screen coordinates with minimum bending energy,
    providing exact local fidelity without high-degree polynomial corner divergence.
    """

    def __init__(self, reg: float = 0.03):
        self.reg = float(reg)
        self.knots: Optional[np.ndarray] = None    # shape (K, 2)
        self.weights: Optional[np.ndarray] = None  # shape (K, 2)
        self.affine: Optional[np.ndarray] = None   # shape (3, 2)

    def fit(self, src: np.ndarray, dst: np.ndarray, knot_weights: Optional[np.ndarray] = None):
        K = len(src)
        if K < 4:
            return None
        self.knots = np.asarray(src, dtype=np.float64)
        dst = np.asarray(dst, dtype=np.float64)

        # Pairwise distance matrix
        diff = self.knots[:, None, :] - self.knots[None, :, :]
        r = np.hypot(diff[:, :, 0], diff[:, :, 1])
        K_mat = _tps_kernel(r)

        # Regularization diagonal: higher weight on static knots -> lower regularization
        if knot_weights is not None:
            w_arr = np.asarray(knot_weights, dtype=np.float64)
            diag_reg = self.reg / np.clip(w_arr, 0.1, 15.0)
        else:
            diag_reg = np.full(K, self.reg, dtype=np.float64)
        K_mat += np.diag(diag_reg)

        P = np.hstack([np.ones((K, 1)), self.knots])
        L_top = np.hstack([K_mat, P])
        L_bot = np.hstack([P.T, np.zeros((3, 3))])
        L = np.vstack([L_top, L_bot])

        RHS = np.vstack([dst, np.zeros((3, 2))])
        try:
            sol, *_ = np.linalg.lstsq(L, RHS, rcond=None)
            self.weights = sol[:K]
            self.affine = sol[K:]
            return self
        except Exception:
            return None

    def predict(self, pt: np.ndarray) -> Optional[Tuple[float, float]]:
        if self.knots is None or self.weights is None or self.affine is None:
            return None
        pt_arr = np.asarray(pt, dtype=np.float64).reshape(2)
        diff = pt_arr - self.knots
        r = np.hypot(diff[:, 0], diff[:, 1])
        u_vals = _tps_kernel(r)
        aff = np.array([1.0, pt_arr[0], pt_arr[1]], dtype=np.float64) @ self.affine
        spl = u_vals @ self.weights
        return float(aff[0] + spl[0]), float(aff[1] + spl[1])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reg": self.reg,
            "knots": self.knots.tolist() if self.knots is not None else None,
            "weights": self.weights.tolist() if self.weights is not None else None,
            "affine": self.affine.tolist() if self.affine is not None else None,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Optional["ThinPlateSpline2D"]:
        if not d or not d.get("knots"):
            return None
        tps = cls(reg=float(d.get("reg", 0.03)))
        tps.knots = np.array(d["knots"], dtype=np.float64)
        tps.weights = np.array(d["weights"], dtype=np.float64)
        tps.affine = np.array(d["affine"], dtype=np.float64)
        return tps


class BinocularCalibrator:
    """Manages dual-eye calibration data, polynomial fitting, and binocular gaze prediction."""

    GRID_NORMALIZED = [
        (0.05, 0.05), (0.50, 0.05), (0.95, 0.05),
        (0.05, 0.50), (0.50, 0.50), (0.95, 0.50),
        (0.05, 0.95), (0.50, 0.95), (0.95, 0.95),
    ]

    TRACKING_PATHS_NORMALIZED = [
        ((0.06, 0.06), (0.94, 0.06)),  # Top L -> R
        ((0.94, 0.06), (0.06, 0.06)),  # Top R -> L
        ((0.06, 0.50), (0.94, 0.50)),  # Mid L -> R
        ((0.94, 0.50), (0.06, 0.50)),  # Mid R -> L
        ((0.06, 0.94), (0.94, 0.94)),  # Bot L -> R
        ((0.94, 0.94), (0.06, 0.94)),  # Bot R -> L
        ((0.06, 0.06), (0.94, 0.94)),  # Diag 1 (Top-Left -> Bot-Right)
        ((0.94, 0.06), (0.06, 0.94)),  # Diag 2 (Top-Right -> Bot-Left)
    ]

    VAL_NORMALIZED = [
        (0.25, 0.25), (0.75, 0.25),
        (0.25, 0.75), (0.75, 0.75),
    ]

    STATIC_WEIGHT = 8.0        # weight of a static fixation sample vs. one pursuit frame
    PURSUIT_MIN_CONF = 0.18    # pursuit frames below this confidence are not used for training
    FUSION_MIN_CONF = 0.05     # eyes below this confidence do not contribute to the fused gaze
    RIDGE_REG = 1e-4
    # [C1] mapping model. "cubic_aligned": robust cubic polynomial on the static anchors + pursuit frames whose
    # per-path drift is removed first (the headset slips a few camera px between calibration stages, so pursuit
    # disagreed with the anchors by 50-100 screen px). Tested on 5 saved sessions (research/calib_models2.py):
    # held-out static point error 80 -> 52 px, worst-case noise amplification (p95) 23 -> 13.5 screen px per
    # camera px vs the old "tps" (thin plate spline with every frame as a knot). Set to "tps" to revert.
    MODEL = "cubic_aligned"
    POLY_DEG = 3
    LAG_RANGE = (0.0, 0.40)    # [C2] pursuit reaction lag searched per eye (was fixed 0.15 s; measured 0.06-0.34 s)
    # [E1] repeat check: the same dot looked at twice (9-point stage and the live revisit ~20 s later) must give the
    # same pupil position apart from a shift shared by all dots (headset slip). Good sessions: <= 5.3 camera px;
    # a wrong detection: 9-25 px. A dot whose two looks differ by more than this is shown a third time.
    REPEAT_FLAG_CAM_PX = 8.0
    # [E2] path check: a static point predicted by the model trained on everything else (incl. ~1,800 pursuit frames)
    # is flagged when its error exceeds max(ANCHOR_FLAG_MIN_PX, ANCHOR_FLAG_RATIO * median of all points).
    # On 5 saved sessions this flagged only the two clear outliers (210 and 145 px).
    ANCHOR_FLAG_MIN_PX = 120.0
    ANCHOR_FLAG_RATIO = 2.5
    # [S2] soft influence: anchor weight 1 while its held-out error is < 0.4 x flag limit, then falling: 0.58 at the
    # flag limit, 0.23 at twice it (soft_weights). Nothing is removed.
    # [S3] map chain: a fine-tune refits the map but stays near the current one (the reference). PRIOR_STRENGTH sets
    # how stiff the reference is (0 = ignore it). SLIP_LEARN_RATE: share of a newly measured slip applied per session.
    # Tested with research/finetune_sim.py: 0.2 protected the old dots best (held out 48 -> 64 px vs 66-77 for
    # 0-0.05). Applying the full measured slip was best within a session (later dots 63 -> 30 px; 70 % -> 34 px).
    PRIOR_STRENGTH = 0.2
    SLIP_LEARN_RATE = 1.0
    # [C4] eye fusion weight = confidence / (held-out error of that eye)^2 instead of confidence only
    ACCURACY_WEIGHTED_FUSION = True

    def __init__(self, screen_w: int = 1920, screen_h: int = 1080):
        self.screen_w = int(screen_w)
        self.screen_h = int(screen_h)

        self.grid_px = [(int(round(fx * self.screen_w)), int(round(fy * self.screen_h))) for fx, fy in self.GRID_NORMALIZED]
        self.val_px = [(int(round(fx * self.screen_w)), int(round(fy * self.screen_h))) for fx, fy in self.VAL_NORMALIZED]
        self.pursuit_px = [
            ((int(round(p1[0] * self.screen_w)), int(round(p1[1] * self.screen_h))),
             (int(round(p2[0] * self.screen_w)), int(round(p2[1] * self.screen_h))))
            for (p1, p2) in self.TRACKING_PATHS_NORMALIZED
        ]

        # Model coefficients: shape (6, 2); col 0 -> screen_x, col 1 -> screen_y
        self.w_left: Optional[np.ndarray] = None
        self.w_right: Optional[np.ndarray] = None
        # Thin Plate Spline models (Regularized non-linear coordinate mapping)
        self.tps_left: Optional[ThinPlateSpline2D] = None
        self.tps_right: Optional[ThinPlateSpline2D] = None
        # Per-eye pupil normalisation (mean_x, mean_y, std_x, std_y)
        self.norm_left: Tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)
        self.norm_right: Tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)

        self.is_calibrated = False
        self.eye_err: Dict[str, Optional[float]] = {"left": None, "right": None}
        self.lag: Dict[str, Optional[float]] = {"left": None, "right": None}
        # [R1] headset-slip correction (screen px) from Quick Re-centre, added to every fused prediction
        self.offset: Tuple[float, float] = (0.0, 0.0)
        self.anchors: Optional[Dict[str, Any]] = None          # static training points (saved for fine-tuning)
        self.metrics: Dict[str, Any] = {}
        self.pursuit_records: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------ features
    @staticmethod
    def _feat_norm(u: float, v: float, deg: int = 2) -> np.ndarray:
        """Bivariate polynomial basis on normalised coordinates: 6 terms (degree 2) or 10 terms (degree 3)."""
        f = [1.0, u, v, u * v, u * u, v * v]
        if deg >= 3:
            f += [u ** 3, u * u * v, u * v * v, v ** 3]
        return np.array(f, dtype=np.float64)

    @staticmethod
    def _feat_mat(pts, deg: int = 2) -> np.ndarray:
        """_feat_norm for many points at once (same terms, same order), vectorised: fits were 70 % feature building."""
        P = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
        u, v = P[:, 0], P[:, 1]
        cols = [np.ones(len(P)), u, v, u * v, u * u, v * v]
        if deg >= 3:
            cols += [u * u * u, u * u * v, u * v * v, v * v * v]
        return np.column_stack(cols)

    @staticmethod
    def _deg_of(W: np.ndarray) -> int:
        return 3 if W.shape[0] == 10 else 2

    @staticmethod
    def _normalize(pts: np.ndarray, norm: Tuple[float, float, float, float]) -> np.ndarray:
        mx, my, sx, sy = norm
        out = np.empty_like(pts, dtype=np.float64)
        out[:, 0] = (pts[:, 0] - mx) / sx
        out[:, 1] = (pts[:, 1] - my) / sy
        return out

    @staticmethod
    def _compute_norm(pts: np.ndarray) -> Tuple[float, float, float, float]:
        mx, my = float(np.mean(pts[:, 0])), float(np.mean(pts[:, 1]))
        sx, sy = float(np.std(pts[:, 0])), float(np.std(pts[:, 1]))
        return mx, my, max(sx, 1.0), max(sy, 1.0)

    @staticmethod
    def _tukey_filter(pts: np.ndarray) -> np.ndarray:
        """Tukey IQR fence outlier filter for raw pupil samples."""
        if len(pts) < 4:
            return pts
        q1 = np.percentile(pts, 25, axis=0)
        q3 = np.percentile(pts, 75, axis=0)
        iqr = q3 - q1
        lo = q1 - 1.5 * iqr
        hi = q3 + 1.5 * iqr
        mask = np.all((pts >= lo) & (pts <= hi), axis=1)
        clean = pts[mask]
        return clean if len(clean) >= 4 else pts

    @classmethod
    def fit_ridge_poly(cls, src_norm: np.ndarray, dst_pts: np.ndarray,
                       weights: Optional[np.ndarray] = None, reg: float = RIDGE_REG, deg: int = 2,
                       W_ref: Optional[np.ndarray] = None, prior: float = 0.0) -> Optional[np.ndarray]:
        """
        Weighted ridge-regularised polynomial fit on normalised inputs:
            W = (X^T diag(w) X + lambda I)^-1 X^T diag(w) Y
        """
        n = len(src_norm)
        if n < 6:
            return None
        X = cls._feat_mat(src_norm, deg)
        Y = np.asarray(dst_pts, dtype=np.float64)
        if weights is None:
            weights = np.ones(n, dtype=np.float64)
        sw = np.sqrt(np.asarray(weights, dtype=np.float64))[:, None]
        Xw = X * sw
        Yw = Y * sw

        xtx = Xw.T @ Xw
        k = X.shape[1]
        lam = reg * np.trace(xtx) / k
        reg_eye = np.eye(k, dtype=np.float64) * lam
        reg_eye[0, 0] = 0.0  # never regularise the bias term
        rhs = Xw.T @ Yw
        if W_ref is not None and prior > 0 and W_ref.shape == (k, 2):
            # [S3] pull toward the reference model: minimise |X W - Y|^2_w + lam_p |W - W_ref|^2
            lam_p = prior * np.trace(xtx) / k
            reg_eye = reg_eye + np.eye(k) * lam_p
            rhs = rhs + lam_p * W_ref
        try:
            return np.linalg.solve(xtx + reg_eye, rhs)
        except np.linalg.LinAlgError:
            W, *_ = np.linalg.lstsq(Xw, Yw, rcond=None)
            return W

    # ------------------------------------------------------------------ fitting
    def _collect_training(self, samples: List[Optional[Point]], targets: List[Tuple[int, int]],
                          pursuit_records: Optional[List[Dict[str, Any]]], eye: str):
        """Builds (src, dst, weights) for one eye from static anchors + pursuit frames."""
        src: List[Point] = []
        dst: List[Point] = []
        wts: List[float] = []
        for s, t in zip(samples, targets):
            if s is None:
                continue
            src.append((float(s[0]), float(s[1])))
            dst.append((float(t[0]), float(t[1])))
            wts.append(self.STATIC_WEIGHT)

        if pursuit_records:
            key_p = f"{eye}_pupil"
            key_c = f"{eye}_conf"
            for rec in pursuit_records:
                if rec.get("is_blink"):
                    continue
                p = rec.get(key_p)
                if p is None or float(rec.get(key_c, 0.0)) < self.PURSUIT_MIN_CONF:
                    continue
                tgt = rec.get("compensated_target") or rec.get("target")
                if tgt is None:
                    continue

                pid = rec.get("path_idx")
                tx, ty = float(tgt[0]), float(tgt[1])

                # Standard Reference / Geometric Ground Truth Constraint:
                # On horizontal paths (paths 0-5), the dot sweeps horizontally at constant elevation.
                # Strictly lock the target Y coordinate to the standard path elevation so vertical
                # pupil/eyelash flutter during horizontal tracking cannot contaminate the polynomial.
                if pid is not None and 0 <= pid < len(self.pursuit_px):
                    p_start, p_end = self.pursuit_px[pid]
                    if abs(p_start[1] - p_end[1]) < 2.0:  # Horizontal path
                        ty = float(p_start[1])
                    elif abs(p_start[0] - p_end[0]) < 2.0:  # Vertical path
                        tx = float(p_start[0])

                src.append((float(p[0]), float(p[1])))
                dst.append((tx, ty))
                wts.append(1.0)
        return src, dst, wts

    def _pursuit_rows(self, pursuit_records, eye: str):
        """Usable pursuit frames of one eye -> (pupils, targets, velocities, elapsed, path ids).
        Cached per eye for the same list object and length (one fit parses the same frames ~19 times)."""
        cache = self.__dict__.setdefault("_rows_cache", {})
        hit = cache.get(eye)
        n = len(pursuit_records or [])
        if hit is not None and hit[0] is pursuit_records and hit[1] == n:
            return hit[2]
        out = self._parse_pursuit_rows(pursuit_records, eye)
        cache[eye] = (pursuit_records, n, out)
        return out

    def _parse_pursuit_rows(self, pursuit_records, eye: str):
        P, T, V, E, I = [], [], [], [], []
        for rec in pursuit_records or []:
            p = rec.get(f"{eye}_pupil")
            if rec.get("is_blink") or p is None or float(rec.get(f"{eye}_conf", 0.0)) < self.PURSUIT_MIN_CONF:
                continue
            if rec.get("target") is None or rec.get("path_idx") is None:
                continue
            P.append(p)
            T.append(rec["target"])
            V.append(rec.get("target_velocity") or (0.0, 0.0))
            E.append(float(rec.get("elapsed_sec", 0.0)))
            I.append(int(rec["path_idx"]))
        return (np.array(P, float).reshape(-1, 2), np.array(T, float).reshape(-1, 2),
                np.array(V, float).reshape(-1, 2), np.array(E, float), np.array(I, int))

    def _robust_poly(self, src_norm, dst, w0, deg, iters: int = 4, W_ref=None, prior: float = 0.0):
        """Huber-reweighted ridge polynomial: frames far off the model (blinks, glances) lose weight."""
        X = self._feat_mat(src_norm, deg)
        w = w0.copy()
        W = None
        for _ in range(iters):
            W = self.fit_ridge_poly(src_norm, dst, w, deg=deg, W_ref=W_ref, prior=prior)
            if W is None:
                return None
            r = np.hypot(*(X @ W - dst).T)
            sc = max(1.4826 * float(np.median(r)), 5.0)
            w = w0 * np.minimum(1.0, 2.5 * sc / np.maximum(r, 1e-9))
        return W

    def _fit_eye_aligned(self, samples, targets, pursuit_records, eye: str):
        """[C1/C2] -> (W, norm, lag) or None. The static anchors define the mapping; each pursuit path is shifted
        by its own median offset against the statics-only model (drift), and the reaction lag is estimated.
        [S2] each anchor counts STATIC_WEIGHT x its consistency weight (self.anchor_w[eye], default 1).
        [S3] with a reference model (self.prior[eye] = (W_ref, norm_ref)) the fit stays near it (PRIOR_STRENGTH)."""
        aw = (getattr(self, "anchor_w", None) or {}).get(eye)
        st = [(s, t, (aw[i] if aw and i < len(aw) and aw[i] is not None else 1.0))
              for i, (s, t) in enumerate(zip(samples, targets)) if s is not None]
        if len(st) < 6:
            return None
        s_src = np.array([s for s, _, _ in st], float)
        s_dst = np.array([t for _, t, _ in st], float)
        s_w = np.array([w for _, _, w in st], float)
        P, T, V, E, I = self._pursuit_rows(pursuit_records, eye)
        ref = (getattr(self, "prior", None) or {}).get(eye)
        use_prior = ref is not None and ref[0] is not None and np.asarray(ref[0]).shape == (10, 2)
        norm = tuple(ref[1]) if use_prior else self._compute_norm(np.vstack([s_src, P]) if len(P) else s_src)
        s_n = self._normalize(s_src, norm)
        base = self.fit_ridge_poly(s_n, s_dst, deg=2)
        if base is None:
            return None
        lag = 0.15
        src, dst, w = [s_n], [s_dst], [self.STATIC_WEIGHT * s_w]
        if len(P) >= 100:
            p_n = self._normalize(P, norm)
            pred = self._feat_mat(p_n, 2) @ base
            best = None
            for lg in np.arange(self.LAG_RANGE[0], self.LAG_RANGE[1] + 1e-9, 0.02):
                tg = T - V * np.minimum(lg, E)[:, None]
                e = float(np.median(np.hypot(*(pred - tg).T)))
                if best is None or e < best[0]:
                    best = (e, lg)
            lag = float(best[1])
            tg = T - V * np.minimum(lag, E)[:, None]
            for pid in np.unique(I):
                k = (I == pid) & (E > 0.5)
                if k.sum() < 20:
                    continue
                off = np.median(pred[k] - tg[k], axis=0)
                src.append(p_n[k])
                dst.append(tg[k] + off)
                w.append(np.ones(int(k.sum())))
        deg = self.POLY_DEG if (len(src) > 1 or use_prior) else 2   # cubic only with pursuit data or a cubic reference
        W = self._robust_poly(np.vstack(src), np.vstack(dst), np.concatenate(w), deg,
                              W_ref=np.asarray(ref[0], float) if use_prior else None,
                              prior=self.PRIOR_STRENGTH if use_prior else 0.0)
        return None if W is None else (W, norm, lag)

    @classmethod
    def repeat_check(cls, first: List[Optional[Point]], second: List[Optional[Point]]):
        """[E1] -> (shift, residuals): shift = median(second - first) over dots seen twice (slip, camera px);
        residuals[i] = |second - first - shift| or None. Needs >= 4 dots seen twice."""
        idx = [i for i, (a, b) in enumerate(zip(first, second)) if a is not None and b is not None]
        res: List[Optional[float]] = [None] * len(first)
        if len(idx) < 4:
            return (0.0, 0.0), res
        d = np.array([(second[i][0] - first[i][0], second[i][1] - first[i][1]) for i in idx])
        shift = np.median(d, axis=0)
        for i, di in zip(idx, d):
            res[i] = float(np.hypot(*(di - shift)))
        return (float(shift[0]), float(shift[1])), res

    def anchor_loo(self, samples, targets, pursuit_records, eye: str) -> List[Optional[float]]:
        """[E2] error (screen px) of each static point predicted by a model fitted on all the OTHER data."""
        out: List[Optional[float]] = [None] * len(samples)
        if sum(1 for s in samples if s is not None) < 7:
            return out
        for i, s in enumerate(samples):
            if s is None:
                continue
            sub = list(samples)
            sub[i] = None
            r = self._fit_eye_aligned(sub, targets, pursuit_records, eye)
            p = None if r is None else self._predict_eye(s, r[0], r[1], None)
            if p is not None:
                out[i] = math.hypot(p[0] - targets[i][0], p[1] - targets[i][1])
        return out

    def flag_limit(self, loo: List[Optional[float]]) -> Optional[float]:
        vals = [v for v in loo if v is not None]
        if len(vals) < 5:
            return None
        return max(self.ANCHOR_FLAG_MIN_PX, self.ANCHOR_FLAG_RATIO * float(np.median(vals)))

    def flag_anchors(self, loo: List[Optional[float]]) -> List[int]:
        lim = self.flag_limit(loo)
        return [] if lim is None else [i for i, v in enumerate(loo) if v is not None and v > lim]

    def soft_weights(self, loo: List[Optional[float]]) -> List[float]:
        """[S2] consistency weight per anchor from its held-out error (1 = fully trusted)."""
        lim = self.flag_limit(loo)
        out = []
        for v in loo:
            if v is None or lim is None or v <= 0.4 * lim:
                out.append(1.0)                                    # normal agreement: full weight
            else:                                                  # 0.58 at the flag limit, 0.23 at twice it
                out.append((1.0 + 0.4 ** 2) / (1.0 + (v / lim) ** 2))
        return out

    def _eye_holdout_error(self, samples, targets, pursuit_records, eye: str) -> Optional[float]:
        """Mean error of each static anchor predicted by a model trained without it (true accuracy of this eye)."""
        errs = []
        for i, s in enumerate(samples):
            if s is None:
                continue
            sub = list(samples)
            sub[i] = None
            r = self._fit_eye_aligned(sub, targets, pursuit_records, eye)
            if r is None:
                continue
            p = self._predict_eye(s, r[0], r[1], None)
            if p is not None:
                errs.append(math.hypot(p[0] - targets[i][0], p[1] - targets[i][1]))
        return float(np.mean(errs)) if len(errs) >= 4 else None

    def fit(self,
            left_samples: List[Optional[Point]],
            right_samples: List[Optional[Point]],
            grid_targets: List[Tuple[int, int]],
            val_records: Optional[List[Dict[str, Any]]] = None,
            pursuit_records: Optional[List[Dict[str, Any]]] = None,
            static_quality: Optional[Dict[str, List[float]]] = None,
            static_val_records: Optional[List[Dict[str, Any]]] = None,
            anchor_weights: Optional[Dict[str, List[float]]] = None,
            reference: Optional["BinocularCalibrator"] = None) -> bool:
        """
        Fits Left and Right models. A sample of None means that eye had no usable
        detection at that grid point; the point is skipped for that eye only.
        Returns True if at least one eye could be fitted.
        """
        left_samples = list(left_samples)
        right_samples = list(right_samples)
        n_pts = min(len(grid_targets), max(len(left_samples), len(right_samples)))
        left_samples += [None] * (n_pts - len(left_samples))
        right_samples += [None] * (n_pts - len(right_samples))
        targets = list(grid_targets[:n_pts])

        new_w = {}
        new_tps = {}
        new_norm = {}
        cleaned_left = list(left_samples)
        cleaned_right = list(right_samples)

        self.eye_err = {"left": None, "right": None}
        self.lag = {"left": None, "right": None}
        self.anchor_w = anchor_weights or {}
        self.prior = {} if reference is None else {"left": (reference.w_left, reference.norm_left),
                                                   "right": (reference.w_right, reference.norm_right)}
        for eye, cur_samples in (("left", cleaned_left), ("right", cleaned_right)):
            if self.MODEL == "cubic_aligned":
                r = self._fit_eye_aligned(cur_samples, targets, pursuit_records, eye)
                if r is not None:
                    new_w[eye], new_norm[eye], self.lag[eye] = r
                    new_tps[eye] = None
                    if pursuit_records and self.ACCURACY_WEIGHTED_FUSION:
                        self.eye_err[eye] = self._eye_holdout_error(cur_samples, targets, pursuit_records, eye)
                    continue
            src, dst, wts = self._collect_training(cur_samples, targets, pursuit_records, eye)
            n_static = sum(1 for s in cur_samples if s is not None)
            if len(src) < 6 or n_static < 4:
                new_w[eye] = None
                new_tps[eye] = None
                new_norm[eye] = (0.0, 0.0, 1.0, 1.0)
                continue
            src_arr = np.array(src, dtype=np.float64)
            norm = self._compute_norm(src_arr)
            src_norm = self._normalize(src_arr, norm)
            dst_arr = np.array(dst, dtype=np.float64)
            wts_arr = np.array(wts, dtype=np.float64)

            # Fit Thin Plate Spline (Primary local deformation map)
            tps_model = ThinPlateSpline2D(reg=0.03)
            tps_model.fit(src_norm, dst_arr, wts_arr)

            # Fit Ridge Polynomial as robust baseline
            W = self.fit_ridge_poly(src_norm, dst_arr, wts_arr)

            # Robust Outlier Rejection for Static Anchors:
            # If a static anchor has an extreme residual (> 450 px and > 3.0x the median residual
            # of the other anchors, e.g. eyelid droop or eyelash catch), prune that corrupted
            # anchor for this eye and re-fit with clean data to prevent model warping.
            if n_static >= 6:
                anchor_res = []
                for s, (tx, ty) in zip(cur_samples, targets):
                    if s is None:
                        continue
                    p = self._predict_eye(s, W, norm, tps_model)
                    if p is not None:
                        anchor_res.append(math.hypot(p[0] - tx, p[1] - ty))
                if anchor_res:
                    med_res = float(np.median(anchor_res))
                    pruned = False
                    for idx, (s, (tx, ty)) in enumerate(zip(cur_samples, targets)):
                        if s is None:
                            continue
                        p = self._predict_eye(s, W, norm, tps_model)
                        if p is not None:
                            err = math.hypot(p[0] - tx, p[1] - ty)
                            if err > 450.0 and err > 3.0 * max(med_res, 35.0):
                                print(f"[Calibrator] Pruned corrupted static anchor Pt {idx + 1} for {eye}: "
                                      f"residual={err:.1f}px (median={med_res:.1f}px)")
                                cur_samples[idx] = None
                                pruned = True
                    if pruned:
                        src_clean, dst_clean, wts_clean = self._collect_training(cur_samples, targets, pursuit_records, eye)
                        n_clean = sum(1 for s in cur_samples if s is not None)
                        if len(src_clean) >= 6 and n_clean >= 4:
                            src_arr = np.array(src_clean, dtype=np.float64)
                            norm = self._compute_norm(src_arr)
                            src_norm = self._normalize(src_arr, norm)
                            dst_arr = np.array(dst_clean, dtype=np.float64)
                            wts_arr = np.array(wts_clean, dtype=np.float64)
                            tps_model = ThinPlateSpline2D(reg=0.03)
                            tps_model.fit(src_norm, dst_arr, wts_arr)
                            W = self.fit_ridge_poly(src_norm, dst_arr, wts_arr)

            new_w[eye] = W
            new_tps[eye] = tps_model
            new_norm[eye] = norm

        if new_w["left"] is None and new_w["right"] is None and new_tps["left"] is None and new_tps["right"] is None:
            return False

        self.w_left, self.norm_left = new_w["left"], new_norm["left"]
        self.w_right, self.norm_right = new_w["right"], new_norm["right"]
        self.tps_left = new_tps["left"]
        self.tps_right = new_tps["right"]
        self.is_calibrated = True
        self.pursuit_records = list(pursuit_records or [])
        # training data kept with the model, so Level 2 fine-tuning can build on it
        self.anchors = {"left": [None if p is None else [float(p[0]), float(p[1])] for p in cleaned_left],
                        "right": [None if p is None else [float(p[0]), float(p[1])] for p in cleaned_right],
                        "targets": [[float(t[0]), float(t[1])] for t in targets],
                        "weights": {e: list(v) for e, v in (self.anchor_w or {}).items()}}
        self.prior = {}                                   # the reference is only used while fitting
        self.metrics = self._evaluate_metrics(cleaned_left, cleaned_right, targets,
                                              val_records, self.pursuit_records, static_quality,
                                              static_val_records=static_val_records)
        return True

    # ------------------------------------------------------------------ prediction
    def _predict_eye(self, pupil: Optional[Point], W: Optional[np.ndarray],
                     norm: Tuple[float, float, float, float],
                     tps: Optional[ThinPlateSpline2D] = None) -> Optional[Point]:
        if pupil is None:
            return None
        mx, my, sx, sy = norm
        norm_pt = np.array([(pupil[0] - mx) / sx, (pupil[1] - my) / sy], dtype=np.float64)
        if tps is not None:
            pred_tps = tps.predict(norm_pt)
            if pred_tps is not None:
                return pred_tps
        if W is not None:
            f = self._feat_norm(norm_pt[0], norm_pt[1], self._deg_of(W))
            return float(f @ W[:, 0]), float(f @ W[:, 1])
        return None

    def predict(self,
                left_pupil: Optional[Point],
                right_pupil: Optional[Point],
                left_conf: float = 1.0,
                right_conf: float = 1.0) -> Tuple[Optional[Tuple[int, int]], str, Dict[str, Any]]:
        """
        Predicts screen coordinates using confidence-weighted Bayesian binocular fusion.
        Returns: ((screen_x, screen_y) clamped to screen, mode_str, details_dict)
        details_dict["raw_fused"] is the unclamped fused prediction.
        """
        if not self.is_calibrated:
            return None, "Not Calibrated", {}

        pred_l = self._predict_eye(left_pupil, self.w_left, self.norm_left, self.tps_left)
        pred_r = self._predict_eye(right_pupil, self.w_right, self.norm_right, self.tps_right)

        cl = max(0.0, float(left_conf)) if pred_l is not None else 0.0
        cr = max(0.0, float(right_conf)) if pred_r is not None else 0.0
        thr = self.FUSION_MIN_CONF
        ee = getattr(self, "eye_err", None) or {}
        el, er = ee.get("left"), ee.get("right")
        if self.ACCURACY_WEIGHTED_FUSION and el and er:
            # [C4] the more accurate eye counts more: weight = conf * (mean error / this eye's error)^2
            ref = 0.5 * (el + er)
            ql, qr = (ref / el) ** 2, (ref / er) ** 2
        else:
            ql = qr = 1.0

        if cl > thr and cr > thr:
            # Measure eye disparity
            disparity = math.hypot(pred_l[0] - pred_r[0], pred_l[1] - pred_r[1])
            if disparity > 260.0:
                # One eye is experiencing occlusion, lash droop, or outlier slip:
                # Dynamic soft attenuation of the eye with lower confidence
                if cl * ql > cr * qr:
                    wl = (cl * ql) ** 2
                    wr = (cr * qr) ** 2 * 0.15
                else:
                    wr = (cr * qr) ** 2
                    wl = (cl * ql) ** 2 * 0.15
                tot = wl + wr
                gx = (pred_l[0] * wl + pred_r[0] * wr) / tot
                gy = (pred_l[1] * wl + pred_r[1] * wr) / tot
                mode = "Disparity Compensated Fusion"
            else:
                wl, wr = cl * ql, cr * qr
                tot = wl + wr
                gx = (pred_l[0] * wl + pred_r[0] * wr) / tot
                gy = (pred_l[1] * wl + pred_r[1] * wr) / tot
                mode = "Binocular Fused"
        elif cl > thr:
            gx, gy = pred_l
            mode = "Left Eye Only"
        elif cr > thr:
            gx, gy = pred_r
            mode = "Right Eye Only"
        else:
            return None, "No Gaze Signal", {"pred_left": pred_l, "pred_right": pred_r, "raw_fused": None, "cl": cl, "cr": cr}

        ox, oy = getattr(self, "offset", (0.0, 0.0))
        gx, gy = gx + ox, gy + oy
        cgx = max(0, min(self.screen_w - 1, int(round(gx))))
        cgy = max(0, min(self.screen_h - 1, int(round(gy))))
        details = {"pred_left": pred_l, "pred_right": pred_r, "raw_fused": (gx, gy), "cl": cl, "cr": cr}
        return (cgx, cgy), mode, details

    # ------------------------------------------------------------------ metrics
    def _region_of(self, y: float) -> str:
        fy = y / float(self.screen_h)
        if fy < 0.35:
            return "up"
        if fy > 0.65:
            return "down"
        return "center"

    def _evaluate_metrics(self, left_samples, right_samples, grid_targets,
                          val_records, pursuit_records, static_quality,
                          static_val_records: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Computes diagnostic metrics strictly from measured data."""
        sw, sh = self.screen_w, self.screen_h
        regional: Dict[str, List[float]] = {"up": [], "center": [], "down": []}

        # ---- Training residuals per eye (static anchors through their own model)
        train = {}
        for eye, samples, W, norm, tps in (("left", left_samples, self.w_left, self.norm_left, self.tps_left),
                                           ("right", right_samples, self.w_right, self.norm_right, self.tps_right)):
            res_list = []
            ex, ey = [], []
            for s, (tx, ty) in zip(samples, grid_targets):
                p = self._predict_eye(s, W, norm, tps) if s is not None else None
                if p is None:
                    res_list.append(None)
                    continue
                dx, dy = p[0] - tx, p[1] - ty
                ex.append(dx)
                ey.append(dy)
                res_list.append(round(math.hypot(dx, dy), 1))
            train[eye] = {
                "residuals": res_list,
                "points_used": len(ex),
                "rms_x": round(float(np.sqrt(np.mean(np.square(ex)))), 1) if ex else None,
                "rms_y": round(float(np.sqrt(np.mean(np.square(ey)))), 1) if ey else None,
                "rms": round(float(np.sqrt(np.mean(np.square(ex) + np.square(ey)))), 1) if ex else None,
            }

        # ---- 9-Point Static Validation Evaluation
        sv_landings = []
        sv_errors = []
        for r in (static_val_records or []):
            if not isinstance(r, dict):
                continue
            tx, ty = r["target"]
            gz = r.get("gaze")
            err = r.get("error_px")
            if gz is not None:
                if err is None or err >= 900.0:
                    err = math.hypot(gz[0] - tx, gz[1] - ty)
                sv_errors.append(float(err))
                regional[self._region_of(ty)].append(float(err))
            sv_landings.append({
                "target_idx": r.get("target_idx", len(sv_landings)),
                "target": (int(tx), int(ty)),
                "gaze": (round(float(gz[0]), 1), round(float(gz[1]), 1)) if gz is not None else None,
                "error_px": round(float(err), 1) if err is not None and err < 900.0 else None,
            })

        sv_mean = float(np.mean(sv_errors)) if sv_errors else None
        sv_p95 = float(np.percentile(sv_errors, 95)) if sv_errors else None
        sv_max = float(np.max(sv_errors)) if sv_errors else None

        # ---- Validation (unseen points)
        val_errors_fused, val_errors_left, val_errors_right = [], [], []
        val_landings = []
        jit_f, jit_l, jit_r = [], [], []

        for rec in (val_records or []):
            tx, ty = rec["target"]
            pred, mode, dtls = self.predict(rec.get("left_median"), rec.get("right_median"),
                                            rec.get("left_conf", 1.0), rec.get("right_conf", 1.0))
            landing = {"target": (tx, ty), "fused": None, "left": None, "right": None, "mode": mode}
            if pred is not None:
                err_f = math.hypot(pred[0] - tx, pred[1] - ty)
                val_errors_fused.append(err_f)
                regional[self._region_of(ty)].append(err_f)
                landing["fused"] = pred
            if dtls.get("pred_left") is not None:
                pl = dtls["pred_left"]
                val_errors_left.append(math.hypot(pl[0] - tx, pl[1] - ty))
                landing["left"] = (round(pl[0], 1), round(pl[1], 1))
            if dtls.get("pred_right") is not None:
                pr = dtls["pred_right"]
                val_errors_right.append(math.hypot(pr[0] - tx, pr[1] - ty))
                landing["right"] = (round(pr[0], 1), round(pr[1], 1))
            val_landings.append(landing)
            if rec.get("jitter") is not None:
                jit_f.append(float(rec["jitter"]))
            if rec.get("jitter_left") is not None:
                jit_l.append(float(rec["jitter_left"]))
            if rec.get("jitter_right") is not None:
                jit_r.append(float(rec["jitter_right"]))

        has_val = len(val_errors_fused) > 0
        mean_err = float(np.mean(val_errors_fused)) if has_val else None
        p95_err = float(np.percentile(val_errors_fused, 95)) if has_val else None
        l_mean = float(np.mean(val_errors_left)) if val_errors_left else None
        r_mean = float(np.mean(val_errors_right)) if val_errors_right else None

        # Fallback to 9-point static validation if 4-point unseen validation was not run
        if mean_err is None and sv_mean is not None:
            mean_err = sv_mean
            p95_err = sv_p95
            has_val = True

        # ---- Smooth pursuit (live, unfiltered fused prediction vs. target)
        p_errs, c_errs = [], []
        dropouts = blinks = 0
        path_diags: Dict[int, Dict[str, Any]] = {}
        for prec in (pursuit_records or []):
            pid = prec.get("path_idx", 0)
            d = path_diags.setdefault(pid, {
                "path_idx": pid, "path_name": prec.get("path_name", f"Path {pid + 1}"),
                "frames": 0, "tracked": 0, "blinks": 0, "_e": [], "_c": [], "_d": []
            })
            d["frames"] += 1
            if prec.get("is_blink"):
                blinks += 1
                d["blinks"] += 1
                continue
            g = prec.get("raw_fused")
            if g is None:
                dropouts += 1
                continue
            tx, ty = prec["target"]
            p_err = math.hypot(g[0] - tx, g[1] - ty)
            c_tgt = prec.get("compensated_target") or (tx, ty)
            c_err = math.hypot(g[0] - c_tgt[0], g[1] - c_tgt[1])
            p_errs.append(p_err)
            c_errs.append(c_err)
            regional[self._region_of(ty)].append(c_err)
            d["tracked"] += 1
            d["_e"].append(p_err)
            d["_c"].append(c_err)
            if prec.get("disparity_px") is not None:
                d["_d"].append(prec["disparity_px"])

        for d in path_diags.values():
            d["mean_error_px"] = round(float(np.mean(d["_e"])), 1) if d["_e"] else None
            d["rms_error_px"] = round(float(np.sqrt(np.mean(np.square(d["_e"])))), 1) if d["_e"] else None
            d["compensated_error_px"] = round(float(np.mean(d["_c"])), 1) if d["_c"] else None
            d["mean_disparity_px"] = round(float(np.mean(d["_d"])), 1) if d["_d"] else None
            for k in ("_e", "_c", "_d"):
                del d[k]

        pursuit_mean = float(np.mean(p_errs)) if p_errs else None
        pursuit_rms = float(np.sqrt(np.mean(np.square(p_errs)))) if p_errs else None
        pursuit_c_mean = float(np.mean(c_errs)) if c_errs else None
        pursuit_c_rms = float(np.sqrt(np.mean(np.square(c_errs)))) if c_errs else None

        # ---- Regional accuracy (validation + compensated pursuit)
        regional_out = {k: {"mean_error_px": round(float(np.mean(v)), 1) if v else None, "samples": len(v)}
                        for k, v in regional.items()}

        # ---- AAC key hit rates (only meaningful with validation)
        hits = {}
        for key_px in (40, 60, 80, 100):
            hits[f"hit_{key_px}"] = round(_rayleigh_hit_rate(key_px / 2.0, mean_err), 1) if has_val else None

        # ---- Clinical grade
        if mean_err is None:
            grade, grade_color = "N/A [NO VALIDATION DATA]", "#8b949e"
        elif mean_err <= 35.0:
            grade, grade_color = "A [EXCELLENT - CLINICAL GRADE]", "#3fb950"
        elif mean_err <= 50.0:
            grade, grade_color = "B [GOOD - ACCESSIBLE]", "#00e5ff"
        elif mean_err <= 75.0:
            grade, grade_color = "C [ACCEPTABLE - USABLE]", "#e3b341"
        else:
            grade, grade_color = "D [POOR - RECALIBRATION ADVISED]", "#f85149"

        r1 = lambda v: None if v is None else round(float(v), 1)
        r2 = lambda v: None if v is None else round(float(v), 2)

        return {
            "screen_w": sw, "screen_h": sh,
            "grid_px": list(self.grid_px), "val_px": list(self.val_px), "pursuit_px": list(self.pursuit_px),
            "model": "Robust cubic, drift-aligned pursuit" if self.MODEL == "cubic_aligned" else "Thin plate spline",
            "eye_holdout_error_px": {k: (None if v is None else round(v, 1))
                                     for k, v in (getattr(self, "eye_err", None) or {}).items()},
            "pursuit_lag_s": getattr(self, "lag", None),
            "has_validation": has_val,
            "mean_error_px": r1(mean_err), "p95_error_px": r1(p95_err),
            "left_mean_error_px": r1(l_mean), "right_mean_error_px": r1(r_mean),
            "val_errors_fused": [round(e, 1) for e in val_errors_fused],
            "val_errors_left": [round(e, 1) for e in val_errors_left],
            "val_errors_right": [round(e, 1) for e in val_errors_right],
            "val_landings": val_landings,
            "jitters": [round(j, 2) for j in jit_f],
            "jitters_left": [round(j, 2) for j in jit_l],
            "jitters_right": [round(j, 2) for j in jit_r],
            "mean_jitter": r2(np.mean(jit_f)) if jit_f else None,
            "mean_jitter_left": r2(np.mean(jit_l)) if jit_l else None,
            "mean_jitter_right": r2(np.mean(jit_r)) if jit_r else None,
            "train_left": train["left"], "train_right": train["right"],
            "static_quality_left": list((static_quality or {}).get("left", [])),
            "static_quality_right": list((static_quality or {}).get("right", [])),
            "regional": regional_out,
            **hits,
            "grade": grade, "grade_color": grade_color,
            "static_val_records": sv_landings,
            "static_val_landings": sv_landings,
            "static_val_mean_err": r1(sv_mean),
            "static_val_p95_err": r1(sv_p95),
            "static_val_max_err": r1(sv_max),
            "pursuit_mean_px": r1(pursuit_mean), "pursuit_rmse": r1(pursuit_rms),
            "pursuit_compensated_mean_px": r1(pursuit_c_mean), "pursuit_compensated_rmse": r1(pursuit_c_rms),
            "pursuit_count": len(p_errs), "pursuit_frames": len(pursuit_records or []),
            "pursuit_dropout_frames": dropouts, "pursuit_blink_frames": blinks,
            "pursuit_path_diagnostics": list(path_diags.values()),
            "calibrated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    # ------------------------------------------------------------------ persistence
    def save(self, filepath: str = "binocular_calibration.json"):
        """Persists models + metrics; pursuit frames go to pursuit_telemetry.json next to it."""
        if not self.is_calibrated:
            return
        data = {
            "screen": [self.screen_w, self.screen_h],
            "model_type": "Binocular_Cubic_Aligned" if self.MODEL == "cubic_aligned" else "Binocular_Thin_Plate_Spline_and_Ridge_Poly",
            "pupil_algorithm": self.metrics.get("pupil_algorithm", "Unknown"),
            "w_left": self.w_left.tolist() if self.w_left is not None else None,
            "w_right": self.w_right.tolist() if self.w_right is not None else None,
            "tps_left": self.tps_left.to_dict() if self.tps_left is not None else None,
            "tps_right": self.tps_right.to_dict() if self.tps_right is not None else None,
            "norm_left": list(self.norm_left),
            "norm_right": list(self.norm_right),
            "eye_err": getattr(self, "eye_err", None),
            "offset": list(getattr(self, "offset", (0.0, 0.0))),
            "lag": getattr(self, "lag", None),
            "anchors": getattr(self, "anchors", None),
            "metrics": self.metrics,
            "calibrated_at": self.metrics.get("calibrated_at", time.strftime("%Y-%m-%d %H:%M:%S")),
        }
        tmp = filepath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, filepath)

        telemetry_file = os.path.join(os.path.dirname(os.path.abspath(filepath)), "pursuit_telemetry.json")
        if not self.pursuit_records and os.path.exists(telemetry_file):
            return      # a loaded calibration (Re-centre, slip-only fine-tune) has no frames: keep the saved ones
        try:
            with open(telemetry_file, "w", encoding="utf-8") as f_tel:
                json.dump({
                    "calibrated_at": data["calibrated_at"],
                    "pupil_algorithm": data.get("pupil_algorithm", "Unknown"),
                    "screen": [self.screen_w, self.screen_h],
                    "total_frames": len(self.pursuit_records),
                    "summary_by_path": self.metrics.get("pursuit_path_diagnostics", []),
                    "frames": self.pursuit_records,
                }, f_tel, indent=1)
        except Exception as e:
            print(f"[Calibrator] Could not write pursuit_telemetry.json: {e}")

    def save_archive(self, archive_dir: str, algorithm_name: str, root_dir: Optional[str] = None):
        """
        Saves full historical session archive into archive_dir:
          1. calibration.json
          2. pursuit_telemetry.json
          3. static_validation.json
          4. summary.txt
        And updates active root calibration files if root_dir is provided.
        """
        os.makedirs(archive_dir, exist_ok=True)
        self.metrics["pupil_algorithm"] = algorithm_name
        calib_archive = os.path.join(archive_dir, "calibration.json")
        self.save(calib_archive)

        # Save static_validation.json
        static_val_path = os.path.join(archive_dir, "static_validation.json")
        try:
            with open(static_val_path, "w", encoding="utf-8") as f:
                json.dump({
                    "calibrated_at": self.metrics.get("calibrated_at", time.strftime("%Y-%m-%d %H:%M:%S")),
                    "pupil_algorithm": algorithm_name,
                    "screen": [self.screen_w, self.screen_h],
                    "mean_error_px": self.metrics.get("static_val_mean_err"),
                    "max_error_px": self.metrics.get("static_val_max_err"),
                    "points": self.metrics.get("static_val_records", []),
                }, f, indent=2)
        except Exception as e:
            print(f"[Calibrator] Could not save static_validation.json: {e}")

        # Save summary.txt
        summary_path = os.path.join(archive_dir, "summary.txt")
        try:
            self._write_summary_txt(summary_path, algorithm_name)
        except Exception as e:
            print(f"[Calibrator] Could not save summary.txt: {e}")

        # Sync active root files
        if root_dir is not None and os.path.isdir(root_dir):
            try:
                import shutil
                shutil.copy2(calib_archive, os.path.join(root_dir, "binocular_calibration.json"))
                tel_archive = os.path.join(archive_dir, "pursuit_telemetry.json")
                if os.path.exists(tel_archive):
                    shutil.copy2(tel_archive, os.path.join(root_dir, "pursuit_telemetry.json"))
            except Exception as e:
                print(f"[Calibrator] Could not sync active root calibration files: {e}")

    def _write_summary_txt(self, filepath: str, algorithm_name: str):
        m = self.metrics
        sw, sh = self.screen_w, self.screen_h
        ts = m.get("calibrated_at", time.strftime("%Y-%m-%d %H:%M:%S"))
        grade = m.get("grade", "N/A")
        mean_err = m.get("mean_error_px")
        p95_err = m.get("p95_error_px")
        jitter = m.get("mean_jitter")
        p_rmse = m.get("pursuit_rmse")
        p_comp = m.get("pursuit_compensated_rmse")
        p_cnt = m.get("pursuit_count", 0)
        p_tot = m.get("pursuit_frames", 0)

        lines = [
            "=" * 80,
            "BINOCULAR EYE TRACKING MODULE — CALIBRATION SESSION REPORT",
            "=" * 80,
            f"Timestamp:        {ts}",
            f"Algorithm:        {algorithm_name}",
            f"Screen:           {sw} x {sh} px",
            f"Model Type:       {m.get('model') or 'n/a'}",
            f"Status / Grade:   {grade}",
            "",
            "--- ACCURACY & PRECISION SUMMARY ---",
            # mean_error_px = the unseen validation dots (4 VAL dots, or the fine-tune's held-out new dots)
            f"Validation Mean Error:      {mean_err:.1f} px (~{mean_err / 39.5:.2f} deg)" if mean_err is not None else "Validation Mean Error:      N/A",
            f"Validation 95% Error:       {p95_err:.1f} px (~{p95_err / 39.5:.2f} deg)" if p95_err is not None else "Validation 95% Error:       N/A",
            *([f"9-Point Revisit Mean Error: {m['static_val_mean_err']:.1f} px (dots also used for training)"]
              if (m.get("static_val_mean_err") is not None and m.get("val_errors_fused")) else []),
            f"Fixation Jitter (Fused):    {jitter:.1f} px (~{jitter / 39.5:.2f} deg)" if jitter is not None else "Fixation Jitter (Fused):    N/A",
            f"Pursuit Tracking RMSE:      {p_rmse:.1f} px (~{p_rmse / 39.5:.2f} deg)" if p_rmse is not None else "Pursuit Tracking RMSE:      N/A",
            f"Pursuit Lag-Compensated:    {p_comp:.1f} px (~{p_comp / 39.5:.2f} deg)" if p_comp is not None else "Pursuit Lag-Compensated:    N/A",
            f"Pursuit Frames Tracked:     {p_cnt} / {p_tot} frames ({100.0 * p_cnt / max(1, p_tot):.1f}%)",
            "",
            "--- 9-POINT STATIC VALIDATION DETAILS ---",
            f"{'Point':<8}{'Target (X, Y)':<20}{'Gaze Landing (X, Y)':<24}{'Error (px)':<14}{'Rating':<12}",
            "-" * 78,
        ]

        sv_records = m.get("static_val_records") or []
        for i, r in enumerate(sv_records):
            tx, ty = r.get("target", (0, 0))
            gz = r.get("gaze")
            err = r.get("error_px")
            tgt_str = f"({tx:4d}, {ty:4d})"
            if gz is not None:
                gz_str = f"({gz[0]:6.1f}, {gz[1]:6.1f})"
                err_str = f"{err:5.1f} px"
                if err <= 35.0:
                    rating = "EXCELLENT"
                elif err <= 50.0:
                    rating = "GOOD"
                elif err <= 75.0:
                    rating = "ACCEPTABLE"
                else:
                    rating = "DRIFT"
            else:
                gz_str = "No Signal"
                err_str = "n/a"
                rating = "MISSED"
            lines.append(f"P{i + 1:<7}{tgt_str:<20}{gz_str:<24}{err_str:<14}{rating:<12}")

        lines.extend([
            "",
            "--- STATIC ANCHOR MODEL RESIDUALS ---",
        ])
        tl = m.get("train_left") or {}
        tr = m.get("train_right") or {}
        lines.append(f"Left Eye Anchors:   {tl.get('points_used', 0)}/{len(tl.get('residuals') or []) or 9} fitted · RMS: {tl.get('rms', 'n/a')} px (X: {tl.get('rms_x', 'n/a')}, Y: {tl.get('rms_y', 'n/a')})")
        lines.append(f"Right Eye Anchors:  {tr.get('points_used', 0)}/{len(tr.get('residuals') or []) or 9} fitted · RMS: {tr.get('rms', 'n/a')} px (X: {tr.get('rms_x', 'n/a')}, Y: {tr.get('rms_y', 'n/a')})")

        lines.extend([
            "",
            "--- PREDICTED AAC KEY HIT RATES ---",
            f"40 px Key (Dense Grid):     {m.get('hit_40', 'n/a')}%",
            f"60 px Key (Standard Grid):  {m.get('hit_60', 'n/a')}%",
            f"80 px Key (Accessible):     {m.get('hit_80', 'n/a')}%",
            f"100 px Key (Macro Target):  {m.get('hit_100', 'n/a')}%",
            "=" * 80,
        ])

        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def load_pursuit(self, filepath: str = "binocular_calibration.json") -> List[Dict[str, Any]]:
        """Pursuit frames saved next to a calibration file (pursuit_telemetry.json), [] if missing."""
        f = os.path.join(os.path.dirname(os.path.abspath(filepath)), "pursuit_telemetry.json")
        try:
            with open(f, "r", encoding="utf-8") as fh:
                return list(json.load(fh).get("frames", []))
        except Exception:
            return []

    def load(self, filepath: str = "binocular_calibration.json") -> bool:
        """Loads fitted calibration models from JSON."""
        if not os.path.exists(filepath):
            return False
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                d = json.load(f)
            self.screen_w, self.screen_h = [int(v) for v in d.get("screen", [1920, 1080])]
            wl, wr = d.get("w_left"), d.get("w_right")
            self.w_left = np.array(wl, dtype=np.float64) if wl is not None else None
            self.w_right = np.array(wr, dtype=np.float64) if wr is not None else None
            if "tps_left" in d and d["tps_left"] is not None:
                self.tps_left = ThinPlateSpline2D.from_dict(d["tps_left"])
            if "tps_right" in d and d["tps_right"] is not None:
                self.tps_right = ThinPlateSpline2D.from_dict(d["tps_right"])
            self.norm_left = tuple(d.get("norm_left", [0.0, 0.0, 1.0, 1.0]))
            self.norm_right = tuple(d.get("norm_right", [0.0, 0.0, 1.0, 1.0]))
            self.eye_err = d.get("eye_err") or {"left": None, "right": None}
            self.lag = d.get("lag") or {"left": None, "right": None}
            self.offset = tuple(d.get("offset") or (0.0, 0.0))
            self.anchors = d.get("anchors")
            self.metrics = d.get("metrics", {})
            self.is_calibrated = (
                self.w_left is not None or self.w_right is not None or
                self.tps_left is not None or self.tps_right is not None
            )
            return self.is_calibrated
        except Exception as e:
            print(f"[Calibrator] Failed to load {filepath}: {e}")
            self.is_calibrated = False
            return False
