from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
import pytest
from bot.knowledge.store import KnowledgeStore
from bot.knowledge.models import Goal
from bot.scheduler.state import RuntimeState, Chain
from bot.scheduler.chains import due_followup, close_chain, chain_gaps

NY = ZoneInfo("America/New_York")
def T(h, m=0): return datetime(2026, 9, 3, h, m, tzinfo=NY)

class FakeAgent:
    def __init__(self): self.kinds = []
    async def compose(self, kind, context, fallback): self.kinds.append(kind); return fallback

@pytest.fixture
def store(tmp_path):
    s = KnowledgeStore(tmp_path / "k", clock=lambda: T(8)); s.init()
    s.add(Goal(path="", title="Ship app", period="2026-W36")); s.commit("g"); return s

def test_gaps():
    from bot.knowledge.models import Profile
    assert chain_gaps(Profile(), "briefing") == [20, 90] and chain_gaps(Profile(), "checkin") == [20]

def test_chain_gaps_caps_per_kind():
    from bot.knowledge.models import Profile
    profile = Profile()
    profile.followup_gaps_minutes = [5, 10, 15, 20, 25, 30]
    assert chain_gaps(profile, "briefing") == [5, 10]
    assert chain_gaps(profile, "checkin") == [5]
    profile.followup_gaps_minutes = [5]
    assert chain_gaps(profile, "briefing") == [5]
    assert chain_gaps(profile, "checkin") == [5]

async def test_briefing_chain_two_steps_then_closes(store):
    s = RuntimeState.load(Path("/nonexistent")); a = FakeAgent()
    s.chain = Chain("briefing", T(8), T(8), 0, "Call dentist", ["brief"])
    assert await due_followup(T(8, 19), store, s, a) is None
    o1 = await due_followup(T(8, 20), store, s, a); assert "Giovanni" not in o1.text and "Call dentist" in o1.text and o1.kind == "followup" and not o1.voice
    assert await due_followup(T(9, 0), store, s, a) is None
    o2 = await due_followup(T(9, 50), store, s, a); assert "leave it there" in o2.text
    assert s.chain is None and a.kinds == ["followup"] * 2

async def test_checkin_chain_one_step(store):
    s = RuntimeState.load(Path("/nonexistent")); a = FakeAgent()
    s.chain = Chain("checkin", T(10), T(10), 0, "X", [])
    assert await due_followup(T(10, 19), store, s, a) is None
    assert await due_followup(T(10, 20), store, s, a)
    assert s.chain is None

async def test_chain_closed_outside_waking_hours(store):
    s = RuntimeState.load(Path("/nonexistent"))
    s.chain = Chain("briefing", T(21, 50), T(21, 50), 0, "X", [])
    assert await due_followup(T(22, 5), store, s, FakeAgent()) is None and s.chain is None

def test_close_chain():
    s = RuntimeState.load(Path("/nonexistent")); s.chain = Chain("checkin", T(10), T(10), 0, "X", [])
    close_chain(s); assert s.chain is None

async def test_composed_prose_is_escaped(store):
    class HtmlAgent:
        async def compose(self, kind, context, fallback): return "Do it <b>now</b>."
    s = RuntimeState.load(Path("/nonexistent")); s.chain = Chain("checkin", T(10), T(10), 0, "X", [])
    out = await due_followup(T(10, 20), store, s, HtmlAgent())
    assert "&lt;b&gt;" in out.text and "<b>now</b>" not in out.text

async def test_composed_prose_markdown_bold(store):
    class MdAgent:
        async def compose(self, kind, context, fallback): return "**Nice work**"
    s = RuntimeState.load(Path("/nonexistent")); s.chain = Chain("checkin", T(10), T(10), 0, "X", [])
    out = await due_followup(T(10, 20), store, s, MdAgent())
    assert "<b>Nice work</b>" in out.text
