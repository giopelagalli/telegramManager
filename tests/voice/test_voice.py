import sys, types
from pathlib import Path
import pytest
from bot.voice import stt, tts

async def test_transcriber_uses_model(monkeypatch, tmp_path):
    class Seg:  # faster-whisper segment
        text = " hello world"; avg_logprob = -0.2
    class WM:
        def __init__(self, *a, **k): pass
        def transcribe(self, path, **k): return [Seg(), Seg()], None
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=WM))
    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(get_cuda_device_count=lambda: 0))
    t = stt.Transcriber()
    text, conf = await t.transcribe(tmp_path / "x.ogg")
    assert text == "hello world hello world" and conf == pytest.approx(-0.2)

async def test_synthesizer_writes_ogg(monkeypatch, tmp_path):
    import numpy as np
    class K:
        def __init__(self, *a, **k): pass
        def create(self, text, voice, speed=1.0, lang="en-us"): return np.zeros(2400, dtype="float32"), 24000
    monkeypatch.setitem(sys.modules, "kokoro_onnx", types.SimpleNamespace(Kokoro=K))
    calls = []
    async def fake_exec(*args, **k):
        calls.append(args); Path(args[-1]).write_bytes(b"OggS")
        class P:
            returncode = 0
            async def communicate(self): return b"", b""
        return P()
    monkeypatch.setattr(tts.asyncio, "create_subprocess_exec", fake_exec)
    s = tts.Synthesizer(tmp_path / "m.onnx", tmp_path / "v.bin")
    out = await s.synthesize("hi there", tmp_path)
    assert out.suffix == ".ogg" and out.read_bytes() == b"OggS" and calls[0][0] == "ffmpeg"
