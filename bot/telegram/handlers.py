from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from telegram import ReactionTypeEmoji

from bot.knowledge.models import UNBOUND, Channel, channel_key
from bot.knowledge.views import esc

logger = logging.getLogger(__name__)

LOW_CONFIDENCE = -0.8
# What a bot may download through the Telegram API.
FILE_LIMIT_BYTES = 20 * 1024 * 1024
NOT_UNDERSTOOD = "❌"


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

    @asynccontextmanager
    async def _busy(self, message):
        """Keep the typing indicator up for as long as the work takes; Telegram drops it after ~5s."""
        async def keep():
            while True:
                await asyncio.sleep(4)
                await self._typing(message)
        await self._typing(message)
        task = asyncio.create_task(keep())
        try:
            yield
        finally:
            task.cancel()

    async def _outcome(self, message) -> None:
        # Only mark the one case worth knowing about: the message went to the inbox unparsed.
        if self.router.last_outcome == "inbox":
            await self._react(message, NOT_UNDERSTOOD)

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
        logger.info("text from %s: %r", getattr(getattr(update, "effective_user", None), "id", None), (message.text or "")[:40])
        async with self._busy(message):
            outs = await self.router.on_text(message.text, channel=self._channel(update))
        await self._outcome(message)
        await self._send(outs)

    async def on_voice(self, update, context) -> None:
        message = update.effective_message
        channel = self._channel(update)
        if self.transcriber is None:
            await self._send(await self.router.on_voice_unavailable(channel=channel))
            return
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        voice = message.voice
        path = self.tmp_dir / f"{voice.file_id}.ogg"
        async with self._busy(message):
            file = await voice.get_file()
            await file.download_to_drive(path)
            try:
                started = time.monotonic()
                text, confidence = await self.transcriber.transcribe(path)
                logger.info("transcribed in %.1fs: %r", time.monotonic() - started, text[:40])
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
        photo = message.photo[-1]
        if _too_large(getattr(photo, "file_size", None)):
            await self._send(await self.router.on_file_too_large(channel=self._channel(update)))
            return
        async with self._busy(message):
            file = await photo.get_file()
            image = await file.download_as_bytearray()
            outs = await self.router.on_photo(
                bytes(image), message.caption, channel=self._channel(update)
            )
        await self._outcome(message)
        await self._send(outs)

    async def on_document(self, update, context) -> None:
        message = update.effective_message
        document = message.document
        channel = self._channel(update)
        if channel is None or channel.kind != "course":
            # Only a course topic ingests files; nothing else reads the bytes.
            await self._send(await self.router.on_document(b"", "", "", None, channel=channel))
            return
        if _too_large(getattr(document, "file_size", None)):
            await self._send(await self.router.on_file_too_large(channel=channel))
            return
        async with self._busy(message):
            file = await document.get_file()
            data = await file.download_as_bytearray()
            outs = await self.router.on_document(
                bytes(data), document.file_name or "", document.mime_type or "", message.caption, channel=channel
            )
        await self._outcome(message)
        await self._send(outs)

    async def on_location(self, update, context) -> None:
        location = update.effective_message.location
        outs = await self.router.on_location(
            location.latitude, location.longitude, channel=self._channel(update)
        )
        await self._send(outs)

    async def on_forum_topic_created(self, update, context) -> None:
        message = update.effective_message
        outs = self.router.on_topic_named(
            update.effective_chat.id, message.message_thread_id, message.forum_topic_created.name
        )
        await self._send(outs)

    async def on_forum_topic_edited(self, update, context) -> None:
        message = update.effective_message
        name = message.forum_topic_edited.name
        if name is None:
            return
        outs = self.router.on_topic_named(update.effective_chat.id, message.message_thread_id, name)
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


def _too_large(size: int | None) -> bool:
    return size is not None and size > FILE_LIMIT_BYTES


def _buttons(message) -> list[tuple[str, str]] | None:
    markup = getattr(message, "reply_markup", None) if message else None
    if markup is None:
        return None
    return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]
