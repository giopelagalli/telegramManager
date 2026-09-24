from datetime import datetime, date
from zoneinfo import ZoneInfo
from pathlib import Path
import pytest
from bot.knowledge.models import Profile, Event, Todo, Goal
from bot.knowledge.store import KnowledgeStore
from bot.scheduler.state import RuntimeState
from bot.scheduler.briefings import briefing_time, morning_text, evening_text, due_briefings
from bot.agent.agent import Agent
from bot.agent.client import FakeModelClient

NY = ZoneInfo("America/New_York")
def T(h, m=0, d=3): return datetime(2026, 9, d, h, m, tzinfo=NY)

class FakeAgent:
    def context(self, now, awaiting=None, remote=False): return "ctx"
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
    assert t.startswith("<b>Good morning, Giovanni.</b>") and "Gym" in t and "leave by <b>5:40pm</b>" in t and "Health week" in t

def test_evening_is_a_checklist_with_done_buttons(store):
    store.add(Todo(path="", title="Read & <write>", due=date(2026, 9, 4), due_time="23:59"))
    store.commit("due tomorrow")
    text, buttons = evening_text(store, T(21))
    assert text.splitlines()[:2] == ["<b>Evening wrap-up</b>", "Todo:"]
    assert "• ⚠️ Slipped — due Wed Sep 2" in text
    assert "• Read &amp; &lt;write&gt; — due Fri Sep 4, 11:59pm" in text and "Read & <write>" not in text
    assert text.endswith("\nDone:\n• Done today (unconfirmed)")
    assert [label for label, _ in buttons] == ["✅ Slipped", "✅ Read & <write>"]
    assert all(data.startswith("done:") for _, data in buttons)


def test_evening_is_nothing_but_the_checklist(store):
    # No tomorrow line (leave-by reminders cover getting to class), no goal nag, no slipped section.
    text, _ = evening_text(store, T(21))
    assert "Tomorrow" not in text and "Doctor" not in text
    assert "Nothing toward" not in text and "Slipped:" not in text and "Nothing marked done" not in text


def test_evening_lists_everything_due_by_tomorrow_then_fills_to_five(store):
    for i in range(6):
        store.add(Todo(path="", title=f"Study: day {i}", priority=1, due=date(2026, 9, 10 + i), kind="study"))
    store.add(Todo(path="", title="Essay", priority=3, due=date(2026, 9, 4)))
    store.add(Todo(path="", title="Quiz prep", priority=3, due=date(2026, 9, 3)))
    store.commit("a busy week")
    text, buttons = evening_text(store, T(21))
    todo = text.split("Done:")[0]
    assert "Slipped" in todo and "Quiz prep" in todo and "Essay" in todo  # due by tomorrow, whatever the priority
    assert todo.count("• ") == 5 and "Study: day 1" in todo and "Study: day 2" not in todo
    assert len(buttons) == 5


def test_evening_with_nothing_on_the_list(tmp_path):
    s = KnowledgeStore(tmp_path / "k", clock=lambda: T(8)); s.init()
    text, buttons = evening_text(s, T(21))
    assert text == "<b>Evening wrap-up</b>\nNothing on your list." and buttons == []


def test_evening_does_not_call_unconfirmed_events_missed(store):
    gym = next(e for e in store.events() if e.title == "Gym")
    gym.status = "missed"; store.save(gym); store.commit("passed")  # what the scheduler does 15 min after start
    text, _ = evening_text(store, T(21))
    assert "Missed" not in text and "Gym" not in text

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
    assert len(out) == 1 and out[0].kind == "briefing" and not out[0].voice and "Make it count." not in out[0].text
    assert "<b>Thursday, September 3</b>" in out[0].text and "<b>Goals</b>" in out[0].text
    assert len(out[0].buttons) == 1 and out[0].buttons[0][1].startswith("done:")
    assert s.chain and s.chain.kind == "briefing"
    assert await due_briefings(T(8, 1), store, s, FakeAgent()) == []
    out = await due_briefings(T(21), store, s, FakeAgent())
    assert len(out) == 1 and out[0].buttons

async def test_evening_has_no_composed_prose(store):
    s = RuntimeState.load(Path("/nonexistent"))
    out = (await due_briefings(T(21), store, s, FakeAgent()))[0]
    assert "Make it count." not in out.text and out.text.endswith("Done:\n• Done today (unconfirmed)")


async def test_morning_includes_weather_line_when_home_is_set(store, monkeypatch):
    from bot.scheduler import briefings as B
    class W:
        async def line(self, latlng): return "72° and clear, high 80, UV high."
    monkeypatch.setattr(B, "WEATHER", W())
    p = store.profile(); p.home_latlng = (33.7, -84.4); store.save_profile(p)
    s = RuntimeState.load(__import__("pathlib").Path("/nonexistent"))
    out = await B.send_morning(T(8), store, s, Agent(FakeModelClient([]), None, store, lambda: T(8)))
    assert "72° and clear, high 80, UV high." in out.text.splitlines()[1]


async def test_evening_writes_nightly_notes_from_the_days_conversation(store):
    from bot.memory.thread import remember
    class Notebook:
        degraded = False
        def context(self, now, awaiting=None, remote=False): return "ctx"
        async def compose(self, kind, context, fallback): return "Rest."
        async def consolidate(self, thread, known):
            assert any("ally" in body.lower() for _, body in thread) and isinstance(known, list)
            return [{"kind": "fact", "text": "Ally is the girl from Saturday; she went quiet Wednesday"},
                    {"kind": "state", "text": "Spanish class is wearing on him"}]
    s = RuntimeState.load(Path("/nonexistent"))
    for i, line in enumerate(["ally didn't answer", "spanish sucks", "gym later", "ok fine"]):
        remember(s, "user", line, T(12 + i)); remember(s, "assistant", "…", T(12 + i))
    out = await due_briefings(T(21), store, s, Notebook())
    text = out[-1].text
    assert "Noted today" not in text  # written, not read back
    kinds = {m.text: m.kind for m in store.memories()}
    assert kinds["Ally is the girl from Saturday; she went quiet Wednesday"] == "fact"
    assert kinds["Spanish class is wearing on him"] == "state"


async def test_no_notes_on_a_quiet_day(store):
    class Notebook:
        degraded = False
        def context(self, now, awaiting=None, remote=False): return "ctx"
        async def compose(self, kind, context, fallback): return "Rest."
        async def consolidate(self, thread, known): raise AssertionError("should not run")
    s = RuntimeState.load(Path("/nonexistent"))
    out = await due_briefings(T(21), store, s, Notebook())
    assert out and "Noted today" not in out[-1].text
