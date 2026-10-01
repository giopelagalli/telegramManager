"""Telegram and the web share the router's lock: one message at a time, whichever surface."""
import asyncio
from types import SimpleNamespace

from bot.scheduler.outbound import Outbound
from bot.telegram.handlers import Handlers
from bot.web.door import WebDoor

from .conftest import LIFE, make_conversation


class GatedRouter:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.gate = asyncio.Event()
        self.log = []
        self.last_outcome = "handled"

    async def on_text(self, text, via_voice=False, *, channel=None):
        self.log.append(("start", text))
        if text == "web":
            await self.gate.wait()
            self.last_outcome = "inbox"
        else:
            self.last_outcome = "captured"
        self.log.append(("end", text))
        return [Outbound(text, kind="reply")]


class Bot:
    def __init__(self):
        self.reactions = []

    async def send_chat_action(self, chat_id, action):
        pass

    async def set_message_reaction(self, chat_id, message_id, reaction):
        self.reactions.append(reaction[0].emoji)


class Sender:
    def __init__(self):
        self.bot = Bot()
        self.sent = []

    async def send(self, out):
        self.sent.append(out.text)


def telegram_update(text):
    message = SimpleNamespace(text=text, chat_id=1, message_id=5)
    return SimpleNamespace(effective_message=message, effective_chat=SimpleNamespace(id=1, type="private"),
                           effective_user=SimpleNamespace(id=1))


async def test_a_slow_web_message_holds_off_a_telegram_message(tmp_path):
    router, sender = GatedRouter(), Sender()
    door = WebDoor(router, make_conversation(tmp_path), LIFE)
    handlers = Handlers(router, sender)
    assert handlers.lock is router.lock is door.lock

    web = asyncio.create_task(door.message("web"))
    while router.log != [("start", "web")]:
        await asyncio.sleep(0)
    telegram = asyncio.create_task(handlers.on_text(telegram_update("telegram"), None))
    await asyncio.sleep(0.05)
    assert router.log == [("start", "web")]  # Telegram waits for the router

    router.gate.set()
    await asyncio.gather(web, telegram)
    assert router.log == [("start", "web"), ("end", "web"), ("start", "telegram"), ("end", "telegram")]
    # Telegram read its own outcome, not the web's "inbox": no ❌ on the Telegram message.
    assert sender.bot.reactions == [] and sender.sent == ["telegram"]
