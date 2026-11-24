# Eye Tracking Module
**Part of the Augmentative Communication System for Plegia**

This module provides the **Dual-Sensor (Binocular) Eye Tracking System**, empowering individuals with plegia or severe motor disabilities by tracking both eyes simultaneously.

---

## Key Features in this Milestone

1. **Dual Eye Video Capture (640x480 @ 30 FPS)**:
   - Independent capture thread per sensor (`SingleCameraStream`) via DirectShow (`CAP_DSHOW`); every frame carries a sequence number.
   - Frames are forced to 640x480 so cages and calibration models always share one coordinate system.
2. **One Shared Pupil-Detection Pipeline per Eye**:
   - `BinocularController` runs the active detector exactly once per new camera frame and publishes an `EyeSample` (frame, pupil centre, ellipse, confidence, latency).
   - The launcher, Eye View, calibration overlay and gaze engine only read these samples; no window runs its own detector and no frame is processed twice.
   - Algorithms: **ArcFit** (default), **Orlosky**, or **None** (detection off everywhere).
3. **Eyeball Boundary Cage**:
   - Independent elliptical cage per eye with X/Y positioning, radius, and horizontal/vertical squeeze (40%-160%).
   - Outside of the cage is filled with the mean colour of the inside, so the cage edge does not create a false high-contrast boundary.
4. **Binocular Gaze Calibration & Smooth Pursuit Engine**:
   - **Static 9-Point Calibration**: 8% screen margins, 1.3s shrinking approach rings, 1.1s 360° progress sweeps, and flash confirmation.
   - **8 Continuous Smooth Pursuit Paths**: 4.8s slow gliding across Top, Mid, Bottom, and Diagonal paths (~330 px/s).
   - **1.3s Visual Break with Shrinking Circle Animation**: Between every path, target pauses at start while a 75px circle collapses to 14px with countdown text to allow comfortable blinking and eye settling.
   - **Real-Time Live Pointer**: Electric Cyan reticle + motion trail rendered on screen during tracking so the user sees tracking smoothness live.
   - **Progressive In-Flight Re-Fitting**: Calibrator model re-fits progressively after each path during the 1.3s break, visibly tightening the pointer.
   - **Oculomotor Reaction Latency Compensation**: Pursuit training targets are where the dot was 150 ms earlier.
   - **Weighted Ridge Fit on Normalised Pupil Coordinates**: Static anchors weigh 8x a pursuit frame; pupil coordinates are zero-mean / unit-variance per eye before the degree-2 polynomial fit.
   - **No Placeholder Data**: A static point with too few accepted frames for an eye is skipped for that eye instead of being replaced by a fake sample.
   - **4-Point Unseen Spatial Validation**: Accuracy plus screen-space fixation jitter per eye and fused.
   - **Honest 9-Panel Diagnostic Chart** (`binocular_calibration_chart.png`): every panel is measured; a panel with no data says so instead of plotting an estimate. Grade is `N/A` when no validation was collected.
   - **Frame Telemetry**: every pursuit frame (pupils, per-eye and fused predictions, errors, blinks, dropouts) goes to `pursuit_telemetry.json`; `binocular_calibration.json` stays small (model + metrics).
5. **Real-Time Mouse Control (Raw & Filtered)**:
   - Direct raw gaze mapping for testing and evaluation.
   - Optional 1-Euro adaptive smoothing filter for clinical low-jitter performance.
   - Global hotkeys polled system-wide: **`[F5]`** toggle Mouse Control, **`[F6]`** emergency stop.
   - Physical fail-safe: pushing the real mouse into the top-left corner (somewhere the tracker did not place it) disables Mouse Control.
   - When both eyes are lost the cursor holds its last position; the 1-Euro filter resets after 0.35 s without signal so it never lags on recovery.
6. **Independent Hardware Orientation Flipping**:
   - Independent **Flip Horizontally (Mirror)** and **Flip Vertically (Invert)** for each eye.
7. **Persistent Settings**:
   - Camera indices, flip preferences, boundary cages, and active algorithm automatically persist to `binocular_settings.json`.

---

## Quick Start

Open terminal in this directory:
```powershell
cd "E:\Files and Other Things\Projects\Augmentative Communication System for Plegia\Eye Tracking with two sensor\Combined System\Eye Tracking Module"
python launcher.py
```

### Hotkeys Quick Reference

| Key | Context | Action |
| :--- | :--- | :--- |
| **`[F5]`** | Global / Anywhere | **Toggle Mouse Control ON / OFF** |
| **`[F6]`** | Global / Anywhere | **Emergency Stop (Disable Mouse Control)** |
| **`[Ctrl+E]`**| Launcher | Show / Close Dual Eye View Window |
| **`[ESC]`** | Calibration Overlay | Cancel Calibration |
| **`[W/A/S/D]` / Arrows** | Eye View Window | Move the active tab's boundary cage |
| **`[+] / [-]`** | Eye View Window | Grow / shrink base radius |
| **`[` `]` / `{` `}`** | Eye View Window | Squeeze H / Squeeze V |
| **`[C]` / `[R]`** | Eye View Window | Circle (1:1) / Reset All |

---

## Generated Files

| File | Content |
| :--- | :--- |
| `binocular_settings.json` | Camera indices, flips, boundary cages, active algorithm |
| `binocular_calibration.json` | Per-eye polynomial weights, normalisation, metrics (small) |
| `pursuit_telemetry.json` | Every smooth-pursuit frame + per-path summary |
| `binocular_calibration_chart.png` | 9-panel diagnostic chart |

## Module Map

| File | Role |
| :--- | :--- |
| `launcher.py` | Control panel (3 tabs), owns the controller and gaze engine |
| `binocular_controller.py` | Camera streams, shared detection workers, settings |
| `data/pupil_algorithm/` | Detector package; `extract_pupil_ellipse()` applies the cage |
| `eye_view_window.py` | Dual preview + boundary cage editor |
| `binocular_calibration_overlay.py` | Fullscreen calibration state machine |
| `binocular_calibrator.py` | Fitting, prediction, metrics, persistence |
| `binocular_calibration_reporter.py` | Chart generation |
| `gaze_engine.py` | Gaze -> cursor, hotkeys, fail-safe, 1-Euro filter |

---
