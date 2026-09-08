"""Speech-to-text transcription via faster-whisper."""
import asyncio
import importlib.util
import sys
from pathlib import Path


def _importable(name: str) -> bool:
    if name in sys.modules:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def available() -> bool:
    return _importable("faster_whisper")


class Transcriber:
    def __init__(self, model_size: str = "small", device: str = "auto"):
        if not _importable("faster_whisper"):
            raise RuntimeError("voice extras not installed")
        self._model_size = model_size
        self._device = device
        self._model = None

    def _load_model(self):
        from faster_whisper import WhisperModel
        import ctranslate2

        device = self._device
        if device == "auto":
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        compute_type = "int8" if device == "cpu" else "default"
        return WhisperModel(self._model_size, device=device, compute_type=compute_type)

    def _transcribe_sync(self, path: Path) -> tuple[str, float]:
        if self._model is None:
            self._model = self._load_model()
        segments, _ = self._model.transcribe(str(path))
        segments = list(segments)
        text = " ".join(seg.text.strip() for seg in segments)
        confidence = sum(seg.avg_logprob for seg in segments) / len(segments) if segments else 0.0
        return text, confidence

    async def transcribe(self, path: Path) -> tuple[str, float]:
        return await asyncio.to_thread(self._transcribe_sync, path)
