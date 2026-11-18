#!/usr/bin/env python3
"""
recorder.py - saves the raw eye camera frames so detector and calibration changes can be
replayed offline on real eye movement (see research/replay.py).

A recording is a folder recordings/<YYYYMMDD_HHMMSS>_<tag>/ containing:
  left/<seq>.jpg, right/<seq>.jpg   grayscale frames as the detector saw them (after flips), JPEG q95
  frames.csv   eye, seq, capture time, live detector result (pupil x/y, confidence, latency)
  events.csv   time, stage, phase, target x/y  (written by the calibration overlay; empty otherwise)
  meta.json    algorithm, flips, boundary cages, screen size, start time, link to a calibration archive

Frames are encoded and written by one background thread, so recording never slows detection.
If the disk cannot keep up, frames are dropped (counted in meta.json) rather than delaying tracking.
"""

import os
import csv
import json
import time
import queue
import threading
from typing import Optional, Dict, Any

import cv2
import numpy as np

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REC_ROOT = os.path.join(_THIS_DIR, "recordings")


class Recorder:
    def __init__(self, tag: str, meta: Dict[str, Any], duration_s: Optional[float] = None):
        stamp = time.strftime("%Y%m%d_%H%M%S")
        self.name = f"{stamp}_{tag}"
        self.dir = os.path.join(REC_ROOT, self.name)
        for eye in ("left", "right"):
            os.makedirs(os.path.join(self.dir, eye), exist_ok=True)
        self.meta = dict(meta)
        self.meta.update({"name": self.name, "tag": tag, "started": time.time(), "duration_s": duration_s})
        self.t_end = None if duration_s is None else time.time() + float(duration_s)
        self.active = True
        self.count = {"left": 0, "right": 0}
        self.dropped = 0
        self._q: "queue.Queue" = queue.Queue(maxsize=240)
        self._frames = open(os.path.join(self.dir, "frames.csv"), "w", newline="", encoding="utf-8")
        self._fw = csv.writer(self._frames)
        self._fw.writerow(["eye", "seq", "t", "pupil_x", "pupil_y", "conf", "latency_ms"])
        self._events = open(os.path.join(self.dir, "events.csv"), "w", newline="", encoding="utf-8")
        self._ew = csv.writer(self._events)
        self._ew.writerow(["t", "stage", "phase", "target_x", "target_y"])
        self._ev_lock = threading.Lock()
        self._thread = threading.Thread(target=self._writer, daemon=True, name=f"Recorder-{tag}")
        self._thread.start()

    # ------------------------------------------------------------------ called from detection threads
    def add_frame(self, eye: str, seq: int, t: float, frame: np.ndarray, pupil, conf: float, lat: float):
        if not self.active:
            return
        if self.t_end is not None and time.time() >= self.t_end:
            self.stop()
            return
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        try:
            self._q.put_nowait((eye, seq, t, gray, pupil, conf, lat))
        except queue.Full:
            self.dropped += 1

    # ------------------------------------------------------------------ called from the calibration overlay
    def mark(self, stage: str, phase: str, target=None):
        if not self.active:
            return
        tx, ty = (target if target is not None else ("", ""))
        with self._ev_lock:
            self._ew.writerow([f"{time.time():.4f}", stage, phase, tx, ty])

    def link(self, key: str, value):
        """Store extra info in meta.json (e.g. the calibration archive folder)."""
        self.meta[key] = value

    def stop(self):
        if not self.active:
            return
        self.active = False
        self._q.put(None)

    def seconds_left(self) -> Optional[float]:
        return None if self.t_end is None else max(0.0, self.t_end - time.time())

    # ------------------------------------------------------------------ writer thread
    def _writer(self):
        while True:
            item = self._q.get()
            if item is None:
                break
            eye, seq, t, gray, pupil, conf, lat = item
            cv2.imwrite(os.path.join(self.dir, eye, f"{seq:07d}.jpg"), gray, [cv2.IMWRITE_JPEG_QUALITY, 95])
            px, py = (f"{pupil[0]:.2f}", f"{pupil[1]:.2f}") if pupil is not None else ("", "")
            self._fw.writerow([eye, seq, f"{t:.4f}", px, py, f"{conf:.3f}", f"{lat:.2f}"])
            self.count[eye] += 1
        self._frames.close()
        with self._ev_lock:
            self._events.close()
        self.meta.update({"stopped": time.time(), "frames": self.count, "dropped": self.dropped})
        with open(os.path.join(self.dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(self.meta, f, indent=2)
        print(f"[Recorder] saved {self.count} frames ({self.dropped} dropped) -> {self.dir}")
