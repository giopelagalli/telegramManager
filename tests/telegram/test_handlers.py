from types import SimpleNamespace

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


class FakeRouter:
    def __init__(self, outcome="captured", outs=None):
        self.last_outcome = outcome
        self.outs = outs or []
        self.callbacks = []

    async def on_text(self, text, via_voice=False):
        return [Outbound("ok", kind="reply")]

    async def on_callback(self, data, message_id=None, message_html=None, buttons=None):
        self.callbacks.append((data, message_id, message_html, buttons))
        return self.outs


def _rig(outcome="captured", outs=None):
    bot = FakeBot()
    sender = FakeSender(bot)
    router = FakeRouter(outcome, outs)
    return Handlers(router, sender), bot, sender, router


def _text_update():
    message = SimpleNamespace(chat_id=42, message_id=7, text="buy milk")
    return SimpleNamespace(effective_message=message)


async def test_text_reacts_seen_then_captured_and_types():
    handlers, bot, sender, _ = _rig()
    await handlers.on_text(_text_update(), None)
    assert [emoji for _, _, emoji in bot.reactions] == ["👀", "✅"]
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
    await handlers.on_callback(SimpleNamespace(callback_query=query), None)
    assert answered == [{"text": "Done: X"}]
    assert router.callbacks == [("done:a", 9, "1. X", None)]
    assert sender.sent == [out]


async def test_callback_without_toast_answers_empty():
    handlers, _bot, _sender, _ = _rig(outs=[Outbound("hi", kind="reply")])
    answered = []
    query = SimpleNamespace(
        data="snooze:1", message=None, answer=lambda **kw: _record(answered, kw)
    )
    await handlers.on_callback(SimpleNamespace(callback_query=query), None)
    assert answered == [{}]


async def _noop():
    return None


def _record(sink, kwargs):
    sink.append(kwargs)
    return _noop()
