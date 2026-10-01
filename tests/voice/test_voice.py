import sys, types
from pathlib import Path
from unittest.mock import AsyncMock
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

async def test_api_transcriber_calls_client(tmp_path):
    path = tmp_path / "x.ogg"
    path.write_bytes(b"OggS")
    t = stt.ApiTranscriber("http://s/v1", "key", "whisper-v3")
    t._client.audio.transcriptions.create = AsyncMock(return_value=" hello world ")
    text, conf = await t.transcribe(path)
    assert text == "hello world" and conf == 0.0
    kwargs = t._client.audio.transcriptions.create.call_args.kwargs
    assert kwargs["model"] == "whisper-v3"
    assert kwargs["response_format"] == "text"
    assert kwargs["file"].name == str(path)


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
    assert len(out.stem) == 32 and all(c in "0123456789abcdef" for c in out.stem)
    assert not out.with_suffix(".wav").exists()

async def test_synthesizer_unique_names(monkeypatch, tmp_path):
    import numpy as np
    class K:
        def __init__(self, *a, **k): pass
        def create(self, text, voice, speed=1.0, lang="en-us"): return np.zeros(2400, dtype="float32"), 24000
    monkeypatch.setitem(sys.modules, "kokoro_onnx", types.SimpleNamespace(Kokoro=K))
    async def fake_exec(*args, **k):
        Path(args[-1]).write_bytes(b"OggS")
        class P:
            returncode = 0
            async def communicate(self): return b"", b""
        return P()
    monkeypatch.setattr(tts.asyncio, "create_subprocess_exec", fake_exec)
    s = tts.Synthesizer(tmp_path / "m.onnx", tmp_path / "v.bin")
    out1 = await s.synthesize("hi there", tmp_path)
    out2 = await s.synthesize("hi there", tmp_path)
    assert out1 != out2


async def test_api_synthesizer_writes_opus_and_passes_style(tmp_path):
    class Speech:
        def __init__(self): self.kwargs = None
        async def create(self, **kwargs):
            self.kwargs = kwargs
            return types.SimpleNamespace(content=b"OggS-fake")
    speech = Speech()
    client = types.SimpleNamespace(audio=types.SimpleNamespace(speech=speech))
    s = tts.ApiSynthesizer("http://t/v1", "k", "gpt-4o-mini-tts", "onyx", "dry, unhurried", client=client)
    path = await s.synthesize("Leave now.", tmp_path / "out")
    assert path.suffix == ".ogg" and path.read_bytes() == b"OggS-fake"
    assert speech.kwargs["voice"] == "onyx" and speech.kwargs["response_format"] == "opus"
    assert speech.kwargs["instructions"] == "dry, unhurried" and speech.kwargs["input"] == "Leave now."


async def test_twilio_caller_posts_twiml_and_returns_sid():
    import httpx
    from bot.voice.call import TwilioCaller
    seen = {}
    def handler(request):
        seen["url"] = str(request.url); seen["body"] = request.content.decode()
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(201, json={"sid": "CA123"})
    c = TwilioCaller("AC1", "tok", "+15550001111", "+15552223333", transport=httpx.MockTransport(handler))
    assert await c.call("Leave now for Flight & go.") == "CA123"
    assert seen["url"].endswith("/Accounts/AC1/Calls.json") and seen["auth"].startswith("Basic ")
    assert "To=%2B15552223333" in seen["body"] and "Flight+%26amp%3B+go" in seen["body"]


async def test_twilio_caller_failure_returns_none():
    import httpx
    from bot.voice.call import TwilioCaller
    c = TwilioCaller("AC1", "tok", "+1", "+2", transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    assert await c.call("x") is None


async def test_two_first_transcriptions_load_whisper_once(monkeypatch, tmp_path):
    import asyncio, time
    loads = []

    class Seg:
        text = "hi"; avg_logprob = -0.1

    class WM:
        def __init__(self, *a, **k):
            loads.append(1)
            time.sleep(0.05)  # a slow load, so both threads arrive while it runs

        def transcribe(self, path, **k):
            return [Seg()], None

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=WM))
    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(get_cuda_device_count=lambda: 0))
    t = stt.Transcriber()
    await asyncio.gather(t.transcribe(tmp_path / "a.ogg"), t.transcribe(tmp_path / "b.webm"))
    assert loads == [1]
