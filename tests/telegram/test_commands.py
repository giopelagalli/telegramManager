from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from bot.agent.client import FakeModelClient
from bot.agent.agent import Agent
from bot.knowledge.store import KnowledgeStore
from bot.knowledge.models import Todo, Event, Channel, Course, Source, UNBOUND
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


TOPIC = Channel(-100, 45, UNBOUND)
DM = Channel(42, None, "life")


def _subject(store):
    import subprocess
    return subprocess.run(["git", "log", "-1", "--format=%s"], cwd=store.root,
                          capture_output=True, text=True).stdout.strip()


async def test_bind_course_creates_the_course_and_the_binding(rig):
    r, store, _ = rig
    out = (await r.command("bind", "course CS101 Intro to CS", channel=TOPIC))[0]
    assert out.text == "Bound this topic to CS101 (Intro to CS)."
    assert out.channel == "course:cs101"
    assert store.get_course("cs101").title == "Intro to CS"
    assert store.channels().by_key("-100:45") == Channel(-100, 45, "course", "cs101")
    assert _subject(store) == "bind: course cs101"

    # a second topic for the same code reuses the existing course
    out = (await r.command("bind", "course CS101", channel=Channel(-100, 46, UNBOUND)))[0]
    assert out.text == "Bound this topic to CS101 (Intro to CS)."
    assert len(store.courses()) == 1


async def test_bind_kind_and_unbind(rig):
    r, store, _ = rig
    out = (await r.command("bind", "assignments", channel=TOPIC))[0]
    assert out.text == "Bound this topic to assignments." and out.channel == "assignments"
    assert store.channels().for_kind("assignments").thread_id == 45

    bound = Channel(-100, 45, "assignments")
    assert "Unbound" in (await r.command("unbind", "", channel=bound))[0].text
    assert store.channels().for_kind("assignments") is None
    assert (await r.command("unbind", "", channel=bound))[0].text == "Nothing is bound here."


async def test_bind_usage_and_dm_refusal(rig):
    r, store, _ = rig
    assert (await r.command("bind", "", channel=DM))[0].text == "Bind topics inside your group, not here."
    assert "Usage" in (await r.command("bind", "", channel=TOPIC))[0].text
    assert "Usage" in (await r.command("bind", "lectures", channel=TOPIC))[0].text
    assert "Usage" in (await r.command("bind", "course", channel=TOPIC))[0].text
    assert store.channels().bindings == {}


async def test_channels_and_courses_listings(rig):
    r, store, _ = rig
    assert "Nothing bound yet" in (await r.command("channels", ""))[0].text
    assert "No courses yet" in (await r.command("courses", ""))[0].text

    await r.command("bind", "course CS101 Intro to CS", channel=TOPIC)
    await r.command("bind", "review", channel=Channel(-100, 46, UNBOUND))
    text = (await r.command("channels", ""))[0].text
    assert "course:cs101 — Intro to CS" in text and "- review" in text
    assert "Intro to CS (cs101) — 0 topics" in (await r.command("courses", ""))[0].text


CS101 = Channel(-100, 45, "course", "cs101")


async def test_now_prefers_an_event_starting_within_90_minutes(rig):
    r, store, _ = rig
    store.add(Event(path="", title="Lab", start=NOW.replace(hour=15), travel_minutes=20))
    store.commit("e")
    assert (await r.command("now", ""))[0].text == "Get ready: Lab at 3:00pm, leave by 2:40pm."


async def test_now_falls_back_to_the_top_todo(rig):
    r, store, _ = rig
    assert (await r.command("now", ""))[0].text == "Do this: T0"
    store.add(Todo(path="", title="Paper", priority=1, due=NOW.date()))
    store.commit("t")
    assert (await r.command("now", ""))[0].text == "Do this: Paper (due Thu Sep 3)"


async def test_now_when_nothing_is_pending(tmp_path):
    from bot.agent.agent import Agent
    from bot.agent.client import FakeModelClient
    from bot.knowledge.store import KnowledgeStore
    from bot.scheduler.clock import FakeClock
    from bot.scheduler.state import RuntimeState
    from bot.telegram.router import Router

    clock = FakeClock(NOW)
    store = KnowledgeStore(tmp_path / "k", clock=clock.now)
    store.init()
    r = Router(store, Agent(FakeModelClient([]), None, store, clock.now),
               RuntimeState.load(tmp_path / "s.json"), clock, None)
    assert (await r.command("now", ""))[0].text == "Nothing urgent. Pick something from /backlog or rest."


async def test_sources_in_a_course_topic_then_summary(rig):
    r, store, state = rig
    await r.command("bind", "course CS101 Intro to CS", channel=TOPIC)
    store.add_source(Source(path="", title="Chapter 4", course="cs101", kind="chapter", pages=12,
                            summary="Pointers and the stack."))
    store.add_source(Source(path="", title="Lecture 7", course="cs101", kind="slides", pages=30))
    store.commit("ingest")

    text = (await r.command("sources", "", channel=CS101))[0].text
    assert text.splitlines() == [
        "<b>Sources — Intro to CS</b>",
        "1. Chapter 4 (chapter, 12 pages)",
        "2. Lecture 7 (slides, 30 pages)",
    ]
    assert len(state.last_sources_listing) == 2
    assert (await r.command("summary", "1"))[0].text == "<b>Chapter 4</b>\nPointers and the stack."
    assert "No summary" in (await r.command("summary", "2"))[0].text
    assert "no 3 in the last" in (await r.command("summary", "3"))[0].text


async def test_sources_everywhere_are_grouped_by_course(rig):
    r, store, state = rig
    assert "No sources yet" in (await r.command("sources", ""))[0].text
    assert "Run /sources first" in (await r.command("summary", "1"))[0].text

    store.add_course(Course(path="courses/cs101.md", title="Intro to CS"))
    store.add_course(Course(path="courses/phys1.md", title="Physics"))
    store.add_source(Source(path="", title="Chapter 4", course="cs101", kind="chapter"))
    store.add_source(Source(path="", title="Waves", course="phys1", kind="notes"))
    store.commit("ingest")

    text = (await r.command("sources", ""))[0].text
    assert text.splitlines() == [
        "<b>Intro to CS</b>",
        "1. Chapter 4 (chapter)",
        "<b>Physics</b>",
        "2. Waves (notes)",
    ]
    assert "Which one?" in (await r.command("summary", "later"))[0].text


async def test_move_sends_the_last_source_to_another_course(rig):
    r, store, _ = rig
    store.add_course(Course(path="courses/cs101.md", title="Intro to CS"))
    store.add_course(Course(path="courses/phys1.md", title="Physics"))
    store.add_source(Source(path="", title="Waves", course="cs101"))
    store.commit("ingest: Waves")

    out = (await r.command("move", "phys1", channel=CS101))[0]
    assert out.text == "Moved Waves to Physics."
    assert store.sources("cs101") == []
    assert store.sources("phys1")[0].course == "phys1"
    assert _subject(store) == "move: Waves -> phys1"


async def test_move_refuses_outside_a_course_or_without_a_target(rig):
    r, store, _ = rig
    store.add_course(Course(path="courses/cs101.md", title="Intro to CS"))
    assert (await r.command("move", "phys1", channel=DM))[0].text == "Run /move inside a course topic."
    assert "&lt;slug&gt;" in (await r.command("move", "", channel=CS101))[0].text
    assert "No course phys9" in (await r.command("move", "phys9", channel=CS101))[0].text
    assert (await r.command("move", "cs101", channel=CS101))[0].text == "Nothing to move here."


async def test_move_takes_every_part_of_a_split_source(rig):
    r, store, _ = rig
    store.add_course(Course(path="courses/cs101.md", title="Intro to CS"))
    store.add_course(Course(path="courses/phys1.md", title="Physics"))
    body = "\n".join(f"line {i}" for i in range(30000))
    assert len(body) > 300_000
    store.add_source(Source(path="", title="Big", course="cs101", body=body))
    store.commit("ingest: Big")
    assert len(store.sources("cs101")) == 2

    out = (await r.command("move", "phys1", channel=CS101))[0]
    assert out.text == "Moved Big to Physics."
    assert store.sources("cs101") == []
    moved = store.sources("phys1")
    assert [s.path.rsplit("/", 1)[-1] for s in moved] == [
        "2026-09-03-big-part-1.md",
        "2026-09-03-big-part-2.md",
    ]
    assert {s.course for s in moved} == {"phys1"}


async def test_bind_and_unbind_answer_in_the_topic_that_asked(rig):
    r, store, _ = rig
    assert (await r.command("bind", "lectures", channel=TOPIC))[0].target == (-100, 45)
    assert (await r.command("bind", "course", channel=TOPIC))[0].target == (-100, 45)
    assert (await r.command("unbind", "", channel=TOPIC))[0].target == (-100, 45)
