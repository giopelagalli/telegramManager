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
    def context(self, now, awaiting=None, remote=False): return "ctx"
    async def compose(self, kind, context, fallback): return f"[{kind}] " + fallback

@pytest.fixture
def store(tmp_path):
    s = KnowledgeStore(tmp_path / "k", clock=lambda: T(8)); s.init(); return s

def test_slots_are_the_profile_times_three_a_day_by_default():
    a = checkin_slots(Profile(), D)
    assert [due for _, due in a] == [T(11), T(15), T(19)]
    assert a[0][0] == "checkin:2026-09-03:0"
    p = Profile(); p.checkin_times = ["13:30"]
    assert checkin_slots(p, D) == [("checkin:2026-09-03:0", T(13, 30))]
    p.checkin_times = []
    assert checkin_slots(p, D) == []

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
    assert out and out.kind == "checkin" and not out.voice and "Call dentist" in out.text
    assert out.buttons[0][1].startswith("done:") and out.buttons[0][0] == "✅ Done"
    assert [d for _, d in out.buttons[1:]] == [f"sprint:{out.buttons[0][1].split(':', 1)[1]}", "ack:still"]
    assert s.chain and s.chain.kind == "checkin" and s.chain.item == "Call dentist"
    assert await due_checkin(due, store, s, FakeAgent()) is None

async def test_skipped_checkin_is_consumed(store):
    s = RuntimeState.load(Path("/nonexistent")); s.pause_until = T(23)
    key, due = checkin_slots(store.profile(), D)[2]
    assert await due_checkin(due, store, s, FakeAgent()) is None and key in s.fired

async def test_composed_prose_is_escaped(store):
    class HtmlAgent:
        def context(self, now, awaiting=None, remote=False): return "ctx"
        async def compose(self, kind, context, fallback): return "Check in <b>now</b>."
    store.add(Todo(path="", title="Ship it")); store.commit("t")
    s = RuntimeState.load(Path("/nonexistent"))
    key, due = checkin_slots(store.profile(), D)[2]
    out = await due_checkin(due, store, s, HtmlAgent())
    assert "&lt;b&gt;" in out.text and "<b>now</b>" not in out.text

async def test_composed_prose_markdown_bold(store):
    class MdAgent:
        def context(self, now, awaiting=None, remote=False): return "ctx"
        async def compose(self, kind, context, fallback): return "**Nice work**"
    store.add(Todo(path="", title="Ship it")); store.commit("t")
    s = RuntimeState.load(Path("/nonexistent"))
    key, due = checkin_slots(store.profile(), D)[2]
    out = await due_checkin(due, store, s, MdAgent())
    assert "<b>Nice work</b>" in out.text


async def test_empty_day_gets_one_planning_prompt_and_no_chain(store):
    from bot.scheduler.checkins import EMPTY_DAY_PROMPT, due_checkin, checkin_slots
    s = RuntimeState.load(__import__("pathlib").Path("/nonexistent"))
    class Quiet:
        def context(self, now, awaiting=None, remote=False): return "ctx"
        async def compose(self, kind, context, fallback): return fallback
    agent = Quiet()
    slots = checkin_slots(store.profile(), T(9).date())
    first = await due_checkin(slots[0][1], store, s, agent)
    assert first is not None and first.text == EMPTY_DAY_PROMPT and s.chain is None
    assert await due_checkin(slots[1][1], store, s, agent) is None


def test_sprint_fires_once_when_time_is_up():
    from bot.scheduler.checkins import due_sprint
    s = RuntimeState.load(__import__("pathlib").Path("/nonexistent"))
    s.sprint = {"path": "todos/x.md", "title": "Write intro", "ends_at": T(10, 25).isoformat()}
    assert due_sprint(T(10, 20), s) is None
    out = due_sprint(T(10, 25), s)
    assert out is not None and "Write intro" in out.text and out.buttons[0][1] == "done:todos/x.md"
    assert s.sprint is None and due_sprint(T(10, 30), s) is None
