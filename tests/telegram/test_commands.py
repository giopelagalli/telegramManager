from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from bot.agent.client import FakeModelClient, FallbackModelClient, ModelResponse, ToolCall
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
    assert o.text.startswith("<b>To do</b>") and o.text.count("\n") == 7 and o.buttons[0][1] == "todo:personal:e:0"
    assert o.buttons[-1] == ("➕ Add", "todo:personal:add")
    assert "All open (7)" in (await r.command("todo", "all"))[0].text


async def test_today_week_goals_backlog(rig):
    r, *_ = rig
    assert "leave by <b>5:40pm</b>" in (await r.command("today", ""))[0].text
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
    assert (await r.command("think", ""))[0].text == "Thinking is on."  # the Spark default

    o = (await r.command("think", "off"))[0]
    assert o.text == "Thinking off — fast mode."
    assert store.profile().thinking is False
    assert r.agent.client.enable_thinking is False
    assert _subject(store) == "profile: thinking off"
    assert (await r.command("think", ""))[0].text == "Thinking is off."

    o = (await r.command("think", "on"))[0]
    assert o.text == "Thinking on — answers are slower but deeper."
    assert store.profile().thinking is True
    assert r.agent.client.enable_thinking is True
    assert _subject(store) == "profile: thinking on"


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
    assert (await r.command("now", ""))[0].text == "Get ready: <b>Lab</b> at 3:00pm, leave by <b>2:40pm</b>."


async def test_now_falls_back_to_the_top_todo(rig):
    r, store, _ = rig
    assert (await r.command("now", ""))[0].text == "Do this: <b>T0</b>"
    store.add(Todo(path="", title="Paper", priority=1, due=NOW.date()))
    store.commit("t")
    assert (await r.command("now", ""))[0].text == "Do this: <b>Paper</b> (due Thu Sep 3)"


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
    assert menu_names == {"todo", "now", "today", "week", "brief", "due", "courses", "schedule", "goals", "pause"}


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


async def test_now_carries_done_and_sprint_buttons_for_a_todo(rig):
    r, store, _ = rig
    out = (await r.command("now", ""))[0]
    assert out.text == "Do this: <b>T0</b>"
    assert [label for label, _ in out.buttons] == ["✅ Done", "🔥 Do it now"]
    assert out.buttons[0][1].startswith("done:todos/") and out.buttons[1][1].startswith("sprint:todos/")


async def test_quick_keys_are_commands_not_captured(rig):
    r, store, _ = rig
    outs = await r.on_text("Now")
    assert outs[0].text == "Do this: <b>T0</b>" and outs[0].buttons
    outs = await r.on_text("today")
    assert "T0" in outs[0].text
    assert r.agent.client.calls == []


def R(*calls):
    return ModelResponse(None, [ToolCall(n, a) for n, a in calls])


async def test_hard_is_the_same_pipeline_on_the_cloud_with_the_remote_view(tmp_path):
    hard = FakeModelClient([], model="qwen")
    cloud = FakeModelClient([R(("add_event", {"title": "Spanish", "start": "2026-09-07T13:15:00-04:00", "repeat_days": ["MO", "FR"]}),
                               ("reply", {"text": "Filed."}))], model="accounts/fireworks/models/deepseek-v4p1-flash")
    r, store, primary = _rig_with_hard(tmp_path, hard)
    store.add_memory("Sister is Anna", kind="fact"); store.add_memory("Ally is the girl from the Saturday party", kind="fact"); store.commit("m")
    r.agent.cloud = cloud
    r.state.recent = [["user", "she left me on read", "2026-09-03T13:00:00-04:00"], ["assistant", "Then don't double text.", "2026-09-03T13:00:00-04:00"]]
    out = (await r.command("hard", "spanish mon/fri 1:15 at the MLC, and what about ally", channel=DM))[0]
    assert out.text.startswith("<i>via deepseek-v4p1-flash</i>") and "Filed." in out.text and "Added weekly: Spanish" in out.text
    assert [e.title for e in store.series()] == ["Spanish"]  # it filed something: same tools as JD
    assert hard.calls == [] and primary.calls == [] and len(cloud.calls) == 1
    sent = "\n".join(m["content"] for m in cloud.calls[0]["messages"])
    assert "Backup model: schedule, todos and goals only." in sent  # the remote view
    assert "Ally is the girl" in sent and "Sister is Anna" not in sent  # only the memories that match
    assert "she left me on read" in sent  # and the conversation
    assert r.state.recent[-1][0] == "assistant" and "Filed." in r.state.recent[-1][1]  # remembered like any exchange


async def test_hard_without_a_cloud_model_still_answers_plainly(tmp_path):
    hard = FakeModelClient([ModelResponse("42", [])], model="glm-5.3")
    r, store, primary = _rig_with_hard(tmp_path, hard)
    out = (await r.command("hard", "what is the answer?", channel=DM))[0]
    assert "42" in out.text and "glm-5.3" in out.text


async def test_due_lists_by_date_and_buttons_finish_change_or_remove(rig):
    r, store, state = rig
    from datetime import date
    for t in store.todos():
        store.delete(t.path)
    store.add(Todo(path="", title="Spanish homework", priority=2, due=date(2026, 9, 4)))
    store.add(Todo(path="", title="CS project", priority=1, due=date(2026, 9, 3)))
    store.add(Todo(path="", title="Someday thing", priority=3))
    store.commit("t")
    out = (await r.command("due", ""))[0]
    assert out.text.splitlines()[1:] == ["today — CS project", "Fri Sep 4 — Spanish homework"]
    assert out.buttons[-1] == ("➕ Add", "todo:due:add") and out.buttons[0][1] == "todo:due:e:0"

    item = (await r.on_callback("todo:due:e:1", 9, "x", out.buttons))[0]
    assert "<b>Spanish homework</b>" in item.text and item.edit_message_id == 9
    assert [b[0] for b in item.buttons] == ["✅ Done", "✏️ Change", "🗑 Remove", "◀ Back"]

    after = (await r.on_callback("todo:due:done:1", 9, "x", item.buttons))[0]
    assert after.toast == "Done: Spanish homework" and "Spanish homework" not in after.text
    assert next(t for t in store.todos() if t.title == "Spanish homework").status == "done"

    ask = (await r.on_callback("todo:due:add", 9, "x", []))[0]
    assert "What's due" in ask.text and state.pending_schedule == {"ui": "todo", "mode": "add", "scope": "due"}
    r.agent.client.responses.append(R(("add_todo", {"title": "Lab report", "priority": 2, "due": "2026-09-05", "verify": "photo"}), ("reply", {"text": "In."})))
    outs = await r.on_text("lab report, saturday")
    assert r.agent.client.calls[-1]["messages"][-1]["content"].startswith("[Adding an assignment")
    assert outs[0].text.startswith("In.") and "Sat Sep 5 — Lab report" in outs[1].text and state.pending_schedule is None


async def test_week_shows_what_is_due_each_day(rig):
    r, store, state = rig
    from datetime import date
    store.add(Todo(path="", title="Spanish homework", priority=2, due=date(2026, 9, 4))); store.commit("t")
    text = (await r.command("week", ""))[0].text
    fri = text.split("<b>Friday, September 4</b>")[1].split("<b>Sat")[0]
    assert "<b>Due:</b> Spanish homework" in fri


async def test_courses_view_and_adding_a_course_a_test_and_an_assignment(rig):
    r, store, state = rig
    from bot.knowledge.models import Course
    store.add_course(Course(path="courses/csci-2670.md", title="CSCI 2670"))
    store.add_series(Event(path="", title="CSCI 2670", start=datetime(2026, 9, 7, 9, 55, tzinfo=NY), repeat_days=["MO"]))
    store.commit("c")
    out = (await r.command("courses", ""))[0]
    assert out.buttons[0] == ("CSCI 2670", "course:view:csci-2670") and out.buttons[-1] == ("➕ Course", "course:add")
    assert "• <b>CSCI 2670</b> — nothing due" in out.text

    view = (await r.on_callback("course:view:csci-2670", 3, "x", out.buttons))[0]
    assert "<b>CSCI 2670</b>" in view.text and "Mon 9:55am" in view.text
    assert [b[0] for b in view.buttons] == ["➕ Assignment", "➕ Test", "◀ Courses"]

    # a new course needs no model
    await r.on_callback("course:add", 3, "x", [])
    outs = await r.on_text("Spanish")
    assert "<b>Spanish</b>" in outs[0].text and any(c.title == "Spanish" for c in store.courses())

    # a test goes in as an exam event on the course
    ask = (await r.on_callback("course:addtest:csci-2670", 3, "x", []))[0]
    assert "Test or quiz" in ask.text
    r.agent.client.responses.append(R(("add_event", {"title": "CSCI 2670 midterm", "start": "2026-10-14T09:00:00-04:00", "kind": "exam", "course": "csci-2670"}),
                                      ("reply", {"text": "In."})))
    outs = await r.on_text("midterm oct 14 9am")
    assert any('kind exam or quiz, course "csci-2670"' in m["content"] for c in r.agent.client.calls for m in c["messages"])
    assert "<b>Tests</b>" in outs[1].text and "CSCI 2670 midterm" in outs[1].text

    # an assignment goes in as a todo on the course, and shows in the course view and in /due
    ask = (await r.on_callback("todo:course:csci-2670:add", 3, "x", []))[0]
    assert "assignment" in ask.text
    r.agent.client.responses.append(R(("add_todo", {"title": "Homework 3", "priority": 2, "due": "2026-09-10", "course": "csci-2670"}), ("reply", {"text": "In."})))
    outs = await r.on_text("homework 3 due sept 10")
    assert 'course "csci-2670"' in r.agent.client.calls[-1]["messages"][-1]["content"]
    assert "<b>Assignments</b>" in outs[1].text and "Homework 3" in outs[1].text
    assert "Homework 3" in (await r.command("due", ""))[0].text
    listing = (await r.command("courses", ""))[0].text
    assert "• <b>CSCI 2670</b> — Homework 3 due <b>Sep 10</b> (+1 more)" in listing.replace("Thu Sep 10", "Sep 10")
    assert "Homework 3" not in (await r.command("todo", ""))[0].text  # course work is not the personal list
