"""Proactive messages fan out to Telegram and every open web stream; a web message stays on the web."""
from datetime import datetime

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.connectors import Fanout
from bot.scheduler.engine import Engine
from bot.scheduler.outbound import Outbound
from bot.scheduler.state import RuntimeState
from bot.web.door import WebDoor
from bot.web.server import build_app

from tests.web.conftest import AUTH, LIFE, TOKEN, FakeRouter, FakeWebSocket, make_conversation


class FakeTelegram:
    def __init__(self, fail=None):
        self.sent = []
        self.fail = fail

    async def send(self, out):
        self.sent.append(out)
        if self.fail:
            raise self.fail


def messages(ws):
    return [e["message"] for e in ws.events if e["type"] == "message"]


async def test_a_proactive_message_reaches_telegram_and_every_open_stream(tmp_path):
    telegram, web = FakeTelegram(), make_conversation(tmp_path)
    phone, laptop = FakeWebSocket(), FakeWebSocket()
    web.streams |= {phone, laptop}
    out = Outbound("<b>Morning</b>", buttons=[("Ok", "ack:later")], kind="briefing")
    await Fanout(telegram, web).send(out)
    assert telegram.sent == [out]
    assert messages(phone) == messages(laptop) == web.history(10)
    assert messages(phone)[0]["text"] == "<b>Morning</b>"


async def test_without_a_browser_attached_only_telegram_gets_it(tmp_path):
    telegram, web = FakeTelegram(), make_conversation(tmp_path)
    await Fanout(telegram, web).send(Outbound("check-in", kind="checkin"))
    assert len(telegram.sent) == 1
    assert web.history(10) == []


async def test_a_telegram_failure_still_reaches_the_web_and_still_raises(tmp_path):
    telegram, web, ws = FakeTelegram(fail=RuntimeError("telegram down")), make_conversation(tmp_path), FakeWebSocket()
    web.streams.add(ws)
    with pytest.raises(RuntimeError, match="telegram down"):
        await Fanout(telegram, web).send(Outbound("reminder", kind="reminder"))
    assert len(messages(ws)) == 1


async def test_a_web_failure_never_reaches_the_caller(tmp_path):
    class Broken:
        async def send(self, out):
            raise RuntimeError("socket gone")

    telegram = FakeTelegram()
    await Fanout(telegram, Broken()).send(Outbound("reminder", kind="reminder"))
    assert len(telegram.sent) == 1


async def test_a_dead_stream_is_dropped(tmp_path):
    class Dead:
        async def send_json(self, event):
            raise ConnectionResetError

    web, alive, dead = make_conversation(tmp_path), FakeWebSocket(), Dead()
    web.streams |= {alive, dead}
    await web.send(Outbound("hi"))
    assert web.streams == {alive} and len(messages(alive)) == 1


async def test_the_engine_sends_proactive_messages_through_the_fanout(tmp_path):
    telegram, web, ws = FakeTelegram(), make_conversation(tmp_path), FakeWebSocket()
    web.streams.add(ws)
    engine = Engine(None, None, RuntimeState(), tmp_path / "state.json", None, Fanout(telegram, web), None)
    out = Outbound("Spark's down.", kind="reply")
    await engine._send(out, datetime(2026, 10, 1, 9), [])
    assert telegram.sent == [out]
    assert messages(ws)[0]["text"] == "Spark's down."


async def test_a_web_message_is_answered_on_the_web_only(tmp_path):
    telegram, web, ws = FakeTelegram(), make_conversation(tmp_path), FakeWebSocket()
    web.streams.add(ws)
    Fanout(telegram, web)  # wired as in __main__: the engine's connector, not the door's
    async with TestClient(TestServer(build_app(WebDoor(FakeRouter(), web, LIFE), TOKEN))) as client:
        replies = (await (await client.post("/messages", headers=AUTH, json={"text": "hi"})).json())["messages"]
    assert telegram.sent == []
    assert messages(ws) == []  # the reply is in the response, not pushed
    assert [e["type"] for e in ws.events] == ["typing", "typing"]
    assert web.history(10) == replies
