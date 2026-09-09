from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from pathlib import Path
import pytest
from bot.knowledge.models import Profile, Event, Todo
from bot.knowledge.store import KnowledgeStore
from bot.scheduler.state import RuntimeState
from bot.scheduler.checkins import checkin_slots, should_skip_checkin, due_checkin

NY = ZoneInfo("America/New_York")
D = date(2026, 9, 3)
def T(h, m=0): return datetime(2026, 9, 3, h, m, tzinfo=NY)

class FakeAgent:
    async def compose(self, kind, context, fallback): return f"[{kind}] " + fallback

@pytest.fixture
def store(tmp_path):
    s = KnowledgeStore(tmp_path / "k", clock=lambda: T(8)); s.init(); return s

def test_slots_are_hourly_jittered_and_stable():
    a = checkin_slots(Profile(), D); b = checkin_slots(Profile(), D)
    assert a == b and len(a) == 14
    assert all(T(8 + i) <= due < T(9 + i) for i, (_, due) in enumerate(a))
    assert a[0][0] == "checkin:2026-09-03:0"

def test_skip_reasons():
    s = RuntimeState.load(Path("/nonexistent")); p = Profile()
    assert should_skip_checkin(T(10), p, s, []) is None
    s.pause_until = T(11); assert should_skip_checkin(T(10), p, s, []) == "paused"
    s.pause_until = None; s.last_user_message_at = T(9, 50)
    assert should_skip_checkin(T(10), p, s, []) == "active"
    s.last_user_message_at = None
    ev = Event(path="x", title="X", start=T(9, 30), end=T(10, 30))
    assert should_skip_checkin(T(10), p, s, [ev]) == "in_event"

async def test_due_checkin_fires_once_and_opens_chain(store):
    store.add(Todo(path="", title="Call dentist", priority=1)); store.commit("t")
    s = RuntimeState.load(Path("/nonexistent"))
    key, due = checkin_slots(store.profile(), D)[2]
    assert await due_checkin(due - timedelta(minutes=1), store, s, FakeAgent()) is None
    out = await due_checkin(due, store, s, FakeAgent())
    assert out and out.kind == "checkin" and out.voice and "Call dentist" in out.text
    assert out.silent
    assert out.buttons[0][1].startswith("done:") and out.buttons[0][0] == "✅ Done"
    assert [d for _, d in out.buttons[1:]] == ["ack:still", "ack:skip"]
    assert s.chain and s.chain.kind == "checkin" and s.chain.item == "Call dentist"
    assert await due_checkin(due, store, s, FakeAgent()) is None

async def test_skipped_checkin_is_consumed(store):
    s = RuntimeState.load(Path("/nonexistent")); s.pause_until = T(23)
    key, due = checkin_slots(store.profile(), D)[2]
    assert await due_checkin(due, store, s, FakeAgent()) is None and key in s.fired

async def test_composed_prose_is_escaped(store):
    class HtmlAgent:
        async def compose(self, kind, context, fallback): return "Check in <b>now</b>."
    s = RuntimeState.load(Path("/nonexistent"))
    key, due = checkin_slots(store.profile(), D)[2]
    out = await due_checkin(due, store, s, HtmlAgent())
    assert "&lt;b&gt;" in out.text and "<b>now</b>" not in out.text

async def test_composed_prose_markdown_bold(store):
    class MdAgent:
        async def compose(self, kind, context, fallback): return "**Nice work**"
    s = RuntimeState.load(Path("/nonexistent"))
    key, due = checkin_slots(store.profile(), D)[2]
    out = await due_checkin(due, store, s, MdAgent())
    assert "<b>Nice work</b>" in out.text
