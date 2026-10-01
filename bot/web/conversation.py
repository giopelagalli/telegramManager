"""The web view of JD and its connector: an Outbound becomes a wire message (AgentHub 0069) here.

Holds the web conversation (bounded, persisted to a JSON file in DATA_DIR like JD's runtime state,
so a restart keeps it) and the open /stream sockets. As a connector, `send` is the proactive leg:
it reaches the web only while a browser is attached, and lands in the history when it does.
Replies to web requests go through `render`, which records them without pushing.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from bot.scheduler.outbound import Outbound
from bot.telegram.sender import plain_text
from bot.web.audio import MIME
from bot.web.format import to_wire

logger = logging.getLogger(__name__)

HISTORY_CAP = 200


def _now_ms() -> int:
    return int(time.time() * 1000)


class WebConversation:
    def __init__(self, path: Path, audio=None, synthesizer=None, tmp_dir: Path = Path("."),
                 cap: int = HISTORY_CAP, now_ms=_now_ms):
        self.path = Path(path)
        self.audio = audio  # bot.web.audio.AudioStore; reply audio needs it and a synthesizer
        self.synthesizer = synthesizer
        self.tmp_dir = Path(tmp_dir)
        self.cap = cap
        self.now_ms = now_ms
        self.streams: set = set()  # open /stream sockets: anything with `async send_json(dict)`
        self.messages: list[dict] = []
        self.next_id = 1
        self._load()

    # -- the connector ------------------------------------------------------

    async def send(self, out: Outbound) -> None:
        """A proactive message: to every open stream; nothing when no browser is attached."""
        if not self.streams:
            return
        message = await self.render(out)
        await self.push({"type": "message", "message": message})

    # -- the conversation -----------------------------------------------------

    def owner(self, text: str) -> dict:
        message = {"id": self._new_id(), "from": "owner", "at": self.now_ms(), "text": text, "format": "plain"}
        self._append(message)
        return message

    async def render(self, out: Outbound) -> dict:
        """Record what JD said on the web and return it as a wire message; an edit keeps its id."""
        text, fmt = to_wire(out.text)
        existing = self._get(out.edit_message_id)
        message = {
            "id": existing["id"] if existing else self._new_id(),
            "from": "jd",
            "at": existing["at"] if existing else self.now_ms(),
            "text": text,
            "format": fmt,
        }
        if out.buttons:
            message["buttons"] = [[{"label": label, "data": data}] for label, data in out.buttons]
        if out.voice:
            audio_id = await self._voice(out.text)
            if audio_id is not None:
                message["audio"] = {"id": audio_id, "mime": MIME}
        if existing is None:
            self._append(message)
            return message
        self.messages[self.messages.index(existing)] = message
        self._save()
        return {**message, "edit": True}

    def history(self, limit: int) -> list[dict]:
        return self.messages[-limit:] if limit > 0 else []

    def with_button(self, data: str) -> dict | None:
        """The newest message carrying this button, the one a tap on it edits."""
        for message in reversed(self.messages):
            for row in message.get("buttons", ()):
                if any(button["data"] == data for button in row):
                    return message
        return None

    # -- streams --------------------------------------------------------------

    async def push(self, event: dict) -> None:
        for ws in list(self.streams):
            try:
                await ws.send_json(event)
            except Exception:
                logger.debug("dropping a web stream that failed a push", exc_info=True)
                self.streams.discard(ws)

    async def typing(self, on: bool) -> None:
        await self.push({"type": "typing", "on": on})

    # -- internals ------------------------------------------------------------

    def _new_id(self) -> str:
        message_id = str(self.next_id)
        self.next_id += 1
        return message_id

    def _get(self, message_id: int | None) -> dict | None:
        if message_id is None:
            return None
        return next((m for m in self.messages if m["id"] == str(message_id)), None)

    def _append(self, message: dict) -> None:
        self.messages.append(message)
        del self.messages[:-self.cap]
        self._save()

    async def _voice(self, text: str) -> str | None:
        if self.synthesizer is None or self.audio is None:
            return None
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        try:
            note = await self.synthesizer.synthesize(plain_text(text), self.tmp_dir)
        except Exception:
            logger.exception("voice synthesis failed, the web reply goes without audio")
            return None
        try:
            return await self.audio.add(note)
        except Exception:
            logger.exception("storing web reply audio failed, the reply goes without it")
            return None
        finally:
            note.unlink(missing_ok=True)

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
            self.messages = list(data.get("messages", []))[-self.cap:]
            self.next_id = int(data.get("next_id", 1))
        except Exception as exc:
            logger.warning("failed to load the web conversation from %s: %s", self.path, exc)

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"next_id": self.next_id, "messages": self.messages}))
            os.replace(tmp, self.path)
        except Exception:
            logger.exception("failed to save the web conversation to %s", self.path)
