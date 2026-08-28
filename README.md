# Augmentative Communication System for Plegia

This project consists of three main modules that work together to provide an eye-tracking-based communication system:
1. **Backend**: A FastAPI server that provides predictive text and sentence completion using local LLMs.
2. **GazeMouse**: A Python-based eye-tracking module that controls the system cursor using a webcam.
3. **Frontend**: A Flutter-based UI providing an on-screen keyboard optimized for gaze and dwell-clicking.

---

## 1. Backend (Python)

The backend handles the AI text predictions.

### Installation
Navigate to the `backend` directory and install the required Python libraries. It is highly recommended to use a virtual environment.

```powershell
cd backend
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

> **Note:** Ensure you have the required model files (`qwen2.5-0.5b-instruct-q8_0.gguf`, `Llama-3.2-3B-Instruct-Q4_K_M.gguf`) and `vocab.json` placed inside the `backend` directory before running.

### Running the Backend
Ensure your virtual environment is activated, then run:

```powershell
python main.py
```
The server will start and be available at `http://localhost:8000`.

---

## 2. GazeMouse (Python)

The GazeMouse module is responsible for detecting pupils and moving the mouse cursor.

### Installation
Navigate to the `GazeMouse` directory and install its dependencies. It is recommended to use a separate virtual environment for this module.

```powershell
cd GazeMouse
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

### Running GazeMouse
Ensure your virtual environment is activated, then launch the control panel:

```powershell
python launcher.py
```
This will open the EyeTracker Module Control Panel, where you can configure your camera, perform calibration, and start cursor control.

---

## 3. Frontend (Flutter)

The frontend is the main user interface featuring the gaze-optimized keyboard.

### Installation
Ensure you have the [Flutter SDK](https://docs.flutter.dev/get-started/install) installed on your system. Navigate to the `frontend` directory and fetch the required packages:

```powershell
cd frontend
flutter pub get
```

### Running the Frontend
Run the Flutter application (targeting Windows):

```powershell
flutter run -d windows
```
*(You can omit `-d windows` or replace it with another target device if needed)*
