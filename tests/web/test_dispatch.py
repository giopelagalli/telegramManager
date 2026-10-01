"""Web button taps go through JD's real callback dispatcher, the one Telegram's taps use."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.agent.agent import Agent
from bot.agent.client import FakeModelClient
from bot.agent.prompts import build_context
from bot.knowledge.store import KnowledgeStore
from bot.scheduler.clock import FakeClock
from bot.scheduler.outbound import Outbound
from bot.scheduler.state import RuntimeState
from bot.telegram.router import Router
from bot.web.door import WebDoor
from bot.web.server import build_app

from .conftest import AUTH, LIFE, TOKEN, FakeWebSocket, make_conversation

NOW = datetime(2026, 10, 1, 14, 0, tzinfo=ZoneInfo("America/New_York"))


@pytest.fixture
def jd(tmp_path):
    clock = FakeClock(NOW)
    store = KnowledgeStore(tmp_path / "k", clock=clock.now)
    store.init()
    state = RuntimeState()
    router = Router(store, Agent(FakeModelClient([]), None, store, clock.now), state, clock, None)
    yield router, state
    build_context.state = None


async def test_a_check_in_button_tapped_on_the_web(tmp_path, jd):
    router, state = jd
    web = make_conversation(tmp_path)
    web.streams.add(FakeWebSocket())
    await web.send(Outbound("How's the essay going?", buttons=[("Later", "ack:later"), ("Skip today", "ack:skip")],
                            kind="checkin"))
    [checkin] = web.history(1)
    async with TestClient(TestServer(build_app(WebDoor(router, web, LIFE), TOKEN))) as client:
        [edit] = (await (await client.post("/callback", headers=AUTH, json={"data": "ack:skip"})).json())["messages"]
        # _ack keeps the text and drops the buttons; on the web that is an edit of the same message.
        assert edit == {k: v for k, v in checkin.items() if k != "buttons"} | {"edit": True}
        assert state.pause_until is not None  # the dispatcher acted: quiet until tomorrow

        [reply] = (await (await client.post("/callback", headers=AUTH, json={"data": "proj:list"})).json())["messages"]
        assert "isn't set up" in reply["text"]  # the proj: buttons reach projects_ui the same way
