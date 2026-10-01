"""Fakes for the web door: a router that echoes, a transcriber, a synthesizer and a transcoder
that never touch a model or ffmpeg, and an aiohttp test client with the bearer set."""
import asyncio
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.knowledge.models import Channel
from bot.knowledge.views import esc
from bot.scheduler.outbound import Outbound
from bot.web.audio import AudioStore
from bot.web.conversation import WebConversation
from bot.web.door import WebDoor
from bot.web.server import build_app

TOKEN = "test-token-not-a-secret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
LIFE = Channel(1, None, "life")


class FakeRouter:
    def __init__(self):
        self.calls = []
        self.lock = asyncio.Lock()

    async def on_text(self, text, via_voice=False, *, channel=None):
        self.calls.append(("text", text, via_voice, channel))
        return [Outbound(f"<b>Got</b> {esc(text)}", buttons=[("Done", "done:x"), ("Later", "defer:x")],
                         voice=via_voice, kind="reply")]

    async def command(self, name, arg, *, channel=None):
        self.calls.append(("command", name, arg, channel))
        return [Outbound(f"/{name} {esc(arg)}", kind="reply")]

    async def on_callback(self, data, message_id=None, message_html=None, buttons=None, *, channel=None):
        self.calls.append(("callback", data, message_id, message_html, buttons, channel))
        if message_id is None:
            return [Outbound("I don't know that button.", kind="reply")]
        remaining = [b for b in buttons if b[1] != data]
        return [Outbound(f"<s>{message_html}</s>", buttons=remaining, edit_message_id=message_id,
                         toast="Done", kind="edit")]

    async def on_voice_unavailable(self, *, channel=None):
        return [Outbound("Voice input isn't set up here. Send it as text.", kind="reply")]

    async def on_voice_failed(self, reason, *, channel=None):
        self.calls.append(("voice_failed", reason))
        return [Outbound("Couldn't transcribe that. Saved a note in your inbox.", kind="reply")]


class FakeTranscriber:
    def __init__(self, text="remind me to call mom", fail=False):
        self.text = text
        self.fail = fail
        self.seen = []

    async def transcribe(self, path: Path):
        self.seen.append((path.suffix, path.read_bytes()))
        if self.fail:
            raise RuntimeError("whisper fell over")
        return self.text, -0.2


class FakeSynthesizer:
    def __init__(self):
        self.texts = []

    async def synthesize(self, text, out_dir):
        self.texts.append(text)
        path = Path(out_dir) / f"note{len(self.texts)}.ogg"
        path.write_bytes(b"OggS-fake")
        return path


async def fake_transcode(src: Path, dst: Path) -> None:
    dst.write_bytes(b"m4a:" + src.read_bytes())


class FakeWebSocket:
    def __init__(self):
        self.events = []

    async def send_json(self, event):
        self.events.append(event)

    async def close(self, **kwargs):
        self.closed = True


def make_conversation(tmp_path, synthesizer=None):
    return WebConversation(tmp_path / "web.json", AudioStore(tmp_path / "audio", fake_transcode),
                           synthesizer, tmp_path / "tmp")


@pytest.fixture
def router():
    return FakeRouter()


@pytest.fixture
def transcriber():
    return FakeTranscriber()


@pytest.fixture
def synthesizer():
    return FakeSynthesizer()


@pytest.fixture
def door(tmp_path, router, transcriber, synthesizer):
    return WebDoor(router, make_conversation(tmp_path, synthesizer), LIFE, transcriber, tmp_path / "tmp")


@pytest.fixture
async def client(door):
    async with TestClient(TestServer(build_app(door, TOKEN))) as c:
        yield c
