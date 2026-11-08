from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
import os
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
backend_lock = threading.Lock()  # For heavy LLM calls and state updates
fast_ai_lock = threading.Lock()  # For 0.5B word predictions
fast_lock = threading.Lock()     # For instant N-gram calls (never blocked by LLM)

class InitRequest(BaseModel):
    fast_gguf_path: str = "qwen2.5-0.5b-instruct-q8_0.gguf"
    heavy_gguf_path: str = "Llama-3.2-3B-Instruct-Q4_K_M.gguf"
    vocab_path: str = "vocab.json"

class TextRequest(BaseModel):
    text: str

class SpacebarRequest(BaseModel):
    text: str
    sentence_complete: bool = False  # True when user finishes/sends a sentence

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
        backend = HybridKeyboardBackend(fast_gguf_path=fast_gguf_path, heavy_gguf_path=heavy_gguf_path, vocab_path=vocab_path)
    else:
        print("Backend not fully initialized on startup. Missing model or vocab file.")
        print(f"Looked for: {fast_gguf_path}, {heavy_gguf_path} and {vocab_path}")

@app.post("/api/init")
async def initialize_backend(req: InitRequest):
    global backend
    with backend_lock:
        try:
            backend = HybridKeyboardBackend(fast_gguf_path=req.fast_gguf_path, heavy_gguf_path=req.heavy_gguf_path, vocab_path=req.vocab_path)
            return {"status": "success", "message": "Backend initialized successfully."}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to initialize backend: {str(e)}")

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

    # 1. Update state using the global backend_lock to prevent memory corruption
    if req.sentence_complete and req.text.strip():
        with backend_lock:
            backend.on_sentence_submitted(req.text.strip())

    # 2. Run the 0.5B word prediction model on its own dedicated thread lock
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
        suggestions = backend.on_letter_typed(req.text)
    return {"suggestions": suggestions}

@app.post("/api/complete_sentence")
def complete_sentence(req: TextRequest):
    if not backend:
        raise HTTPException(status_code=503, detail="Backend not initialized. Is the model downloaded?")
    
    with backend_lock:
        suggestions = backend.on_sentence_completion(req.text)
    return {"suggestions": suggestions}

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
    with backend_lock:
        with fast_ai_lock:
            info = backend.set_gpu_mode(req.enabled)
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

@app.get("/api/tts/audio/{filename}")
def get_audio_file(filename: str):
    """Static file endpoint to download or stream generated TTS audio."""
    file_path = os.path.join(tts_manager.cache_dir, filename)
    if os.path.exists(file_path):
        media_type = "audio/mpeg" if filename.endswith(".mp3") else "audio/wav"
        return FileResponse(file_path, media_type=media_type)
    raise HTTPException(status_code=404, detail="Audio file not found.")

# Serve static files for the UI
@app.get("/")
async def get_index():
    if os.path.exists("index.html"):
        return FileResponse("index.html")
    return {"message": "index.html not found."}

@app.get("/style.css")
async def get_style():
    if os.path.exists("style.css"):
        return FileResponse("style.css")
    return {"message": "style.css not found."}

@app.get("/script.js")
async def get_script():
    if os.path.exists("script.js"):
        return FileResponse("script.js")
    return {"message": "script.js not found."}

if __name__ == "__main__":
    import uvicorn
    print("Starting Hybrid Keyboard Server...")
    print("UI will be available at: http://localhost:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000)
