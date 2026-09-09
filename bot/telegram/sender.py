from __future__ import annotations

import asyncio
import html
import logging
import re
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.error import BadRequest, NetworkError

from bot.scheduler.outbound import Outbound

logger = logging.getLogger(__name__)

BACKOFF_SECONDS = (1, 2, 4)
LOCATION_BUTTON_TEXT = "📍 Share my location"

_TAG_RE = re.compile(r"<[^>]+>")


def plain_text(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text))


class Sender:
    """Sends an Outbound to one chat, retrying transient network failures."""

    def __init__(self, bot, chat_id: int, synthesizer=None, tmp_dir: Path = Path(".")):
        self.bot = bot
        self.chat_id = chat_id
        self.synthesizer = synthesizer
        self.tmp_dir = Path(tmp_dir)

    async def send(self, out: Outbound) -> None:
        if out.edit_message_id is not None:
            await self._edit(out)
            return

        try:
            await self._retry(
                lambda: self.bot.send_message(
                    chat_id=self.chat_id,
                    text=out.text,
                    parse_mode="HTML",
                    reply_markup=_markup(out),
                    disable_notification=out.silent,
                )
            )
        except NetworkError as exc:
            logger.error("send_message gave up after retries: %s", exc)
            return

        if out.voice and self.synthesizer is not None:
            await self._send_voice(out.text, out.silent)

    async def _edit(self, out: Outbound) -> None:
        async def call():
            try:
                return await self.bot.edit_message_text(
                    chat_id=self.chat_id,
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

    async def _send_voice(self, text: str, silent: bool = False) -> None:
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
                    chat_id=self.chat_id, voice=data, disable_notification=silent
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
