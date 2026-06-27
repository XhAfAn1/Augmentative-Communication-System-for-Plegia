from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import os
import threading

# Import our backend logic
from backend import HybridKeyboardBackend

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
backend_lock = threading.Lock()  # For heavy LLM calls
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
async def predict_next_word_fast(req: SpacebarRequest):
    """Instant N-gram predictions (< 1ms). Uses its own lock, never blocked by LLM."""
    if not backend:
        raise HTTPException(status_code=503, detail="Backend not initialized.")
    with fast_lock:
        suggestions = backend.on_spacebar_fast(req.text)
    return {"suggestions": suggestions}

@app.post("/api/spacebar")
async def predict_next_word(req: SpacebarRequest):
    if not backend:
        raise HTTPException(status_code=503, detail="Backend not initialized. Is the model downloaded?")

    with backend_lock:
        # If the user just completed/sent a full sentence, record it in session history
        if req.sentence_complete and req.text.strip():
            backend.on_sentence_submitted(req.text.strip())

        suggestions = backend.on_spacebar(req.text)

    return {"suggestions": suggestions}

@app.post("/api/submit_sentence")
async def submit_sentence(req: TextRequest):
    """Called when a user finishes and sends a full sentence.
    Updates the session history buffer and triggers personalization learning."""
    if not backend:
        raise HTTPException(status_code=503, detail="Backend not initialized.")

    with backend_lock:
        if req.text.strip():
            backend.on_sentence_submitted(req.text.strip())

    return {"status": "ok", "history_size": len(backend.session_history)}

@app.post("/api/type")
async def predict_prefix(req: TextRequest):
    if not backend:
        raise HTTPException(status_code=503, detail="Backend not initialized. Is the model downloaded?")
    
    with fast_lock:
        suggestions = backend.on_letter_typed(req.text)
    return {"suggestions": suggestions}

@app.post("/api/complete_sentence")
async def complete_sentence(req: TextRequest):
    if not backend:
        raise HTTPException(status_code=503, detail="Backend not initialized. Is the model downloaded?")
    
    with backend_lock:
        suggestions = backend.on_sentence_completion(req.text)
    return {"suggestions": suggestions}

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
