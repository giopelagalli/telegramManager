from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from bot.agent.client import FakeModelClient
from bot.agent.agent import Agent
from bot.knowledge.store import KnowledgeStore
from bot.knowledge.models import Todo, Event
from bot.scheduler.state import RuntimeState
from bot.scheduler.clock import FakeClock
from bot.telegram.router import Router

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 3, 14, 0, tzinfo=NY)


@pytest.fixture
def rig(tmp_path):
    clock = FakeClock(NOW)
    store = KnowledgeStore(tmp_path / "k", clock=clock.now)
    store.init()
    for i in range(7):
        store.add(Todo(path="", title=f"T{i}", priority=1 + i % 3))
    store.add(Event(path="", title="Gym", start=NOW.replace(hour=18), travel_minutes=20))
    store.commit("s")
    state = RuntimeState.load(tmp_path / "s.json")
    return Router(store, Agent(FakeModelClient([]), None, store, clock.now), state, clock, None), store, state


async def test_todo_top5_with_buttons(rig):
    r, store, state = rig
    o = (await r.command("todo", ""))[0]
    assert o.text.count("\n") >= 5 and len(o.buttons) == 5 and o.buttons[0][1].startswith("done:")
    assert "All open (7)" in (await r.command("todo", "all"))[0].text


async def test_today_week_goals_backlog(rig):
    r, *_ = rig
    assert "leave by 5:40pm" in (await r.command("today", ""))[0].text
    assert "free" in (await r.command("week", ""))[0].text
    assert "No goals" in (await r.command("goals", ""))[0].text
    assert "empty" in (await r.command("backlog", ""))[0].text.lower()


async def test_pause_quiet_resume(rig):
    r, store, state = rig
    assert "2h" in (await r.command("pause", ""))[0].text and state.pause_until == NOW.replace(hour=16)
    await r.command("pause", "30m")
    assert state.pause_until == NOW.replace(minute=30)
    await r.command("quiet", "")
    assert state.pause_until == NOW.replace(hour=22, minute=0)
    await r.command("resume", "")
    assert state.pause_until is None


async def test_brief_now_and_shift(rig):
    r, store, state = rig
    o = (await r.command("brief", ""))[0]
    assert "Good morning" in o.text and "morning:2026-09-03" in state.fired
    o = (await r.command("brief", "9am"))[0]
    assert state.briefing_override["morning:2026-09-03"] == "09:00"


async def test_undo(rig):
    r, store, state = rig
    assert "Reverted: s" in (await r.command("undo", ""))[0].text
