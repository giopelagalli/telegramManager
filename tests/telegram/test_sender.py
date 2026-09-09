import asyncio

import pytest
from telegram import InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.error import BadRequest, NetworkError, TimedOut

from bot.knowledge.models import Channel, Channels
from bot.scheduler.outbound import Outbound
from bot.telegram.sender import Sender, channel_resolver, plain_text


class FakeBot:
    def __init__(self, fail_times=0, error=NetworkError("boom"), voice_fail_times=0):
        self.calls = []
        self.fail_times = fail_times
        self.error = error
        self.voice_fail_times = voice_fail_times
        self._voice_calls = 0
        self.edit_error = None

    async def send_message(self, **kwargs):
        self.calls.append(("send_message", kwargs))
        if len(self.calls) <= self.fail_times:
            raise self.error

    async def edit_message_text(self, **kwargs):
        self.calls.append(("edit_message_text", kwargs))
        if self.edit_error is not None:
            raise self.edit_error

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


async def test_edit_replaces_text_and_keyboard():
    bot = FakeBot()
    out = Outbound("<s>1. x</s>", buttons=[("✅ y", "done:b")], edit_message_id=12, kind="edit")
    await Sender(bot, 7).send(out)
    name, kwargs = bot.calls[0]
    assert name == "edit_message_text"
    assert kwargs["chat_id"] == 7 and kwargs["message_id"] == 12
    assert kwargs["text"] == "<s>1. x</s>" and kwargs["parse_mode"] == "HTML"
    assert isinstance(kwargs["reply_markup"], InlineKeyboardMarkup)


async def test_edit_without_buttons_clears_the_keyboard():
    bot = FakeBot()
    await Sender(bot, 7).send(Outbound("done", edit_message_id=12, kind="edit"))
    assert bot.calls[0][1]["reply_markup"] is None


async def test_edit_swallows_unmodified_message():
    bot = FakeBot()
    bot.edit_error = BadRequest("Message is not modified: nothing changed")
    await Sender(bot, 7).send(Outbound("same", edit_message_id=12, kind="edit"))
    assert len(bot.calls) == 1


async def test_edit_does_not_send_voice(tmp_path):
    bot = FakeBot()

    class FakeSynth:
        async def synthesize(self, text, out_dir):  # pragma: no cover - must not run
            raise AssertionError("no voice leg for edits")

    await Sender(bot, 7, FakeSynth(), tmp_path).send(
        Outbound("x", voice=True, edit_message_id=12, kind="edit")
    )
    assert [name for name, _ in bot.calls] == ["edit_message_text"]


async def test_silent_flag_reaches_both_legs(tmp_path):
    bot = FakeBot()

    class FakeSynth:
        async def synthesize(self, text, out_dir):
            path = tmp_path / "v.ogg"
            path.write_bytes(b"ogg")
            return path

    await Sender(bot, 7, FakeSynth(), tmp_path).send(Outbound("hi", voice=True, silent=True))
    assert all(kwargs["disable_notification"] for _, kwargs in bot.calls)
    await Sender(bot, 7).send(Outbound("loud"))
    assert bot.calls[-1][1]["disable_notification"] is False


class FakeStore:
    def __init__(self, channels):
        self._channels = channels

    def channels(self):
        return self._channels


async def test_channel_routes_to_the_bound_thread(tmp_path):
    bot = FakeBot()
    channels = Channels()
    channels.bind(Channel(-100, 45, "review"))
    resolve = channel_resolver(FakeStore(channels), 7)

    class FakeSynth:
        async def synthesize(self, text, out_dir):
            path = tmp_path / "v.ogg"
            path.write_bytes(b"ogg")
            return path

    sender = Sender(bot, 7, FakeSynth(), tmp_path, resolve=resolve)
    await sender.send(Outbound("quiz time", voice=True, channel="review"))
    for _, kwargs in bot.calls:
        assert kwargs["chat_id"] == -100 and kwargs["message_thread_id"] == 45


async def test_unbound_channel_falls_back_to_the_dm(caplog):
    bot = FakeBot()
    resolve = channel_resolver(FakeStore(Channels()), 7)
    sender = Sender(bot, 7, resolve=resolve)
    with caplog.at_level("WARNING"):
        await sender.send(Outbound("hi", channel="course:cs101"))
        await sender.send(Outbound("again", channel="course:cs101"))
    _, kwargs = bot.calls[0]
    assert kwargs["chat_id"] == 7 and "message_thread_id" not in kwargs
    assert sum("course:cs101" in r.message for r in caplog.records) == 1


async def test_life_channel_and_no_resolver_go_to_the_dm():
    bot = FakeBot()
    channels = Channels()
    channels.bind(Channel(-100, 45, "review"))
    await Sender(bot, 7, resolve=channel_resolver(FakeStore(channels), 7)).send(Outbound("hi"))
    await Sender(bot, 7).send(Outbound("hi", channel="review"))
    for _, kwargs in bot.calls:
        assert kwargs["chat_id"] == 7 and "message_thread_id" not in kwargs


CANT_PARSE = BadRequest("Can't parse entities: unsupported start tag \"3\"")


async def test_unparsable_html_is_resent_as_plain_text():
    bot = FakeBot(fail_times=1, error=CANT_PARSE)
    await Sender(bot, 7).send(Outbound("<b>a</b> &lt;3"))
    assert [name for name, _ in bot.calls] == ["send_message", "send_message"]
    _, kwargs = bot.calls[1]
    assert kwargs["parse_mode"] is None and kwargs["text"] == "a <3"


async def test_unparsable_html_is_resent_once_only():
    bot = FakeBot(fail_times=2, error=CANT_PARSE)
    await Sender(bot, 7).send(Outbound("<b>a</b>"))
    assert len(bot.calls) == 2


async def test_other_bad_requests_are_not_resent_plain(no_sleep):
    bot = FakeBot(fail_times=1, error=BadRequest("chat not found"))
    await Sender(bot, 7).send(Outbound("hi"))
    assert [kwargs["parse_mode"] for _, kwargs in bot.calls] == ["HTML", "HTML"]


async def test_unparsable_edit_is_resent_as_plain_text():
    class EditFailsOnce(FakeBot):
        async def edit_message_text(self, **kwargs):
            self.calls.append(("edit_message_text", kwargs))
            if len(self.calls) == 1:
                raise CANT_PARSE

    bot = EditFailsOnce()
    await Sender(bot, 7).send(Outbound("<b>a</b>", edit_message_id=5))
    assert [name for name, _ in bot.calls] == ["edit_message_text", "edit_message_text"]
    assert bot.calls[1][1]["parse_mode"] is None and bot.calls[1][1]["text"] == "a"


async def test_target_overrides_the_channel():
    bot = FakeBot()
    channels = Channels()
    channels.bind(Channel(-100, 45, "review"))
    resolve = channel_resolver(FakeStore(channels), 7)
    await Sender(bot, 7, resolve=resolve).send(Outbound("hi", channel="review", target=(-100, 46)))
    _, kwargs = bot.calls[0]
    assert kwargs["chat_id"] == -100 and kwargs["message_thread_id"] == 46
