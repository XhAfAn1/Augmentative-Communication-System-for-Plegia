#!/usr/bin/env python3
"""
pupil_algorithm package
=======================
Pupil detection algorithms:
  ArcFit  - default. Reflection-aware visible-arc fit with eye profile (arc_pupil.py)
  Orlosky - multi-threshold contour extraction, kept as a stable fallback (orlosky.py)
  None    - detection off

Removed algorithms (PuReST, Pupil Labs, Hybrid Fusion, ApexPupil) are kept in
research/removed_algorithms/ for reference only.

All detectors operate in a fixed 640x480 coordinate frame. Frames of any
other size are resized before detection so boundary cages and calibration
models always share one coordinate system.
"""

from typing import Dict, Any, Optional, Tuple

import cv2
import numpy as np

from .orlosky import OrloskyDetector
from .arc_pupil import ArcFitDetector

FRAME_W = 640
FRAME_H = 480

ALGORITHM_CLASSES = {
    "ArcFit": ArcFitDetector,
    "Orlosky": OrloskyDetector,
}

ALGORITHM_NAMES = ["ArcFit", "Orlosky"]

ALGORITHM_COLORS = {
    "ArcFit": (80, 255, 160),        # Mint
    "Orlosky": (0, 215, 255),        # Gold
    "None": (140, 140, 140),         # Muted gray
}

ALGORITHM_DISPLAY = {
    "ArcFit": "🎯 ArcFit (Glint-aware visible-arc fit)",
    "Orlosky": "🟡 Orlosky (Multi-Threshold)",
    "None": "⚪ None (Disabled)",
}

DISPLAY_TO_NAME = {v: k for k, v in ALGORITHM_DISPLAY.items()}
for k in list(ALGORITHM_DISPLAY.keys()):
    DISPLAY_TO_NAME[k] = k


def to_display_name(name: str) -> str:
    """Converts canonical algorithm name to formatted display label."""
    return ALGORITHM_DISPLAY.get(str(name), ALGORITHM_DISPLAY["ArcFit"])


def to_canonical_name(label_or_name: str) -> str:
    """Converts display label or raw name back to canonical name."""
    s = str(label_or_name).strip()
    if s in DISPLAY_TO_NAME:
        return DISPLAY_TO_NAME[s]
    for k in ALGORITHM_DISPLAY:
        if k.lower() in s.lower():
            return k
    return "ArcFit"


def get_detector(name: str):
    """
    Instantiates and returns an independent detector by algorithm name.
    Returns None for the "None" (disabled) algorithm so callers never run
    a detector the user switched off.
    """
    canonical = to_canonical_name(name)
    if canonical == "None":
        return None
    cls = ALGORITHM_CLASSES.get(canonical, ArcFitDetector)
    return cls()


def normalize_frame(frame: np.ndarray) -> np.ndarray:
    """Guarantees a 640x480 BGR frame (resizes anything else)."""
    if frame is None:
        return None
    h, w = frame.shape[:2]
    if w != FRAME_W or h != FRAME_H:
        frame = cv2.resize(frame, (FRAME_W, FRAME_H), interpolation=cv2.INTER_AREA)
    if frame.ndim == 2:
        frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    return frame


def boundary_axes(b_dict: Dict[str, Any]) -> Tuple[int, int, int, int]:
    """Returns (cx, cy, rx, ry) of the elliptical cage from a boundary dict."""
    bx = int(b_dict.get("x", FRAME_W // 2))
    by = int(b_dict.get("y", FRAME_H // 2))
    br = int(b_dict.get("r", 110))
    sq_h = int(b_dict.get("squeeze_h", 100))
    sq_v = int(b_dict.get("squeeze_v", 100))
    rx = max(10, int(round(br * sq_h / 100.0)))
    ry = max(10, int(round(br * sq_v / 100.0)))
    return bx, by, rx, ry


def apply_cage(frame: np.ndarray, b_dict: Dict[str, Any]) -> Tuple[np.ndarray, Tuple[int, int, int, int]]:
    """
    Masks everything outside the elliptical eyeball cage. The outside is filled
    with the mean colour of the inside region rather than near-white, so the
    cage edge does not create an artificial high-contrast boundary for the
    edge-based detectors.
    """
    bx, by, rx, ry = boundary_axes(b_dict)
    h, w = frame.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.ellipse(mask, (bx, by), (rx, ry), 0, 0, 360, 255, -1)
    fill = cv2.mean(frame, mask=mask)[:3]
    caged = frame.copy()
    caged[mask == 0] = tuple(int(round(c)) for c in fill)
    return caged, (bx, by, rx, ry)


def extract_pupil_ellipse(frame, detector, b_dict: Optional[Dict[str, Any]] = None, t: Optional[float] = None):
    """
    Applies the eyeball boundary cage (if enabled) and runs the pupil detector.
    Returns: (rotated_rect, latency_ms, confidence) where rotated_rect is
    ((cx, cy), (w, h), angle) in 640x480 coordinates, or (None, latency_ms, 0.0).
    """
    if detector is None or frame is None:
        return None, 0.0, 0.0

    frame = normalize_frame(frame)
    b_en = bool(b_dict.get("enabled", True)) if b_dict else False

    if getattr(detector, "wants_cage", False):
        # Detector handles the cage itself: it sees the real image and treats the cage as a search
        # limit, so a pupil touching the cage edge is not cut off by painted-over pixels.
        cage = None
        if b_en:
            bx, by, rx, ry = boundary_axes(b_dict)
            cage = (bx, by, rx, ry)
        ret = detector.detect(frame, cage=cage) if t is None else detector.detect(frame, cage=cage, t=t)
    else:
        if not b_en:
            inp = frame
        else:
            inp, (bx, by, rx, ry) = apply_cage(frame, b_dict)
        ret = detector.detect(inp)
    if ret is None:
        return None, 0.0, 0.0

    if len(ret) >= 4:
        rect, lat, conf = ret[0], ret[1], ret[2]
    elif len(ret) == 3:
        rect, lat, conf = ret
    elif len(ret) == 2:
        rect, lat = ret
        conf = 1.0 if rect is not None else 0.0
    else:
        rect, lat, conf = ret[0], 0.0, 0.0

    if rect is None:
        return None, float(lat), 0.0

    if b_en:
        px, py = rect[0][0], rect[0][1]
        if ((px - bx) ** 2) / float(rx * rx) + ((py - by) ** 2) / float(ry * ry) > 1.15:
            return None, float(lat), 0.0
    return rect, float(lat), float(conf)


def extract_pupil(frame, detector, b_dict: Optional[Dict[str, Any]] = None):
    """
    Convenience wrapper returning only the pupil centre.
    Returns: ((px, py), latency_ms, confidence) or (None, latency_ms, 0.0)
    """
    rect, lat, conf = extract_pupil_ellipse(frame, detector, b_dict)
    if rect is None:
        return None, lat, 0.0
    return (float(rect[0][0]), float(rect[0][1])), lat, conf
