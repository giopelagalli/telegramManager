from datetime import datetime, date
from zoneinfo import ZoneInfo
import pytest
from bot.agent.client import FakeModelClient, ModelResponse, ToolCall
from bot.agent.agent import Agent
from bot.knowledge.store import KnowledgeStore
from bot.knowledge.models import Todo, Event
from bot.scheduler.state import RuntimeState, Chain, CriticalLeaveState
from bot.scheduler.clock import FakeClock
from bot.telegram.router import Router

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 3, 14, 0, tzinfo=NY)


def R(*calls):
    return ModelResponse(None, [ToolCall(n, a) for n, a in calls])


@pytest.fixture
def rig(tmp_path):
    clock = FakeClock(NOW)
    store = KnowledgeStore(tmp_path / "k", clock=clock.now)
    store.init()
    client = FakeModelClient([])
    agent = Agent(client, None, store, clock.now)
    state = RuntimeState.load(tmp_path / "s.json")
    return Router(store, agent, state, clock, None), store, client, state, clock


async def test_text_capture_closes_chain_and_echoes(rig):
    router, store, client, state, _ = rig
    state.chain = Chain("checkin", NOW, NOW, 0, "X", [])
    client.responses.append(R(("add_todo", {"title": "Buy milk", "priority": 3}), ("reply", {"text": "Sure."})))
    outs = await router.on_text("buy milk")
    assert state.chain is None and state.last_user_message_at == NOW
    assert outs[0].text == "Sure.\nAdded todo: Buy milk (P3)" and outs[0].kind == "reply" and not outs[0].voice
    assert "Awaiting answer to: X" in client.calls[0]["messages"][1]["content"]


async def test_voice_in_voice_out_default(rig):
    router, store, client, state, _ = rig
    client.responses.append(R(("reply", {"text": "Ok."})))
    assert (await router.on_text("hi", via_voice=True))[0].voice


async def test_snooze_sets_pause(rig):
    router, store, client, state, _ = rig
    client.responses.append(R(("snooze", {"minutes": 120}), ("reply", {"text": "Ok, quiet for 2h."})))
    await router.on_text("not now")
    assert state.pause_until == NOW.replace(hour=16)


async def test_critical_text_and_location(rig):
    router, store, client, state, _ = rig
    p = store.add(Event(path="", title="Flight", start=NOW.replace(hour=18), travel_minutes=60, importance="critical"))
    store.commit("e")
    state.critical = CriticalLeaveState(p, "lead", NOW, None, 0)
    assert "Words don't count" in (await router.on_text("I left"))[0].text
    outs = await router.on_location(40.0, -74.0)
    assert state.critical is None and store.get_event(p).status == "left"


async def test_done_button_with_and_without_verify(rig):
    router, store, client, state, _ = rig
    a = store.add(Todo(path="", title="Easy"))
    b = store.add(Todo(path="", title="Car", verify="photo"))
    store.commit("t")
    assert (await router.on_callback(f"done:{a}"))[0].text == "Done: Easy" and store.get_todo(a).status == "done"
    outs = await router.on_callback(f"done:{b}")
    assert "photo" in outs[0].text.lower() and state.pending_verify.todo_path == b
    assert store.get_todo(b).status == "done" and store.get_todo(b).confirmed is False


async def test_photo_verifies_pending(rig):
    router, store, client, state, _ = rig
    b = store.add(Todo(path="", title="Car", verify="photo"))
    store.commit("t")
    await router.on_callback(f"done:{b}")
    outs = await router.on_photo(b"img")  # no vision client -> None -> accepted with note
    assert state.pending_verify is None and "verif" in outs[0].text.lower()


async def test_defer_button(rig):
    router, store, client, state, _ = rig
    a = store.add(Todo(path="", title="Slip", due=date(2026, 9, 1)))
    store.commit("t")
    assert "tomorrow" in (await router.on_callback(f"defer:{a}"))[0].text.lower()
    assert store.get_todo(a).due == date(2026, 9, 4)
