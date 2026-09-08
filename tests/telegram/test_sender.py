import asyncio

import pytest
from telegram import InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.error import NetworkError, TimedOut

from bot.scheduler.outbound import Outbound
from bot.telegram.sender import Sender, plain_text


class FakeBot:
    def __init__(self, fail_times=0, error=NetworkError("boom"), voice_fail_times=0):
        self.calls = []
        self.fail_times = fail_times
        self.error = error
        self.voice_fail_times = voice_fail_times
        self._voice_calls = 0

    async def send_message(self, **kwargs):
        self.calls.append(("send_message", kwargs))
        if len(self.calls) <= self.fail_times:
            raise self.error

    async def send_voice(self, **kwargs):
        self.calls.append(("send_voice", kwargs))
        self._voice_calls += 1
        if self._voice_calls <= self.voice_fail_times:
            raise NetworkError("boom")


@pytest.fixture
def no_sleep(monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return slept


async def test_sends_html_with_inline_buttons():
    bot = FakeBot()
    await Sender(bot, 7).send(Outbound("hi", buttons=[("✅ x", "done:a")]))
    _, kwargs = bot.calls[0]
    assert kwargs["chat_id"] == 7 and kwargs["parse_mode"] == "HTML"
    assert isinstance(kwargs["reply_markup"], InlineKeyboardMarkup)


async def test_location_button_markup():
    bot = FakeBot()
    await Sender(bot, 7).send(Outbound("where are you", location_button=True))
    assert isinstance(bot.calls[0][1]["reply_markup"], ReplyKeyboardMarkup)


async def test_retries_then_succeeds(no_sleep):
    bot = FakeBot(fail_times=2, error=TimedOut())
    await Sender(bot, 7).send(Outbound("hi"))
    assert len(bot.calls) == 3 and no_sleep == [1, 2]


async def test_gives_up_without_raising(no_sleep):
    bot = FakeBot(fail_times=99)
    await Sender(bot, 7).send(Outbound("hi"))
    assert len(bot.calls) == 4 and no_sleep == [1, 2, 4]


async def test_voice_sent_as_plain_text(tmp_path):
    bot = FakeBot()
    seen = []

    class FakeSynth:
        async def synthesize(self, text, out_dir):
            seen.append(text)
            path = tmp_path / "v.ogg"
            path.write_bytes(b"ogg")
            return path

    await Sender(bot, 7, FakeSynth(), tmp_path).send(Outbound("<b>Hi</b> &amp; bye", voice=True))
    assert seen == ["Hi & bye"] and bot.calls[1][0] == "send_voice"


def test_plain_text_strips_markup():
    assert plain_text("<b>a</b>\n<i>b</i>") == "a\nb"


async def test_voice_retry_resends_full_bytes(tmp_path, no_sleep):
    bot = FakeBot(voice_fail_times=1)

    class FakeSynth:
        async def synthesize(self, text, out_dir):
            path = tmp_path / "v.ogg"
            path.write_bytes(b"full ogg bytes")
            return path

    await Sender(bot, 7, FakeSynth(), tmp_path).send(Outbound("hi", voice=True))
    voice_calls = [kwargs for name, kwargs in bot.calls if name == "send_voice"]
    assert len(voice_calls) == 2
    assert voice_calls[0]["voice"] == b"full ogg bytes"
    assert voice_calls[1]["voice"] == b"full ogg bytes"
