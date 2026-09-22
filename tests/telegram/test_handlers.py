from types import SimpleNamespace

from bot.knowledge.models import UNBOUND, Channel, Channels
from bot.scheduler.outbound import Outbound
from bot.telegram.handlers import Handlers


class FakeBot:
    def __init__(self, fail=False):
        self.reactions = []
        self.actions = []
        self.fail = fail

    async def set_message_reaction(self, chat_id, message_id, reaction):
        if self.fail:
            raise RuntimeError("reactions not allowed here")
        self.reactions.append((chat_id, message_id, reaction[0].emoji))

    async def send_chat_action(self, chat_id, action):
        self.actions.append((chat_id, action))


class FakeSender:
    def __init__(self, bot):
        self.bot = bot
        self.sent = []

    async def send(self, out):
        self.sent.append(out)


class FakeStore:
    def __init__(self, channels=None):
        self._channels = channels or Channels()

    def channels(self):
        return self._channels


class FakeRouter:
    def __init__(self, outcome="captured", outs=None, store=None):
        self.last_outcome = outcome
        self.outs = outs or []
        self.callbacks = []
        self.channels = []
        self.documents = []
        self.too_large = []
        self.store = store or FakeStore()

    async def on_text(self, text, via_voice=False, *, channel=None):
        self.channels.append(channel)
        return [Outbound("ok", kind="reply")]

    async def on_callback(self, data, message_id=None, message_html=None, buttons=None, *, channel=None):
        self.callbacks.append((data, message_id, message_html, buttons))
        return self.outs

    async def on_document(self, data, filename, mime, caption=None, *, channel=None):
        self.documents.append((data, filename, mime, caption, channel))
        return [Outbound("stored", kind="reply")]

    async def on_file_too_large(self, *, channel=None):
        self.too_large.append(channel)
        return [Outbound("too big", kind="reply")]


def _rig(outcome="captured", outs=None, store=None):
    bot = FakeBot()
    sender = FakeSender(bot)
    router = FakeRouter(outcome, outs, store)
    return Handlers(router, sender), bot, sender, router


def _text_update(chat_id=42, chat_type="private", thread_id=None):
    message = SimpleNamespace(
        chat_id=chat_id, message_id=7, text="buy milk", message_thread_id=thread_id
    )
    chat = SimpleNamespace(id=chat_id, type=chat_type)
    return SimpleNamespace(effective_message=message, effective_chat=chat)


async def test_text_reacts_seen_then_captured_and_types():
    handlers, bot, sender, _ = _rig()
    await handlers.on_text(_text_update(), None)
    assert bot.reactions == []  # no receipt reactions; only ❌ on an unparsed message
    assert bot.actions == [(42, "typing")]
    assert len(sender.sent) == 1


async def test_inbox_message_gets_a_cross():
    handlers, bot, _sender, _ = _rig(outcome="inbox")
    await handlers.on_text(_text_update(), None)
    assert bot.reactions[-1][2] == "❌"


async def test_failing_reactions_do_not_break_the_reply():
    handlers, bot, sender, _ = _rig()
    bot.fail = True
    await handlers.on_text(_text_update(), None)
    assert len(sender.sent) == 1


async def test_callback_answers_with_the_toast():
    out = Outbound("edited", edit_message_id=9, toast="Done: X", kind="edit")
    handlers, _bot, sender, router = _rig(outs=[out])
    answered = []

    query = SimpleNamespace(
        data="done:a",
        message=SimpleNamespace(message_id=9, text_html="1. X", reply_markup=None),
        answer=lambda **kw: _record(answered, kw),
    )
    await handlers.on_callback(_callback_update(query), None)
    assert answered == [{"text": "Done: X"}]
    assert router.callbacks == [("done:a", 9, "1. X", None)]
    assert sender.sent == [out]


async def test_callback_without_toast_answers_empty():
    handlers, _bot, _sender, _ = _rig(outs=[Outbound("hi", kind="reply")])
    answered = []
    query = SimpleNamespace(
        data="snooze:1", message=None, answer=lambda **kw: _record(answered, kw)
    )
    await handlers.on_callback(_callback_update(query), None)
    assert answered == [{}]


def _callback_update(query):
    return SimpleNamespace(
        callback_query=query,
        effective_message=None,
        effective_chat=SimpleNamespace(id=42, type="private"),
    )


async def test_dm_is_the_life_channel():
    handlers, _bot, _sender, router = _rig()
    await handlers.on_text(_text_update(), None)
    assert router.channels == [Channel(42, None, "life")]


async def test_group_topic_channel_comes_from_the_bindings():
    channels = Channels()
    channels.bind(Channel(-100, 45, "course", "cs101"))
    handlers, _bot, _sender, router = _rig(store=FakeStore(channels))
    await handlers.on_text(_text_update(-100, "supergroup", 45), None)
    await handlers.on_text(_text_update(-100, "supergroup", 46), None)
    assert router.channels == [Channel(-100, 45, "course", "cs101"), Channel(-100, 46, UNBOUND)]


async def _noop():
    return None


def _record(sink, kwargs):
    sink.append(kwargs)
    return _noop()


class FakeFile:
    def __init__(self, data):
        self.data = data

    async def download_as_bytearray(self):
        return bytearray(self.data)


def _document_update(chat_id=-100, thread_id=45):
    document = SimpleNamespace(
        file_name="ch4.pdf",
        mime_type="application/pdf",
        get_file=lambda: _wrap(FakeFile(b"%PDF")),
    )
    message = SimpleNamespace(
        chat_id=chat_id, message_id=7, message_thread_id=thread_id,
        document=document, caption="chapter 4",
    )
    chat = SimpleNamespace(id=chat_id, type="supergroup")
    return SimpleNamespace(effective_message=message, effective_chat=chat)


async def test_document_is_downloaded_and_routed_with_its_channel():
    channels = Channels()
    channels.bind(Channel(-100, 45, "course", "cs101"))
    handlers, bot, sender, router = _rig(store=FakeStore(channels))
    await handlers.on_document(_document_update(), None)
    assert router.documents == [
        (b"%PDF", "ch4.pdf", "application/pdf", "chapter 4", Channel(-100, 45, "course", "cs101"))
    ]
    assert bot.reactions == []  # no receipt reactions; only ❌ on an unparsed message
    assert bot.actions == [(-100, "typing")]
    assert len(sender.sent) == 1


async def _wrap(value):
    return value


async def test_a_document_in_the_dm_is_downloaded():
    handlers, bot, sender, router = _rig()
    update = _document_update(chat_id=42, thread_id=None)
    update.effective_chat.type = "private"
    await handlers.on_document(update, None)
    assert router.documents == [(b"%PDF", "ch4.pdf", "application/pdf", "chapter 4", Channel(42, None, "life"))]
    assert bot.actions == [(42, "typing")]


async def test_a_document_over_the_cap_is_not_downloaded():
    channels = Channels()
    channels.bind(Channel(-100, 45, "course", "cs101"))
    handlers, bot, sender, router = _rig(store=FakeStore(channels))
    update = _document_update()
    update.effective_message.document.file_size = 21 * 1024 * 1024
    update.effective_message.document.get_file = _boom

    await handlers.on_document(update, None)
    assert router.documents == [] and router.too_large == [Channel(-100, 45, "course", "cs101")]
    assert len(sender.sent) == 1


async def test_a_document_in_an_unbound_topic_is_not_downloaded():
    handlers, bot, sender, router = _rig()
    update = _document_update()
    update.effective_message.document.get_file = _boom

    await handlers.on_document(update, None)
    assert router.documents == [(b"", "", "", None, Channel(-100, 45, UNBOUND))]


def _boom():
    raise AssertionError("the file must not be downloaded")
