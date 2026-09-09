from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from bot.agent.agent import Agent
from bot.agent.client import FakeModelClient
from bot.knowledge.models import Todo
from bot.knowledge.store import KnowledgeStore
from bot.scheduler.clock import FakeClock
from bot.scheduler.state import Chain, RuntimeState
from bot.telegram.router import Router

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 3, 14, 0, tzinfo=NY)


@pytest.fixture
def rig(tmp_path):
    clock = FakeClock(NOW)
    store = KnowledgeStore(tmp_path / "k", clock=clock.now)
    store.init()
    agent = Agent(FakeModelClient([]), None, store, clock.now)
    state = RuntimeState.load(tmp_path / "s.json")
    return Router(store, agent, state, clock, None), store, state


def _todo_message(store):
    a = store.add(Todo(path="", title="Call dentist", priority=1))
    b = store.add(Todo(path="", title="Pay rent", priority=1))
    store.commit("t")
    html = "<b>Top 5</b>\n1. Call dentist — P1\n2. Pay rent — P1"
    buttons = [("✅ Call dentist", f"done:{a}"), ("✅ Pay rent", f"done:{b}")]
    return a, b, html, buttons


async def test_done_edits_the_line_and_drops_its_button(rig):
    router, store, _ = rig
    a, b, html, buttons = _todo_message(store)

    outs = await router.on_callback(f"done:{a}", 55, html, buttons)
    out = outs[0]
    assert out.kind == "edit" and out.edit_message_id == 55
    assert out.text == "<b>Top 5</b>\n<s>1. Call dentist — P1</s>\n2. Pay rent — P1"
    assert out.buttons == [("✅ Pay rent", f"done:{b}")]
    assert out.toast == "Done: Call dentist"
    assert store.get_todo(a).status == "done"


async def test_defer_appends_the_marker(rig):
    router, store, _ = rig
    a, _b, html, buttons = _todo_message(store)

    out = (await router.on_callback(f"defer:{a}", 55, html, buttons))[0]
    assert out.text.splitlines()[1] == "1. Call dentist — P1 → tomorrow"
    assert out.toast == "Moved to tomorrow" and out.edit_message_id == 55
    assert store.get_todo(a).due == date(2026, 9, 4)


async def test_done_without_message_html_still_replies(rig):
    router, store, _ = rig
    a, _b, _html, _buttons = _todo_message(store)

    out = (await router.on_callback(f"done:{a}"))[0]
    assert out.kind == "reply" and out.text == "Done: Call dentist"
    assert out.edit_message_id is None


async def test_verify_ask_toasts_and_still_replies(rig):
    router, store, state = rig
    b = store.add(Todo(path="", title="Car", verify="photo"))
    store.commit("t")

    out = (await router.on_callback(f"done:{b}", 55, "1. Car — P3", [("x", f"done:{b}")]))[0]
    assert out.kind == "reply" and out.toast == "Marked done, verify below"
    assert state.pending_verify.todo_path == b


async def test_ack_still_drops_the_keyboard_and_closes_the_chain(rig):
    router, _store, state = rig
    state.chain = Chain("checkin", NOW, NOW, 0, "Call dentist", ["hi"])

    out = (await router.on_callback("ack:still", 60, "Done yet?", [("⏳", "ack:still")]))[0]
    assert state.chain is None
    assert out.kind == "edit" and out.edit_message_id == 60 and out.buttons == []
    assert out.text == "Done yet?" and out.toast == "Ok, I'll check back later."


async def test_ack_skip_pauses_until_the_end_of_the_day(rig):
    router, store, state = rig
    _start, end = store.profile().waking_window(NOW.date())

    out = (await router.on_callback("ack:skip", 60, "Done yet?", [("⏭", "ack:skip")]))[0]
    assert state.pause_until == end
    assert out.toast == "Quiet until tomorrow morning." and out.kind == "edit"


async def test_ack_without_message_html_replies(rig):
    router, _store, state = rig
    out = (await router.on_callback("ack:still"))[0]
    assert out.kind == "reply" and out.text == "Ok."
