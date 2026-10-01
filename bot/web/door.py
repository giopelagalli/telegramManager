"""The web's Handlers: a web request becomes the same router call a Telegram update would, and the
replies come back as wire messages on that request only (they never reach Telegram)."""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from bot.knowledge.views import esc
from bot.scheduler.outbound import Outbound
from bot.telegram.app import ERROR_REPLY
from bot.telegram.commands import COMMANDS
from bot.telegram.sender import QUICK_KEYS

logger = logging.getLogger(__name__)

COMMAND_NAMES = {name for name, _description, _menu in COMMANDS}
# What a browser records, by base Content-Type; the transcribers read the container from the suffix.
AUDIO_TYPES = {"audio/webm": "webm", "audio/mp4": "mp4", "audio/ogg": "ogg"}


class UnsupportedAudio(ValueError):
    pass


class WebDoor:
    def __init__(self, router, conversation, channel, transcriber=None, tmp_dir: Path = Path("."), name=lambda: "JD"):
        self.router = router
        self.conversation = conversation
        self.channel = channel  # the DM's channel: the web is JD's life chat, not a topic
        self.transcriber = transcriber
        self.tmp_dir = Path(tmp_dir)
        self.name = name
        self.keys = list(QUICK_KEYS)
        # The router's lock, shared with Telegram's Handlers (0010): one message at a time, any surface.
        self.lock = router.lock

    async def message(self, text: str) -> list[dict]:
        async with self._busy():
            echo = self.conversation.owner(text)
            outs = await self._safely(self._route(text))
            return [echo, *[await self.conversation.render(out) for out in outs]]

    async def callback(self, data: str) -> list[dict]:
        async with self._busy():
            message = self.conversation.with_button(data)
            if message is None:
                message_id = message_html = buttons = None
            else:
                message_id = int(message["id"])
                message_html = message["text"] if message["format"] == "html" else esc(message["text"])
                buttons = [(b["label"], b["data"]) for row in message.get("buttons", ()) for b in row]
            outs = await self._safely(
                self.router.on_callback(data, message_id, message_html, buttons, channel=self.channel)
            )
            return [await self.conversation.render(out) for out in outs]

    async def voice(self, audio: bytes, content_type: str) -> tuple[str, list[dict]]:
        suffix = AUDIO_TYPES.get(content_type.split(";")[0].strip().lower())
        if suffix is None:
            raise UnsupportedAudio(content_type)
        async with self._busy():
            if self.transcriber is None:
                outs = await self._safely(self.router.on_voice_unavailable(channel=self.channel))
                return "", [await self.conversation.render(out) for out in outs]
            self.tmp_dir.mkdir(parents=True, exist_ok=True)
            path = self.tmp_dir / f"web-{uuid.uuid4().hex}.{suffix}"
            path.write_bytes(audio)
            try:
                text, _confidence = await self.transcriber.transcribe(path)
            except Exception as exc:
                reason = str(exc) or type(exc).__name__
                outs = await self._safely(self.router.on_voice_failed(reason, channel=self.channel))
                return "", [await self.conversation.render(out) for out in outs]
            finally:
                path.unlink(missing_ok=True)
            # The transcript travels on its own field, so no "Heard: …" prefix as on Telegram.
            echo = self.conversation.owner(text)
            outs = await self._safely(self.router.on_text(text, via_voice=True, channel=self.channel))
            return text, [echo, *[await self.conversation.render(out) for out in outs]]

    async def _route(self, text: str) -> list[Outbound]:
        """A known /command goes where Telegram's CommandHandler sends it; anything else is text."""
        stripped = text.strip()
        if stripped.startswith("/"):
            head, _, arg = stripped[1:].partition(" ")
            name = head.split("@")[0].lower()
            if name in COMMAND_NAMES:
                return await self.router.command(name, " ".join(arg.split()), channel=self.channel)
        return await self.router.on_text(text, channel=self.channel)

    async def _safely(self, call) -> list[Outbound]:
        """Like Telegram's error handler: a failure is logged and answered, never a dead request."""
        try:
            return await call
        except Exception:
            logger.exception("web request handling failed")
            return [Outbound(ERROR_REPLY, kind="reply")]

    @asynccontextmanager
    async def _busy(self):
        async with self.lock:
            await self.conversation.typing(True)
            try:
                yield
            finally:
                await self.conversation.typing(False)
