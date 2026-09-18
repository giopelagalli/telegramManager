"""The Spark's voice server: whisper in, speech out, embeddings — all local, OpenAI-shaped.

Serves the three endpoints the bot uses when SPARK_VOICE_URL is set:
  POST /v1/audio/transcriptions   (multipart file)        -> faster-whisper
  POST /v1/audio/speech           (json: input, voice)    -> Kokoro (or Chatterbox), OGG/Opus out
  POST /v1/embeddings             (json: input[])         -> nomic-embed-text via sentence-transformers
  GET  /v1/models

Run: uvicorn voice_server:app --host 0.0.0.0 --port 8890   (see spark/README.md)
Engines load lazily on first use so a missing optional dependency only breaks that one endpoint.
"""
from __future__ import annotations

import asyncio
import io
import os
import subprocess
import tempfile
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel

app = FastAPI(title="spark-voice")

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "large-v3-turbo")
TTS_ENGINE = os.environ.get("TTS_ENGINE", "kokoro")  # kokoro | chatterbox
KOKORO_DIR = Path(os.environ.get("KOKORO_MODEL_DIR", str(Path(__file__).parent / "models")))
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-ai/nomic-embed-text-v1.5")

_whisper = None
_tts = None
_embedder = None
_lock = asyncio.Lock()


def _device() -> str:
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


# ---------- transcription ----------

def _load_whisper():
    from faster_whisper import WhisperModel

    device = _device()
    return WhisperModel(WHISPER_MODEL, device=device, compute_type="float16" if device == "cuda" else "int8")


@app.post("/v1/audio/transcriptions")
async def transcriptions(file: UploadFile = File(...), model: str = Form("whisper"), response_format: str = Form("json")):
    global _whisper
    async with _lock:
        if _whisper is None:
            _whisper = await asyncio.to_thread(_load_whisper)
    data = await file.read()
    with tempfile.NamedTemporaryFile(suffix=Path(file.filename or "a.ogg").suffix or ".ogg", delete=False) as f:
        f.write(data)
        path = f.name
    try:
        def run():
            segments, _info = _whisper.transcribe(path, vad_filter=True)
            return " ".join(s.text.strip() for s in segments).strip()
        text = await asyncio.to_thread(run)
    finally:
        os.unlink(path)
    if response_format == "text":
        return PlainTextResponse(text)
    return {"text": text}


# ---------- speech ----------

class SpeechRequest(BaseModel):
    input: str
    model: str = "kokoro"
    voice: str = "am_onyx"
    response_format: str = "opus"
    instructions: str | None = None


def _load_tts():
    if TTS_ENGINE == "chatterbox":
        from chatterbox.tts import ChatterboxTTS

        return ("chatterbox", ChatterboxTTS.from_pretrained(device=_device()))
    from kokoro_onnx import Kokoro

    return ("kokoro", Kokoro(str(KOKORO_DIR / "kokoro-v1.0.onnx"), str(KOKORO_DIR / "voices-v1.0.bin")))


def _wav_bytes(samples, sample_rate: int) -> bytes:
    import numpy as np
    import soundfile as sf

    buf = io.BytesIO()
    sf.write(buf, np.asarray(samples, dtype="float32"), sample_rate, format="WAV")
    return buf.getvalue()


def _to_opus(wav: bytes) -> bytes:
    proc = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-c:a", "libopus", "-b:a", "32k", "-f", "ogg", "pipe:1"],
        input=wav, capture_output=True, check=True,
    )
    return proc.stdout


@app.post("/v1/audio/speech")
async def speech(req: SpeechRequest):
    global _tts
    async with _lock:
        if _tts is None:
            _tts = await asyncio.to_thread(_load_tts)
    engine, model = _tts
    text = req.input[:2000]

    def synth():
        if engine == "chatterbox":
            wav = model.generate(text)  # torch tensor [1, n]
            return _wav_bytes(wav.squeeze(0).cpu().numpy(), model.sr)
        samples, sr = model.create(text, voice=req.voice or "am_onyx")
        return _wav_bytes(samples, sr)

    wav = await asyncio.to_thread(synth)
    if req.response_format in ("wav",):
        return Response(wav, media_type="audio/wav")
    return Response(_to_opus(wav), media_type="audio/ogg")


# ---------- embeddings ----------

class EmbedRequest(BaseModel):
    input: str | list[str]
    model: str = EMBED_MODEL


def _load_embedder():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(EMBED_MODEL, trust_remote_code=True, device=_device())


@app.post("/v1/embeddings")
async def embeddings(req: EmbedRequest):
    global _embedder
    async with _lock:
        if _embedder is None:
            _embedder = await asyncio.to_thread(_load_embedder)
    texts = [req.input] if isinstance(req.input, str) else list(req.input)
    if not texts:
        raise HTTPException(400, "input is empty")
    vecs = await asyncio.to_thread(lambda: _embedder.encode(texts, normalize_embeddings=True).tolist())
    return {
        "object": "list",
        "model": req.model,
        "data": [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vecs)],
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }


@app.get("/v1/models")
async def models():
    now = int(time.time())
    return {"object": "list", "data": [
        {"id": m, "object": "model", "created": now, "owned_by": "spark"} for m in ("whisper", TTS_ENGINE, EMBED_MODEL)
    ]}
