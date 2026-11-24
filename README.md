# Augmentative Communication System for Plegia

A communication system for people who cannot speak or move their hands (plegia, ALS): they type and speak
with their eyes. Version 1.2.0, Windows.

## What it has

- **Eye tracker:** two small infrared cameras on an eyeglass frame, one per eye. Pupil detection, calibration and
  a gaze-controlled Windows mouse.
- **AAC keyboard app:** large on-screen keyboard, word predictions, sentence suggestions, quick words and fixed
  phrases. Buttons are selected by **dwell** (looking at them) or with the **Spacebar**.
- **AI prediction:** two local language models (no internet needed) plus learned word statistics, per
  **profile** (e.g. *Test* and *Patient*). Sentences you have spoken are offered again when you start typing them.
- **Speech:** online neural voices, with the Windows voice as an automatic offline fallback.

## How it works

```
 eye cameras --> Eye tracker (Python, port 8001) --> moves the Windows mouse cursor
                                                        |
                                    the app's buttons react to the cursor (dwell / Spacebar)
                                                        |
 AAC app (Flutter) <-- word & sentence predictions, speech -- AI backend (Python, port 8000)
```

1. The tracker finds both pupils in the camera images and maps them to a screen position with your calibration.
2. That position drives the mouse cursor, smoothed so it stays calm.
3. Looking at a button for the dwell time (or pressing Space) selects it; the app predicts the next words and
   whole sentences, and **Speak** reads the message aloud.
4. The app starts the tracker and the backend itself and stops them when it closes.

## Folders

| Folder | Contents |
|---|---|
| `frontend/` | The AAC app (Flutter, Windows desktop) |
| `Eye Tracking Module/` | Eye tracker (`tracker_service.py`; `launcher.py` = standalone control panel) |
| `backend/` | AI prediction + speech server (`main.py`), language models, learning profiles |
| `logs/` | `tracker.log`, `backend.log`, setup logs |

## Start

- **This PC:** double-click **`start_aac.bat`**. It rebuilds the app only when the code changed, then opens it.
- **New PC:** copy this folder (without `backend/venv`), run **`setup_new_pc.bat`** once (installs what is missing,
  asks before big installs; `setup_new_pc.bat check` only reports), then use `start_aac.bat`.

## First use

1. **Eye Tracking > Sensors:** check that each camera shows the right eye.
2. **Eye Tracking > Calibration:** run a **Full Calibration** each time the headset is put on.
3. **Settings:** choose the prediction profile, dwell time and voice. Every setting is kept after a restart.
4. Switch the eye mouse on with **F5** (F6 stops it at any time).

## Helpful features

- **Quick Re-centre** (8 s) and **Level 2 Fine-tune** correct a headset that has slipped.
- **Auto re-centre while using** (off by default): learns the remaining cursor offset from the buttons you select.
- **System Status** (Settings) shows every part of the system and can restart the background programs.
- **Logs** in `logs/` help when something does not work.

Do not run `launcher.py` while the app is running: both would use the cameras.
