#!/usr/bin/env python3
"""
orlosky.py
==========
Algorithm 1: Orlosky Pupil Detector
Darkest-point seed + multi-threshold contour extraction.
"""

import time
from typing import Optional, Tuple
import cv2
import numpy as np

from .OrloskyPupilDetector import (
    crop_to_aspect_ratio,
    get_darkest_area,
    apply_binary_threshold,
    mask_outside_square,
    process_frames,
)


class OrloskyDetector:
    """Algorithm 1: Orlosky Pupil Detector."""
    NAME = "Orlosky"
    COLOR = (0, 215, 255)  # Gold BGR

    def __init__(self):
        pass

    def detect(self, frame_640x480: np.ndarray) -> Tuple[Optional[Tuple], float, float]:
        """
        Runs Orlosky detector on a 640x480 frame.
        Returns: (rotated_rect, latency_ms, confidence)
        """
        t0 = time.perf_counter()
        darkest = get_darkest_area(frame_640x480)
        if darkest is None:
            return None, (time.perf_counter() - t0) * 1000.0, 0.0

        gray = cv2.cvtColor(frame_640x480, cv2.COLOR_BGR2GRAY)
        dpv = gray[darkest[1], darkest[0]]

        t_strict  = mask_outside_square(apply_binary_threshold(gray, dpv,  5), darkest, 250)
        t_medium  = mask_outside_square(apply_binary_threshold(gray, dpv, 15), darkest, 250)
        t_relaxed = mask_outside_square(apply_binary_threshold(gray, dpv, 25), darkest, 250)

        result = process_frames(t_strict, t_medium, t_relaxed, frame_640x480, gray, darkest, False, False)
        dt = (time.perf_counter() - t0) * 1000.0

        if result is None or result == ((0, 0), (0, 0), 0):
            return None, dt, 0.0
        return result, dt, 1.0
