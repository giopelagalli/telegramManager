from datetime import datetime, date
from zoneinfo import ZoneInfo
from pathlib import Path
import pytest
from bot.knowledge.models import Profile, Event, Todo, Goal
from bot.knowledge.store import KnowledgeStore
from bot.scheduler.state import RuntimeState
from bot.scheduler.briefings import briefing_time, morning_text, evening_text, due_briefings

NY = ZoneInfo("America/New_York")
def T(h, m=0, d=3): return datetime(2026, 9, d, h, m, tzinfo=NY)

class FakeAgent:
    async def compose(self, kind, context, fallback): return "Make it count."

@pytest.fixture
def store(tmp_path):
    s = KnowledgeStore(tmp_path / "k", clock=lambda: T(8)); s.init()
    s.add(Event(path="", title="Gym", start=T(18), travel_minutes=20))
    s.add(Event(path="", title="Doctor", start=T(9, 30, d=4), travel_minutes=15))
    s.add(Todo(path="", title="Slipped", due=date(2026, 9, 2)))
    t = Todo(path="", title="Done today", status="done", done_at=T(12), confirmed=False, verify="photo"); s.add(t)
    s.add(Goal(path="", title="Health week", period="2026-W36"))
    s.commit("seed"); return s

def test_briefing_time_override():
    s = RuntimeState.load(Path("/nonexistent"))
    assert briefing_time(Profile(), s, date(2026, 9, 3), "morning") == T(8)
    s.briefing_override["morning:2026-09-03"] = "09:15"
    assert briefing_time(Profile(), s, date(2026, 9, 3), "morning") == T(9, 15)
    assert briefing_time(Profile(), s, date(2026, 9, 4), "morning") == T(8, d=4)

def test_morning_text(store):
    t = morning_text(store, T(8))
    assert t.startswith("<b>Good morning, Giovanni.</b>") and "Gym" in t and "leave by 5:40pm" in t and "Health week" in t

def test_evening_text_sections_and_buttons(store):
    text, buttons = evening_text(store, T(21))
    assert "Slipped" in text and "unconfirmed" in text.lower() and "Doctor" in text and "9:00am" in text
    assert buttons and buttons[0][1].startswith("defer:")

async def test_due_briefings_fire_once_and_open_chain(store):
    s = RuntimeState.load(Path("/nonexistent"))
    assert await due_briefings(T(7, 59), store, s, FakeAgent()) == []
    out = await due_briefings(T(8), store, s, FakeAgent())
    assert len(out) == 1 and out[0].kind == "briefing" and out[0].voice and "Make it count." in out[0].text
    assert s.chain and s.chain.kind == "briefing"
    assert await due_briefings(T(8, 1), store, s, FakeAgent()) == []
    out = await due_briefings(T(21), store, s, FakeAgent())
    assert len(out) == 1 and out[0].buttons

async def test_morning_deferred_when_wake_time_set(store):
    p = store.profile(); p.wake_time = "06:30"; store.save_profile(p)
    s = RuntimeState.load(Path("/nonexistent"))
    assert await due_briefings(T(8), store, s, FakeAgent()) == [] and "morning:2026-09-03" not in s.fired
