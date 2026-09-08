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

def test_evening_escapes_tomorrow_title(store):
    store.add(Event(path="", title="Dinner & <Sam>", start=T(7, d=4), travel_minutes=10))
    store.commit("tomorrow event")
    text, _ = evening_text(store, T(21))
    assert "Dinner &amp; &lt;Sam&gt;" in text
    assert "Dinner & <Sam>" not in text

def test_morning_warns_about_broken_files(store):
    # "---\ntype: todo\n:::" without a closing delimiter isn't parsed as
    # frontmatter at all (python-frontmatter just treats it as body with no
    # metadata) so it doesn't reach the YAML parser and never raises. Use
    # unterminated flow-mapping YAML inside a closed frontmatter block so
    # PyYAML actually raises and the file lands in store.broken_files.
    (store.root / "todos" / "2026-09-01-bad.md").write_text("---\n{not valid yaml\n---\n")
    t = morning_text(store, T(8))
    assert "⚠️ 1 file(s) in the bundle couldn't be read; see the log." in t

async def test_due_briefings_fire_once_and_open_chain(store):
    s = RuntimeState.load(Path("/nonexistent"))
    assert await due_briefings(T(7, 59), store, s, FakeAgent()) == []
    out = await due_briefings(T(8), store, s, FakeAgent())
    assert len(out) == 1 and out[0].kind == "briefing" and out[0].voice and "Make it count." in out[0].text
    assert len(out[0].buttons) == 1 and out[0].buttons[0][1].startswith("done:")
    assert s.chain and s.chain.kind == "briefing"
    assert await due_briefings(T(8, 1), store, s, FakeAgent()) == []
    out = await due_briefings(T(21), store, s, FakeAgent())
    assert len(out) == 1 and out[0].buttons

async def test_morning_deferred_when_wake_time_set(store):
    p = store.profile(); p.wake_time = "06:30"; store.save_profile(p)
    s = RuntimeState.load(Path("/nonexistent"))
    assert await due_briefings(T(8), store, s, FakeAgent()) == [] and "morning:2026-09-03" not in s.fired

async def test_composed_prose_is_escaped(store):
    class HtmlAgent:
        async def compose(self, kind, context, fallback): return "Watch out <b>now</b>."
    s = RuntimeState.load(Path("/nonexistent"))
    out = (await due_briefings(T(8), store, s, HtmlAgent()))[0]
    assert "&lt;b&gt;" in out.text and "<b>now</b>" not in out.text


async def test_evening_escapes_composed_prose(store):
    class Sharp:
        async def compose(self, kind, context, fallback): return "<Dune> & rest"

    s = RuntimeState.load(Path("/nonexistent"))
    out = await due_briefings(T(21), store, s, Sharp())
    assert out and out[-1].text.endswith("&lt;Dune&gt; &amp; rest")
