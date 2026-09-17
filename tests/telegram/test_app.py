from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from telegram import Chat, ForumTopicCreated, Location, Message, PhotoSize, Update, User, Voice
from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler

from bot.telegram.app import ERROR_REPLY, build_application
from bot.telegram.commands import COMMANDS
from bot.telegram.handlers import Handlers  # noqa: F401  (import smoke test)

MINE = 42
FOREIGN = 99


@dataclass
class FakeSettings:
    telegram_bot_token: str = "123456:AAHfake-token-for-tests"
    telegram_user_id: int = MINE
    data_dir: Path = Path("/tmp")


CONTENT = {
    "text": dict(text="hi"),
    "voice": dict(voice=Voice(file_id="f", file_unique_id="u", duration=1)),
    "photo": dict(photo=(PhotoSize(file_id="f", file_unique_id="u", width=1, height=1),)),
    "location": dict(location=Location(longitude=1.0, latitude=2.0)),
}


def _update(user_id: int, **content) -> Update:
    user = User(id=user_id, first_name="U", is_bot=False)
    chat = Chat(id=user_id, type=Chat.PRIVATE)
    message = Message(
        message_id=1,
        date=datetime(2026, 9, 3, tzinfo=timezone.utc),
        chat=chat,
        from_user=user,
        **content,
    )
    return Update(update_id=1, message=message)


def test_build_application_registers_every_handler():
    app = build_application(FakeSettings(), router=None, sender=None)
    registered = app.handlers[0]
    # one per command, plus text, voice, photo, document, location, forum topic
    # created, forum topic edited, callback
    assert len(registered) == len(COMMANDS) + 8
    assert app.error_handlers


def test_message_handlers_accept_only_the_owner():
    app = build_application(FakeSettings(), router=None, sender=None)
    message_handlers = [h for h in app.handlers[0] if isinstance(h, MessageHandler)]
    assert len(message_handlers) == 7

    for kind, content in CONTENT.items():
        mine = _update(MINE, **content)
        foreign = _update(FOREIGN, **content)
        assert sum(1 for h in message_handlers if h.check_update(mine)) == 1, kind
        assert not any(h.check_update(foreign) for h in message_handlers), kind


def test_command_handlers_filter_out_foreign_users():
    app = build_application(FakeSettings(), router=None, sender=None)
    command_handlers = [h for h in app.handlers[0] if isinstance(h, CommandHandler)]
    assert len(command_handlers) == len(COMMANDS)

    mine = _update(MINE, text="/undo")
    foreign = _update(FOREIGN, text="/undo")
    for handler in command_handlers:
        assert handler.filters is not None
        assert handler.filters.check_update(mine)
        assert not handler.filters.check_update(foreign)


async def test_callback_handler_ignores_foreign_users():
    calls = []

    class FakeRouter:
        async def on_callback(self, data):
            calls.append(data)
            return []

    app = build_application(FakeSettings(), router=FakeRouter(), sender=None)
    callback = next(h for h in app.handlers[0] if isinstance(h, CallbackQueryHandler))
    update = SimpleNamespace(effective_user=SimpleNamespace(id=FOREIGN))
    await callback.callback(update, None)
    assert calls == []


class FakeErrorBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))


def _error_update(user_id: int, chat_id: int, thread_id: int | None = None):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=chat_id),
        effective_message=SimpleNamespace(message_thread_id=thread_id),
    )


async def test_error_handler_replies_to_the_owner_only():
    bot = FakeErrorBot()
    app = build_application(FakeSettings(), router=None, sender=None)
    handler = next(iter(app.error_handlers))
    context = SimpleNamespace(error=RuntimeError("boom"), bot=bot)

    await handler(_error_update(FOREIGN, FOREIGN), context)
    assert bot.sent == []

    await handler(_error_update(MINE, MINE), context)
    assert bot.sent == [(MINE, ERROR_REPLY, {})]


async def test_error_handler_replies_in_the_group_topic_that_errored():
    bot = FakeErrorBot()
    app = build_application(FakeSettings(), router=None, sender=None)
    handler = next(iter(app.error_handlers))
    context = SimpleNamespace(error=RuntimeError("boom"), bot=bot)

    await handler(_error_update(MINE, -100, 45), context)
    assert bot.sent == [(-100, ERROR_REPLY, {"message_thread_id": 45})]


def _group_update(user_id: int, thread_id: int | None = None, **content) -> Update:
    user = User(id=user_id, first_name="U", is_bot=False)
    chat = Chat(id=-100, type=Chat.SUPERGROUP, is_forum=True)
    message = Message(
        message_id=1,
        date=datetime(2026, 9, 3, tzinfo=timezone.utc),
        chat=chat,
        from_user=user,
        message_thread_id=thread_id,
        **content,
    )
    return Update(update_id=1, message=message)


def test_group_topic_messages_reach_the_handlers():
    """filters.TEXT & filters.User already cover groups; only the owner is answered."""
    app = build_application(FakeSettings(), router=None, sender=None)
    message_handlers = [h for h in app.handlers[0] if isinstance(h, MessageHandler)]
    mine = _group_update(MINE, thread_id=45, text="hi")
    assert sum(1 for h in message_handlers if h.check_update(mine)) == 1
    assert not any(h.check_update(_group_update(FOREIGN, 45, text="hi")) for h in message_handlers)

    # /todo@BotName in a group is unwrapped by CommandHandler itself; the owner filter
    # is what this build has to get right.
    command_handlers = [h for h in app.handlers[0] if isinstance(h, CommandHandler)]
    todo = next(h for h in command_handlers if "todo" in h.commands)
    assert todo.filters.check_update(_group_update(MINE, 45, text="/todo"))
    assert not todo.filters.check_update(_group_update(FOREIGN, 45, text="/todo"))


def test_forum_topic_created_reaches_the_handler_owner_only():
    app = build_application(FakeSettings(), router=None, sender=None)
    message_handlers = [h for h in app.handlers[0] if isinstance(h, MessageHandler)]
    created = dict(forum_topic_created=ForumTopicCreated(name="CS101", icon_color=0))
    mine = _group_update(MINE, thread_id=45, **created)
    foreign = _group_update(FOREIGN, thread_id=45, **created)
    assert sum(1 for h in message_handlers if h.check_update(mine)) == 1
    assert not any(h.check_update(foreign) for h in message_handlers)
