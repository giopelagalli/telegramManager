from __future__ import annotations

import asyncio
import html
import logging
import re
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.error import NetworkError

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
        try:
            await self._retry(
                lambda: self.bot.send_message(
                    chat_id=self.chat_id,
                    text=out.text,
                    parse_mode="HTML",
                    reply_markup=_markup(out),
                )
            )
        except NetworkError as exc:
            logger.error("send_message gave up after retries: %s", exc)
            return

        if out.voice and self.synthesizer is not None:
            await self._send_voice(out.text)

    async def _send_voice(self, text: str) -> None:
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        try:
            path = await self.synthesizer.synthesize(plain_text(text), self.tmp_dir)
        except Exception:
            logger.exception("voice synthesis failed, skipping voice leg")
            return
        try:
            data = path.read_bytes()
            await self._retry(lambda: self.bot.send_voice(chat_id=self.chat_id, voice=data))
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
