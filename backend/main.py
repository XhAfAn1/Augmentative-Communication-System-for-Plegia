from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os
import threading

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
backend_instance = None
backend_lock = threading.Lock()

class InitRequest(BaseModel):
    gguf_path: str = "qwen2.5-0.5b-instruct-q8_0.gguf"
    vocab_path: str = "vocab.json"

class TextRequest(BaseModel):
    text: str

@app.on_event("startup")
async def startup_event():
    global backend_instance
    gguf_path = "qwen2.5-0.5b-instruct-q8_0.gguf"
    vocab_path = "vocab.json"
    
    if os.path.exists(gguf_path) and os.path.exists(vocab_path):
        print("Initializing backend engine on startup...")
        backend_instance = HybridKeyboardBackend(gguf_path=gguf_path, vocab_path=vocab_path)
    else:
        print("Backend not fully initialized on startup. Missing model or vocab file.")
        print(f"Looked for: {gguf_path} and {vocab_path}")

@app.post("/api/init")
async def initialize_backend(req: InitRequest):
    global backend_instance
    with backend_lock:
        try:
            backend_instance = HybridKeyboardBackend(gguf_path=req.gguf_path, vocab_path=req.vocab_path)
            return {"status": "success", "message": "Backend initialized successfully."}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to initialize backend: {str(e)}")

@app.post("/api/spacebar")
async def predict_next_word(req: TextRequest):
    if not backend_instance:
        raise HTTPException(status_code=503, detail="Backend not initialized. Is the model downloaded?")
    
    with backend_lock:
        suggestions = backend_instance.on_spacebar(req.text)
        
        # If user completed a word, let's learn it
        words = req.text.split()
        if len(words) > 0:
            last_word = words[-1]
            backend_instance.learn_user_typing(last_word)
            
    return {"suggestions": suggestions}

@app.post("/api/type")
async def predict_prefix(req: TextRequest):
    if not backend_instance:
        raise HTTPException(status_code=503, detail="Backend not initialized. Is the model downloaded?")
    
    with backend_lock:
        suggestions = backend_instance.on_letter_typed(req.text)
    return {"suggestions": suggestions}

@app.post("/api/sentences")
async def predict_sentences_endpoint(req: TextRequest):
    if not backend_instance:
        raise HTTPException(status_code=503, detail="Backend not initialized. Is the model downloaded?")
    
    with backend_lock:
        suggestions = backend_instance.predict_sentences(req.text)
    return {"suggestions": suggestions}

@app.get("/")
def read_root():
    return {"message": "Hello from Synaptalk Backend"}
