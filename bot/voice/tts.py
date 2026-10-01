"""Text-to-speech voice note synthesis via Kokoro + ffmpeg."""
import asyncio
import importlib.util
import sys
import threading
import uuid
from pathlib import Path

MAX_CHARS = 1500


def _importable(name: str) -> bool:
    if name in sys.modules:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def available(model_path: Path, voices_path: Path) -> bool:
    return (
        _importable("kokoro_onnx")
        and Path(model_path).exists()
        and Path(voices_path).exists()
    )


class ApiSynthesizer:
    """OpenAI-style /audio/speech: returns OGG/Opus directly, so no ffmpeg and no model in RAM."""

    def __init__(self, base_url: str, api_key: str, model: str, voice: str, instructions: str | None = None, client=None):
        import openai

        self._model = model
        self._voice = voice
        self._instructions = instructions
        self._client = client or openai.AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=180.0)  # CPU synthesis is slow

    async def synthesize(self, text: str, out_dir: Path) -> Path:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        kwargs = {"model": self._model, "voice": self._voice, "input": text[:MAX_CHARS], "response_format": "opus"}
        if self._instructions:
            kwargs["instructions"] = self._instructions
        response = await self._client.audio.speech.create(**kwargs)
        path = out_dir / f"{uuid.uuid4().hex}.ogg"
        path.write_bytes(response.content)
        return path


class Synthesizer:
    def __init__(self, model_path: Path, voices_path: Path, voice: str = "am_onyx"):
        self._model_path = model_path
        self._voices_path = voices_path
        self._voice = voice
        self._kokoro = None
        self._load_lock = threading.Lock()  # shared by both connectors: load Kokoro once

    def _load_kokoro(self):
        from kokoro_onnx import Kokoro

        return Kokoro(str(self._model_path), str(self._voices_path))

    def _synthesize_sync(self, text: str, out_path: Path) -> None:
        import soundfile as sf

        with self._load_lock:
            if self._kokoro is None:
                self._kokoro = self._load_kokoro()
        samples, sample_rate = self._kokoro.create(text, voice=self._voice)
        sf.write(str(out_path), samples, sample_rate)

    async def synthesize(self, text: str, out_dir: Path) -> Path:
        text = text[:MAX_CHARS]
        out_dir = Path(out_dir)
        stem = uuid.uuid4().hex
        wav_path = out_dir / f"{stem}.wav"
        ogg_path = out_dir / f"{stem}.ogg"

        await asyncio.to_thread(self._synthesize_sync, text, wav_path)

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", str(wav_path), "-c:a", "libopus", "-b:a", "32k", str(ogg_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            tail = stderr[-2000:].decode(errors="replace") if stderr else ""
            raise RuntimeError(f"ffmpeg failed: {tail}")
        wav_path.unlink()
        return ogg_path
