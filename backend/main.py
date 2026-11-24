from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import os
import json
import threading

# Import our backend logic
from backend import HybridKeyboardBackend
from tts_manager import tts_manager

app = FastAPI(title="Hybrid Keyboard API")

# Allow CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global backend instance
backend = None
backend_lock = threading.Lock()  # For state updates (learning, profile) and model reloads
fast_ai_lock = threading.Lock()  # For 0.5B word predictions
fast_lock = threading.Lock()     # For instant N-gram calls (never blocked by LLM)
sentence_lock = threading.Lock() # For the 3B sentence model: saving a spoken sentence no longer waits for it
# lock order: backend_lock -> sentence_lock -> fast_ai_lock -> fast_lock
_sentence_seq = 0                # number of the newest /api/complete_sentence request
_sentence_seq_lock = threading.Lock()

# Backend's own saved settings (shared by all profiles): GPU on/off survives a restart of the backend / app
SETTINGS_FILE = os.environ.get("AAC_BACKEND_SETTINGS") or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                                       "backend_settings.json")

def load_settings() -> dict:
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def save_setting(key: str, value):
    d = load_settings()
    d[key] = value
    try:
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, SETTINGS_FILE)             # never a half-written file
    except Exception as e:
        print(f"[Settings] could not save {key}: {e}")

class TextRequest(BaseModel):
    text: str
    context: Optional[str] = None   # /api/type: the whole typed text, so completions fit the sentence

class ProfileRequest(BaseModel):
    name: str

class SpacebarRequest(BaseModel):
    text: str

class GpuToggleRequest(BaseModel):
    enabled: bool

class TtsSpeakRequest(BaseModel):
    text: str
    engine: str = "edge_tts"
    voice: Optional[str] = None
    speed: float = 1.0
    pitch: float = 1.0
    temperature: float = 0.3
    play_now: bool = True

class SelectEngineRequest(BaseModel):
    engine: str

@app.on_event("startup")
async def startup_event():
    global backend
    fast_gguf_path = "qwen2.5-0.5b-instruct-q8_0.gguf"
    heavy_gguf_path = "Llama-3.2-3B-Instruct-Q4_K_M.gguf"
    vocab_path = "vocab.json"
    
    if os.path.exists(fast_gguf_path) and os.path.exists(heavy_gguf_path) and os.path.exists(vocab_path):
        print("Initializing backend engine on startup...")
        use_gpu = bool(load_settings().get("use_gpu", True))     # the user's last choice (Settings > GPU)
        backend = HybridKeyboardBackend(fast_gguf_path=fast_gguf_path, heavy_gguf_path=heavy_gguf_path,
                                        vocab_path=vocab_path, use_gpu=use_gpu)
    else:
        print("Backend not fully initialized on startup. Missing model or vocab file.")
        print(f"Looked for: {fast_gguf_path}, {heavy_gguf_path} and {vocab_path}")

@app.post("/api/spacebar_fast")
def predict_next_word_fast(req: SpacebarRequest):
    """Instant N-gram predictions (< 1ms). Uses its own lock, never blocked by LLM."""
    if not backend:
        raise HTTPException(status_code=503, detail="Backend not initialized.")
    with fast_lock:
        suggestions = backend.on_spacebar_fast(req.text)
    return {"suggestions": suggestions}

@app.post("/api/spacebar")
def predict_next_word(req: SpacebarRequest):
    if not backend:
        raise HTTPException(status_code=503, detail="Backend not initialized. Is the model downloaded?")

    # learning happens only through /api/submit_sentence (Speak); this only predicts
    with fast_ai_lock:
        suggestions = backend.on_spacebar(req.text)

    return {"suggestions": suggestions}

@app.post("/api/submit_sentence")
def submit_sentence(req: TextRequest):
    """Called when a user finishes and sends a full sentence.
    Updates the session history buffer and triggers personalization learning."""
    if not backend:
        raise HTTPException(status_code=503, detail="Backend not initialized.")

    with backend_lock:
        if req.text.strip():
            backend.on_sentence_submitted(req.text.strip())

    return {"status": "ok", "history_size": len(backend.session_history)}

@app.post("/api/type")
def predict_prefix(req: TextRequest):
    if not backend:
        raise HTTPException(status_code=503, detail="Backend not initialized. Is the model downloaded?")
    
    with fast_lock:
        suggestions = backend.on_letter_typed(req.text, req.context)
    return {"suggestions": suggestions}

@app.post("/api/phrases")
def phrase_memory(req: TextRequest):
    """Sentences this profile has spoken that start with the typed text (quick lookup, no model).
    The app asks this at every letter, so the sentence list keeps fitting what is typed."""
    if not backend:
        raise HTTPException(status_code=503, detail="Backend not initialized.")
    return {"suggestions": backend.profile.phrases_for(req.text, n=3)}

@app.post("/api/complete_sentence")
def complete_sentence(req: TextRequest):
    if not backend:
        raise HTTPException(status_code=503, detail="Backend not initialized. Is the model downloaded?")
    
    global _sentence_seq
    with _sentence_seq_lock:
        _sentence_seq += 1
        seq = _sentence_seq
    with sentence_lock:
        # a newer sentence request arrived while this one waited: skip the 3B run (the app ignores this reply anyway)
        if seq != _sentence_seq:
            return {"suggestions": [], "outdated": True}
        suggestions = backend.on_sentence_completion(req.text)
    return {"suggestions": suggestions}

@app.get("/api/profile")
def get_profile():
    """Active learning profile (e.g. Test / Patient) and the list of profiles."""
    if not backend:
        raise HTTPException(status_code=503, detail="Backend not initialized.")
    return {"active": backend.profile.name, "profiles": backend.list_profiles(), "phrases": len(backend.profile.phrases)}

@app.post("/api/profile")
def set_profile(req: ProfileRequest):
    """Switches the learning profile: predictions learn from and use only this profile's sentences."""
    if not backend:
        raise HTTPException(status_code=503, detail="Backend not initialized.")
    with backend_lock, fast_ai_lock, fast_lock:
        try:
            return backend.set_profile(req.name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/settings/gpu")
def get_gpu_settings():
    if not backend:
        import shutil, subprocess, llama_cpp
        device_name = None
        if shutil.which("nvidia-smi"):
            try:
                out = subprocess.check_output(
                    ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                    encoding="utf-8"
                )
                device_name = out.strip().splitlines()[0]
            except Exception:
                pass
        return {
            "gpu_available": bool(device_name),
            "device_name": device_name or "CPU",
            "gpu_enabled": False,
            "llama_cuda": llama_cpp.llama_supports_gpu_offload(),
            "active_mode": "Backend starting...",
        }
    return backend.get_gpu_info()

@app.post("/api/settings/gpu")
def set_gpu_settings(req: GpuToggleRequest):
    if not backend:
        raise HTTPException(status_code=503, detail="Backend not initialized.")
    with backend_lock, sentence_lock:          # no sentence may run while the models are reloaded
        with fast_ai_lock:
            info = backend.set_gpu_mode(req.enabled)
            save_setting("use_gpu", bool(req.enabled))          # used again at the next start
            return info

# -------------------------------------------------------------
# Text-to-Speech (TTS) Endpoints
# -------------------------------------------------------------
@app.get("/api/tts/voices")
def get_tts_voices():
    """Returns available TTS engines, voices, and metadata."""
    return tts_manager.get_available_voices()

@app.post("/api/tts/select_engine")
async def select_engine(req: SelectEngineRequest):
    """Called when an engine is selected in the UI:
    - Preloads Pocket TTS proactively in the background if selected.
    - Unloads Pocket TTS to free memory if switching away to another engine.
    """
    if req.engine == "pocket_tts":
        import asyncio
        asyncio.create_task(asyncio.to_thread(tts_manager.preload_pocket_model))
        return {"status": "loading", "engine": "pocket_tts", "message": "Pocket TTS preload started."}
    else:
        tts_manager.unload_pocket_model()
        return {"status": "unloaded", "engine": req.engine, "message": "Pocket TTS unloaded from memory."}

@app.post("/api/tts/speak")
async def speak_text(req: TtsSpeakRequest):
    """Synthesizes and plays speech non-blockingly."""
    print(f"[API TTS Speak] engine={req.engine}, voice={req.voice}, speed={req.speed}, pitch={req.pitch}, temp={req.temperature}, text={req.text!r}")
    res = await tts_manager.synthesize(
        text=req.text,
        engine=req.engine,
        voice=req.voice,
        speed=req.speed,
        pitch=req.pitch,
        temperature=req.temperature,
        play_now=req.play_now
    )
    return res

@app.post("/api/tts/stop")
def stop_speech():
    """Interrupts any currently playing speech."""
    tts_manager.stop_playback()
    return {"status": "stopped"}

@app.get("/api/tts/status")
def tts_status():
    """playing = speech still audible; the app keeps its 'Speaking...' state until this turns false."""
    return {"playing": tts_manager.is_playing()}

if __name__ == "__main__":
    import socket
    import sys
    import uvicorn
    # Take port 8000 BEFORE the models load (uvicorn alone binds only after startup, i.e. after loading): a second
    # copy started meanwhile then stops here at once (exit code 3, like the tracker) instead of loading both models
    # again and failing afterwards. No SO_REUSEADDR: on Windows that would let two copies bind the same port.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 8000))
    except OSError as e:
        print(f"[Backend] port 8000 is in use ({e}): another backend is running or still starting; "
              f"not loading the models.", flush=True)
        sys.exit(3)
    print("Starting Hybrid Keyboard Server...")
    uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8000)).run(sockets=[sock])
