from __future__ import annotations

import logging
from pathlib import Path

from telegram import ReactionTypeEmoji

from bot.knowledge.models import UNBOUND, Channel, channel_key
from bot.knowledge.views import esc

logger = logging.getLogger(__name__)

LOW_CONFIDENCE = -0.8
SEEN = "👀"
OUTCOME_EMOJI = {"captured": "✅", "handled": "✅", "inbox": "❌"}


class Handlers:
    """Unwraps telegram updates, calls the router, sends what comes back."""

    def __init__(self, router, sender, transcriber=None, tmp_dir: Path = Path(".")):
        self.router = router
        self.sender = sender
        self.transcriber = transcriber
        self.tmp_dir = Path(tmp_dir)

    async def _send(self, outs) -> None:
        for out in outs:
            await self.sender.send(out)

    async def _react(self, message, emoji: str) -> None:
        """Cosmetic: reactions fail on some chats and must never break the reply."""
        try:
            reaction = [ReactionTypeEmoji(emoji)]
            await self.sender.bot.set_message_reaction(message.chat_id, message.message_id, reaction)
        except Exception:
            logger.debug("reaction %s failed", emoji, exc_info=True)

    async def _typing(self, message) -> None:
        try:
            await self.sender.bot.send_chat_action(message.chat_id, "typing")
        except Exception:
            logger.debug("typing action failed", exc_info=True)

    async def _outcome(self, message) -> None:
        await self._react(message, OUTCOME_EMOJI[self.router.last_outcome])

    def _channel(self, update) -> Channel | None:
        """Where this update came from: the DM is life, a group topic is what it's bound to."""
        chat = update.effective_chat
        if chat is None:
            return None
        if chat.type == "private":
            return Channel(chat.id, None, "life")
        message = update.effective_message
        thread_id = getattr(message, "message_thread_id", None) if message else None
        bound = self.router.store.channels().by_key(channel_key(chat.id, thread_id))
        return bound if bound is not None else Channel(chat.id, thread_id, UNBOUND)

    def command(self, name: str):
        async def handler(update, context):
            arg = " ".join(context.args) if context.args else ""
            await self._send(await self.router.command(name, arg, channel=self._channel(update)))

        return handler

    async def on_text(self, update, context) -> None:
        message = update.effective_message
        await self._react(message, SEEN)
        await self._typing(message)
        outs = await self.router.on_text(message.text, channel=self._channel(update))
        await self._outcome(message)
        await self._send(outs)

    async def on_voice(self, update, context) -> None:
        message = update.effective_message
        channel = self._channel(update)
        await self._react(message, SEEN)
        if self.transcriber is None:
            await self._send(await self.router.on_voice_unavailable(channel=channel))
            return
        await self._typing(message)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        voice = message.voice
        path = self.tmp_dir / f"{voice.file_id}.ogg"
        file = await voice.get_file()
        await file.download_to_drive(path)
        try:
            text, confidence = await self.transcriber.transcribe(path)
        except Exception as exc:
            reason = str(exc) or type(exc).__name__
            await self._send(await self.router.on_voice_failed(reason, channel=channel))
            return
        finally:
            path.unlink(missing_ok=True)
        outs = await self.router.on_text(text, via_voice=True, channel=channel)
        await self._outcome(message)
        if outs and confidence < LOW_CONFIDENCE:
            outs[0].text = f"Heard: “{esc(text)}”\n" + outs[0].text
        await self._send(outs)

    async def on_photo(self, update, context) -> None:
        message = update.effective_message
        await self._react(message, SEEN)
        file = await message.photo[-1].get_file()
        image = await file.download_as_bytearray()
        outs = await self.router.on_photo(
            bytes(image), message.caption, channel=self._channel(update)
        )
        await self._outcome(message)
        await self._send(outs)

    async def on_document(self, update, context) -> None:
        message = update.effective_message
        document = message.document
        await self._react(message, SEEN)
        await self._typing(message)
        file = await document.get_file()
        data = await file.download_as_bytearray()
        outs = await self.router.on_document(
            bytes(data),
            document.file_name or "",
            document.mime_type or "",
            message.caption,
            channel=self._channel(update),
        )
        await self._outcome(message)
        await self._send(outs)

    async def on_location(self, update, context) -> None:
        location = update.effective_message.location
        outs = await self.router.on_location(
            location.latitude, location.longitude, channel=self._channel(update)
        )
        await self._send(outs)

    async def on_callback(self, update, context) -> None:
        query = update.callback_query
        message = query.message
        message_id = message.message_id if message else None
        message_html = message.text_html if message else None
        outs = await self.router.on_callback(
            query.data,
            message_id,
            message_html,
            _buttons(message),
            channel=self._channel(update),
        )
        toast = next((out.toast for out in outs if out.toast), None)
        await query.answer(text=toast) if toast else await query.answer()
        await self._send(outs)


def _buttons(message) -> list[tuple[str, str]] | None:
    markup = getattr(message, "reply_markup", None) if message else None
    if markup is None:
        return None
    return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]
