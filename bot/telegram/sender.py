from __future__ import annotations

import asyncio
import html
import logging
import re
from pathlib import Path
from typing import Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.error import BadRequest, NetworkError

from bot.scheduler.outbound import Outbound

logger = logging.getLogger(__name__)

BACKOFF_SECONDS = (1, 2, 4)
LOCATION_BUTTON_TEXT = "📍 Share my location"

_TAG_RE = re.compile(r"<[^>]+>")


def plain_text(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text))


def channel_resolver(store, dm_chat_id: int) -> Callable[[str], tuple[int, int | None]]:
    """Resolves an Outbound channel to (chat_id, thread_id), falling back to the DM."""
    warned: set[str] = set()

    def resolve(channel: str) -> tuple[int, int | None]:
        if channel == "life":
            return dm_chat_id, None
        kind, _, course = channel.partition(":")
        bound = store.channels().for_kind(kind, course or None)
        if bound is None:
            if channel not in warned:
                warned.add(channel)
                logger.warning("channel %s is not bound, sending to the DM", channel)
            return dm_chat_id, None
        return bound.chat_id, bound.thread_id

    return resolve


class Sender:
    """Sends an Outbound to one chat, retrying transient network failures."""

    def __init__(
        self,
        bot,
        chat_id: int,
        synthesizer=None,
        tmp_dir: Path = Path("."),
        resolve: Callable[[str], tuple[int, int | None]] | None = None,
    ):
        self.bot = bot
        self.chat_id = chat_id
        self.synthesizer = synthesizer
        self.tmp_dir = Path(tmp_dir)
        self.resolve = resolve

    def _target(self, out: Outbound) -> tuple[int, dict]:
        if self.resolve is None:
            return self.chat_id, {}
        chat_id, thread_id = self.resolve(out.channel)
        return chat_id, {} if thread_id is None else {"message_thread_id": thread_id}

    async def send(self, out: Outbound) -> None:
        chat_id, thread = self._target(out)
        if out.edit_message_id is not None:
            await self._edit(out, chat_id)
            return

        try:
            await self._retry(
                lambda: self.bot.send_message(
                    chat_id=chat_id,
                    text=out.text,
                    parse_mode="HTML",
                    reply_markup=_markup(out),
                    disable_notification=out.silent,
                    **thread,
                )
            )
        except NetworkError as exc:
            logger.error("send_message gave up after retries: %s", exc)
            return

        if out.voice and self.synthesizer is not None:
            await self._send_voice(out.text, out.silent, chat_id, thread)

    async def _edit(self, out: Outbound, chat_id: int) -> None:
        # No message_thread_id here: an edit is addressed by chat_id + message_id.
        async def call():
            try:
                return await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=out.edit_message_id,
                    text=out.text,
                    parse_mode="HTML",
                    reply_markup=_markup(out),
                )
            except BadRequest as exc:
                if "message is not modified" not in str(exc).lower():
                    raise
                logger.debug("edit was a no-op: %s", exc)

        try:
            await self._retry(call)
        except NetworkError as exc:
            logger.error("edit_message_text gave up after retries: %s", exc)

    async def _send_voice(self, text: str, silent: bool, chat_id: int, thread: dict) -> None:
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        try:
            path = await self.synthesizer.synthesize(plain_text(text), self.tmp_dir)
        except Exception:
            logger.exception("voice synthesis failed, skipping voice leg")
            return
        try:
            data = path.read_bytes()
            await self._retry(
                lambda: self.bot.send_voice(
                    chat_id=chat_id, voice=data, disable_notification=silent, **thread
                )
            )
        except NetworkError as exc:
            logger.error("send_voice gave up after retries: %s", exc)
        finally:
            path.unlink(missing_ok=True)

    async def _retry(self, call):
        for delay in (*BACKOFF_SECONDS, None):
            try:
                return await call()
            except NetworkError:
                if delay is None:
                    raise
                await asyncio.sleep(delay)
            except Exception:
                logger.exception("telegram call failed")
                raise


def _markup(out: Outbound):
    if out.buttons:
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton(label, callback_data=data)] for label, data in out.buttons]
        )
    if out.location_button:
        return ReplyKeyboardMarkup(
            [[KeyboardButton(LOCATION_BUTTON_TEXT, request_location=True)]],
            one_time_keyboard=True,
            resize_keyboard=True,
        )
    return None
