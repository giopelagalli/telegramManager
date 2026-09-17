from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from bot.agent.client import FakeModelClient, FallbackModelClient, ModelResponse
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


async def test_pause_and_resume_button(rig):
    r, store, state = rig
    out = (await r.command("pause", ""))[0]
    assert "2h" in out.text and state.pause_until == NOW.replace(hour=16) and out.buttons == [("▶️ Resume", "resume")]
    await r.command("pause", "30m")
    assert state.pause_until == NOW.replace(minute=30)
    await r.on_callback("resume")
    assert state.pause_until is None


async def test_brief_now_and_shift(rig):
    r, store, state = rig
    o = (await r.command("brief", ""))[0]
    assert "Good morning" in o.text and "morning:2026-09-03" in state.fired
    o = (await r.command("brief", "9am"))[0]
    assert state.briefing_override["morning:2026-09-03"] == "09:00"


async def test_think_reports_state_and_toggles(rig):
    r, store, state = rig
    assert (await r.command("think", ""))[0].text == "Thinking is off."

    o = (await r.command("think", "on"))[0]
    assert o.text == "Thinking on — answers are slower but deeper."
    assert store.profile().thinking is True
    assert r.agent.client.enable_thinking is True
    assert _subject(store) == "profile: thinking on"
    assert (await r.command("think", ""))[0].text == "Thinking is on."

    o = (await r.command("think", "off"))[0]
    assert o.text == "Thinking off — fast mode."
    assert store.profile().thinking is False
    assert r.agent.client.enable_thinking is False
    assert _subject(store) == "profile: thinking off"


async def test_think_unwraps_fallback_client_primary(rig):
    r, store, state = rig
    primary = FakeModelClient([])
    r.agent.client = FallbackModelClient(primary, FakeModelClient([]))

    await r.command("think", "on")

    assert primary.enable_thinking is True
    assert store.profile().thinking is True


async def test_undo(rig):
    r, store, state = rig
    assert "Reverted: s" in (await r.command("undo", ""))[0].text


TOPIC = Channel(-100, 45, UNBOUND)
DM = Channel(42, None, "life")
CS101 = Channel(-100, 45, "course", "cs101")


def _subject(store):
    import subprocess
    return subprocess.run(["git", "log", "-1", "--format=%s"], cwd=store.root,
                          capture_output=True, text=True).stdout.strip()


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
    assert (await r.command("now", ""))[0].text == "Nothing urgent. Rest, or pick something from /backlog."


def test_command_menu_is_the_minimal_set():
    from bot.telegram.commands import COMMANDS

    menu_names = {name for name, _, menu in COMMANDS if menu}
    assert menu_names == {"todo", "now", "today", "week", "brief", "pause"}


async def test_help_lists_only_the_menu_commands(rig):
    r, *_ = rig
    text = (await r.command("help", ""))[0].text
    assert "/todo" in text and "/now" in text and "/bind" not in text and "/think" not in text
    assert "just say it" in text


def _rig_with_hard(tmp_path, hard):
    clock = FakeClock(NOW)
    store = KnowledgeStore(tmp_path / "k", clock=clock.now)
    store.init()
    state = RuntimeState.load(tmp_path / "s.json")
    primary = FakeModelClient([])
    agent = Agent(primary, None, store, clock.now, hard=hard)
    return Router(store, agent, state, clock, None), store, primary


async def test_hard_without_hard_model_configured(rig):
    r, *_ = rig
    out = (await r.command("hard", "why does this happen?"))[0]
    assert out.text == "No hard model configured. Set HARD_MODEL in .env."


async def test_hard_in_dm_answers_with_via_line_and_writes_nothing(tmp_path):
    hard = FakeModelClient([ModelResponse("42", [])], model="glm-5.3")
    r, store, primary = _rig_with_hard(tmp_path, hard)

    out = (await r.command("hard", "what is the answer?", channel=DM))[0]

    assert out.text == "<i>via glm-5.3</i>\n42"
    assert store.todos() == []
    assert primary.calls == [] and len(hard.calls) == 1


async def test_hard_in_a_course_topic_uses_the_hard_client(tmp_path):
    hard = FakeModelClient([ModelResponse("Because of pointers.", [])], model="glm-5.3")
    r, store, primary = _rig_with_hard(tmp_path, hard)
    store.add_course(Course(path="courses/cs101.md", title="Intro to CS"))
    store.commit("c")

    out = (await r.command("hard", "why does this crash?", channel=CS101))[0]

    assert out.text == "<i>via glm-5.3</i>\nBecause of pointers."
    assert primary.calls == []
    assert len(hard.calls) == 1 and hard.calls[0]["tools"] is None
    assert store.sources("cs101") == []


async def test_hard_model_offline(tmp_path):
    class Boom:
        model = "glm-5.3"

        async def chat(self, *a, **k):
            raise ConnectionError("down")

    r, store, primary = _rig_with_hard(tmp_path, Boom())
    out = (await r.command("hard", "why?", channel=DM))[0]
    assert out.text == "The hard model didn't answer; try again."
