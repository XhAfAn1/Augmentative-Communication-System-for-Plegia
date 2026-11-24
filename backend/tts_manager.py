import os
import sys
import time
import asyncio
import subprocess
import threading
from typing import Optional, List, Dict, Any, Tuple

# Predefined Edge-TTS voices
EDGE_VOICES = [
    # Female
    {"id": "en-US-JennyNeural", "name": "Jenny (US Female - Conversational Flagship)", "gender": "female"},
    {"id": "en-US-AriaNeural", "name": "Aria (US Female - Expressive)", "gender": "female"},
    {"id": "en-GB-SoniaNeural", "name": "Sonia (UK Female - British)", "gender": "female"},
    # Male
    {"id": "en-US-GuyNeural", "name": "Guy (US Male - Conversational Flagship)", "gender": "male"},
    {"id": "en-US-ChristopherNeural", "name": "Christopher (US Male - Professional)", "gender": "male"},
    {"id": "en-GB-RyanNeural", "name": "Ryan (UK Male - British)", "gender": "male"}
]

# Predefined Pocket-TTS voices
POCKET_VOICES = [
    # Female
    {"id": "anna", "name": "Anna (Gentle Female)", "gender": "female"},
    {"id": "cosette", "name": "Cosette (Melodic Female)", "gender": "female"},
    {"id": "jane", "name": "Jane (Grounded Female)", "gender": "female"},
    # Male
    {"id": "jean", "name": "Jean (Natural Male)", "gender": "male"},
    {"id": "paul", "name": "Paul (Warm Male)", "gender": "male"},
    {"id": "george", "name": "George (Deep Male)", "gender": "male"},
    {"id": "michael", "name": "Michael (Clean Male)", "gender": "male"}
]


class TTSManager:
    """Modular, thread-safe, lightweight TTS Manager for SynapTalk AAC.
    
    Supports:
      1. Edge-TTS (Cloud Neural via Microsoft Azure — zero RAM/VRAM footprint)
      2. Pocket TTS (On-Device lightweight 100M ALM — pinned to CPU, lazy loaded)
      3. Windows SAPI (Native offline fallback)
    """

    def __init__(self, cache_dir: str = "tts_outputs"):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.cache_dir = os.path.join(self.base_dir, cache_dir)
        os.makedirs(self.cache_dir, exist_ok=True)
        
        # Rotating ring buffer slots (0, 1, 2) to cap disk usage under 1 MB
        self._max_slots = 3
        self._current_slot = 0
        self._slot_lock = threading.Lock()

        # Clean up any legacy or accumulated files on boot
        self._cleanup_cache()

        # Lazy Pocket-TTS instance
        self._pocket_model = None
        self._pocket_lock = threading.Lock()
        
        # Playback tracking via native pygame.mixer
        self._mixer_initialized = False
        self._playback_lock = threading.Lock()
        self._playback_process = None
        self._loaded_path = None   # file pygame currently holds open (see _release_loaded_file)
        # every Stop / new request ends the older ones: a late result (slow network) is never played afterwards
        self._generation = 0

    def _cleanup_cache(self):
        """Cleans up legacy files in tts_outputs on startup."""
        try:
            for fname in os.listdir(self.cache_dir):
                fpath = os.path.join(self.cache_dir, fname)
                if os.path.isfile(fpath) and (
                    fname.startswith("pocket_") or fname.startswith("edge_") or fname.startswith("tts_slot_")
                ):
                    try:
                        os.remove(fpath)
                    except Exception:
                        pass
        except Exception as e:
            print(f"[TTSManager] Cache cleanup notice: {e}")

    def _get_next_slot(self, extension: str) -> Tuple[str, str]:
        """Returns (filename, filepath) for the next rotating slot."""
        with self._slot_lock:
            slot = self._current_slot
            self._current_slot = (self._current_slot + 1) % self._max_slots
            filename = f"tts_slot_{slot}.{extension}"
            filepath = os.path.join(self.cache_dir, filename)
        self._release_loaded_file(filepath)
        return filename, filepath

    def _release_loaded_file(self, file_path: str):
        """pygame keeps the loaded file open (read-only share on Windows), so writing that slot again fails with
        WinError 32. This happens when the in-between requests never loaded a file (Stop, play_now=False, SAPI fallback).
        Unloads only when the slot about to be written is the loaded file; other playback is not touched."""
        abs_path = os.path.abspath(file_path)
        with self._playback_lock:
            if self._loaded_path != abs_path:
                return
            try:
                import pygame
                if pygame.mixer.get_init():
                    pygame.mixer.music.stop()
                    pygame.mixer.music.unload()
                self._loaded_path = None
            except Exception as e:
                print(f"[TTSManager] Could not release {os.path.basename(abs_path)}: {e}")

    def get_available_voices(self) -> Dict[str, Any]:
        """Returns metadata for all available engines and their voices."""
        return {
            "engines": [
                {
                    "id": "edge_tts",
                    "name": "Edge-TTS (Azure Neural Cloud)",
                    "description": "Ultra-natural studio quality. Zero local compute/VRAM. Requires internet.",
                    "is_cloud": True,
                    "default_voice": "en-US-JennyNeural",
                    "voices": EDGE_VOICES
                },
                {
                    "id": "pocket_tts",
                    "name": "Pocket TTS (On-Device Edge)",
                    "description": "Lightweight 100M model. 100% offline, privacy-safe, runs strictly on CPU.",
                    "is_cloud": False,
                    "default_voice": "anna",
                    "voices": POCKET_VOICES
                },
                {
                    "id": "sapi",
                    "name": "Windows Native (SAPI Fallback)",
                    "description": "Built-in Windows system speech synthesizer. Always available.",
                    "is_cloud": False,
                    "default_voice": "default",
                    "voices": [
                        {"id": "default", "name": "System Default Voice", "gender": "female"}
                    ]
                }
            ]
        }

    def _get_pocket_model(self):
        """Lazy loader: Pinned strictly to CPU to prevent any VRAM contention with LLMs."""
        if self._pocket_model is None:
            with self._pocket_lock:
                if self._pocket_model is None:
                    from pocket_tts import TTSModel
                    print("[TTSManager] Preloading Pocket TTS on CPU (Zero VRAM impact)...")
                    self._pocket_model = TTSModel.load_model()
                    self._pocket_voice_cache = {}
                    print("[TTSManager] Pocket TTS loaded successfully on CPU.")
        return self._pocket_model

    def preload_pocket_model(self):
        """Proactively preloads Pocket TTS in memory when selected in dropdown."""
        return self._get_pocket_model()

    def unload_pocket_model(self):
        """Immediately unloads Pocket TTS to free memory when another model is selected."""
        with self._pocket_lock:
            if self._pocket_model is not None:
                print("[TTSManager] Unloading Pocket TTS to free memory...")
                self._pocket_model = None
                self._pocket_voice_cache = {}
                import gc
                gc.collect()
                print("[TTSManager] Pocket TTS unloaded successfully.")

    def _ensure_mixer(self):
        if not self._mixer_initialized:
            try:
                import pygame
                if not pygame.mixer.get_init():
                    pygame.mixer.init(frequency=24000, size=-16, channels=1, buffer=1024)
                self._mixer_initialized = True
            except Exception as e:
                print(f"[TTSManager] Failed to init pygame mixer: {e}")

    EDGE_TIMEOUT_S = 6.0   # slow / broken internet: speak with Windows SAPI instead of keeping the user waiting

    def stop_playback(self):
        """Immediately interrupts any playing audio (also SAPI speech) and cancels requests still synthesizing."""
        self._generation += 1
        with self._playback_lock:
            p, self._playback_process = self._playback_process, None
            if p is not None and p.poll() is None:
                try:
                    p.kill()
                except Exception:
                    pass
            try:
                import pygame
                if pygame.mixer.get_init():
                    pygame.mixer.music.stop()
            except Exception:
                pass

    def _play_audio_file(self, file_path: str) -> bool:
        """Non-blocking audio playback using native pygame.mixer. False = could not play (no audio device, mixer
        failed): the caller then speaks with Windows SAPI so the user is never left in silence."""
        abs_path = os.path.abspath(file_path)
        with self._playback_lock:
            try:
                self._ensure_mixer()
                import pygame
                if not pygame.mixer.get_init():
                    raise RuntimeError("audio mixer not available")
                pygame.mixer.music.stop()
                pygame.mixer.music.load(abs_path)
                self._loaded_path = abs_path    # set only after a successful load: a failed load keeps the old file
                pygame.mixer.music.play()
                return True
            except Exception as e:
                print(f"[TTSManager] Playback error: {e}. Falling back to SAPI...")
                return False

    def is_playing(self) -> bool:
        """True while speech is still audible (audio file or SAPI); the app waits for this to end."""
        with self._playback_lock:
            p = self._playback_process
            if p is not None and p.poll() is None:
                return True
        try:
            import pygame
            return bool(pygame.mixer.get_init() and pygame.mixer.music.get_busy())
        except Exception:
            return False

    @staticmethod
    def sapi_rate(speed: float) -> int:
        """App speech rate 0.5..1.5 (1.0 = normal) -> SAPI Rate -10..10 (0 = normal); 0.5 -> -5, 1.5 -> +5."""
        try:
            return max(-10, min(10, int(round((float(speed) - 1.0) * 10))))
        except (TypeError, ValueError):
            return 0

    async def synthesize(
        self,
        text: str,
        engine: str = "edge_tts",
        voice: Optional[str] = None,
        speed: float = 1.0,
        pitch: float = 1.0,
        temperature: float = 0.3,
        play_now: bool = True
    ) -> Dict[str, Any]:
        """Synthesizes text using the requested engine and voice persona."""
        cleaned_text = text.strip()
        if not cleaned_text:
            return {"status": "error", "message": "Empty text provided."}

        timestamp = int(time.time() * 1000)
        gen = self._generation          # Stop (or the app's timeout) after this point cancels the playback

        # ----------------------------------------------------
        # 1. Edge-TTS (Cloud Neural)
        # ----------------------------------------------------
        if engine == "edge_tts":
            try:
                import edge_tts
                selected_voice = voice or "en-US-JennyNeural"
                output_filename, output_path = self._get_next_slot("mp3")

                # Format rate & pitch for Edge-TTS
                rate_pct = int(round((speed - 1.0) * 100))
                pitch_hz = int(round((pitch - 1.0) * 50))
                rate_str = f"{rate_pct:+d}%" if rate_pct != 0 else "+0%"
                pitch_str = f"{pitch_hz:+d}Hz" if pitch_hz != 0 else "+0Hz"

                t0 = time.time()
                communicate = edge_tts.Communicate(
                    text=cleaned_text,
                    voice=selected_voice,
                    rate=rate_str,
                    pitch=pitch_str
                )
                await asyncio.wait_for(communicate.save(output_path), timeout=self.EDGE_TIMEOUT_S)
                latency_ms = round((time.time() - t0) * 1000, 1)

                if play_now and gen == self._generation and not self._play_audio_file(output_path):
                    return await self._synthesize_sapi(cleaned_text, speed=speed, play_now=True)

                return {
                    "status": "success",
                    "engine": "edge_tts",
                    "voice": selected_voice,
                    "audio_filename": output_filename,
                    "audio_path": output_path,
                    "latency_ms": latency_ms
                }
            except Exception as e:
                print(f"[TTSManager] Edge-TTS failed: {e}. Falling back to SAPI...")
                # Graceful fallback to SAPI if internet is down
                return await self._synthesize_sapi(cleaned_text, speed=speed,
                                                   play_now=play_now and gen == self._generation)

        # ----------------------------------------------------
        # 2. Pocket TTS (On-Device CPU)
        # ----------------------------------------------------
        elif engine == "pocket_tts":
            try:
                import scipy.io.wavfile
                import numpy as np

                # loading the model (first use) and preparing a voice take seconds: done in a worker thread so
                # word prediction and the Stop button keep working meanwhile
                model = await asyncio.to_thread(self._get_pocket_model)
                selected_voice = voice or "anna"
                output_filename, output_path = self._get_next_slot("wav")

                # Set generation temperature (expressiveness)
                try:
                    model.temp = float(temperature)
                except Exception:
                    pass

                t0 = time.time()
                if selected_voice not in getattr(self, "_pocket_voice_cache", {}):
                    if not hasattr(self, "_pocket_voice_cache"):
                        self._pocket_voice_cache = {}
                    v_state = await asyncio.to_thread(model.get_state_for_audio_prompt, selected_voice)
                    self._pocket_voice_cache[selected_voice] = v_state
                else:
                    v_state = self._pocket_voice_cache[selected_voice]

                loop = asyncio.get_event_loop()
                audio_tensor = await loop.run_in_executor(
                    None,
                    lambda: model.generate_audio(v_state, cleaned_text)
                )
                latency_ms = round((time.time() - t0) * 1000, 1)

                audio_data = audio_tensor.cpu().numpy()
                sample_rate = getattr(model, "sample_rate", 24000)

                # Peak normalize to prevent faint/whisper "ghost" sound
                max_val = np.max(np.abs(audio_data))
                if max_val > 0:
                    audio_data = (audio_data / max_val) * 0.95
                audio_int16 = (audio_data * 32767).astype(np.int16)

                scipy.io.wavfile.write(output_path, sample_rate, audio_int16)

                if play_now and gen == self._generation and not self._play_audio_file(output_path):
                    return await self._synthesize_sapi(cleaned_text, speed=speed, play_now=True)

                return {
                    "status": "success",
                    "engine": "pocket_tts",
                    "voice": selected_voice,
                    "audio_filename": output_filename,
                    "audio_path": output_path,
                    "latency_ms": latency_ms
                }
            except Exception as e:
                print(f"[TTSManager] Pocket TTS failed: {e}. Falling back to SAPI...")
                return await self._synthesize_sapi(cleaned_text, speed=speed,
                                                   play_now=play_now and gen == self._generation)

        # ----------------------------------------------------
        # 3. Windows Native SAPI Fallback
        # ----------------------------------------------------
        else:
            return await self._synthesize_sapi(cleaned_text, speed=speed, play_now=play_now)

    # The text reaches PowerShell through an environment variable, never inside the command itself: PowerShell
    # also treats curly quotes (’ ‘) as quote marks, so "I don’t know" used to break the command (silence), and
    # typed text could never run as a command.
    SAPI_COMMAND = ("Add-Type -AssemblyName System.Speech; "
                    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                    "$s.Rate = [int]$env:AAC_TTS_RATE; "
                    "$s.Speak($env:AAC_TTS_TEXT);")

    async def _synthesize_sapi(self, text: str, speed: float = 1.0, play_now: bool = True) -> Dict[str, Any]:
        """Direct fallback speech using Windows System.Speech (speech rate follows the app's Speech Rate)."""
        if play_now:
            self.stop_playback()
            env = dict(os.environ, AAC_TTS_TEXT=text, AAC_TTS_RATE=str(self.sapi_rate(speed)))
            with self._playback_lock:
                try:
                    self._playback_process = subprocess.Popen(
                        ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", self.SAPI_COMMAND],
                        env=env, creationflags=subprocess.CREATE_NO_WINDOW
                    )
                except Exception as e:
                    print(f"[TTSManager] SAPI playback error: {e}")

        return {
            "status": "success",
            "engine": "sapi",
            "voice": "default",
            "message": "Spoken via Windows SAPI fallback."
        }


# Global singleton instance
tts_manager = TTSManager()
