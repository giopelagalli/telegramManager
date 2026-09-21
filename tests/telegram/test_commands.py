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
    assert menu_names == {"todo", "now", "today", "week", "brief", "goals", "pause"}


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
    assert out.text == "Do this: T0"
    assert [label for label, _ in out.buttons] == ["✅ Done", "🔥 Do it now"]
    assert out.buttons[0][1].startswith("done:todos/") and out.buttons[1][1].startswith("sprint:todos/")


async def test_quick_keys_are_commands_not_captured(rig):
    r, store, _ = rig
    outs = await r.on_text("Now")
    assert outs[0].text == "Do this: T0" and outs[0].buttons
    outs = await r.on_text("today")
    assert "T0" in outs[0].text
    assert r.agent.client.calls == []


async def test_hard_prefers_the_cloud_opt_in_with_the_blind_view(tmp_path):
    hard = FakeModelClient([ModelResponse("spark says", [])], model="qwen")
    cloud = FakeModelClient([ModelResponse("cloud says", [])], model="deepseek")
    r, store, primary = _rig_with_hard(tmp_path, hard)
    store.add_memory("Sister is Anna", kind="fact"); store.commit("m")
    r.agent.cloud = cloud
    out = (await r.command("hard", "what should I do?", channel=DM))[0]
    assert "cloud says" in out.text and "deepseek" in out.text
    assert hard.calls == [] and len(cloud.calls) == 1
    sent = "\n".join(m["content"] for m in cloud.calls[0]["messages"])
    assert "Anna" not in sent and "Backup model: schedule, todos and goals only." in sent


async def test_hard_carries_the_conversation_but_not_the_memories(tmp_path):
    cloud = FakeModelClient([ModelResponse("cloud says", [])], model="deepseek")
    r, store, primary = _rig_with_hard(tmp_path, None)
    store.add_memory("Sister is Anna", kind="fact"); store.commit("m")
    r.agent.cloud = cloud
    store.add_memory("Ally is the girl from the Saturday party", kind="fact"); store.commit("m2")
    r.state.recent = [["user", "she left me on read"], ["assistant", "Then don't double text."]]
    await r.command("hard", "what do you think about Ally?", channel=DM)
    sent = "\n".join(m["content"] for m in cloud.calls[0]["messages"])
    assert "she left me on read" in sent and "don't double text" in sent
    assert "Ally is the girl" in sent and "Anna" not in sent  # only the memories that match the question


async def test_hard_searches_the_web_first_when_search_is_configured(tmp_path):
    cloud = FakeModelClient([ModelResponse("Merriam-Webster added 'rizz' in 2023.", [])], model="accounts/fireworks/models/deepseek-v4p1-flash")
    r, store, primary = _rig_with_hard(tmp_path, None)
    r.agent.cloud = cloud
    class FakeSearch:
        def __init__(self): self.queries = []
        async def search(self, q): self.queries.append(q); return "1. New words 2026 — https://example.com/words"
    r.search = FakeSearch()
    out = (await r.command("hard", "what words were added to the dictionary this year", channel=DM))[0]
    assert r.search.queries == ["what words were added to the dictionary this year"]
    sent = "\n".join(m["content"] for m in cloud.calls[0]["messages"])
    assert "example.com/words" in sent and "cite the URL" in sent
    assert out.text.startswith("<i>via deepseek-v4p1-flash</i>")


async def test_queue_command_reports_the_cluster(rig):
    r, store, _ = rig
    class FakeCluster:
        async def report(self, now): return "Spark: idle.\n• Probability engine — active"
    r.cluster = FakeCluster()
    assert (await r.command("queue", ""))[0].text == "Spark: idle.\n• Probability engine — active"
    r.cluster = None
    assert (await r.command("queue", ""))[0].text.startswith("Nothing to report")


async def test_voice_command_switches_reply_mode(rig):
    r, store, _ = rig
    assert "when you send voice" in (await r.command("voice", ""))[0].text
    assert (await r.command("voice", "on"))[0].text == "Voice on every reply."
    assert store.profile().voice_reply_mode == "always"
    await r.command("voice", "off")
    assert store.profile().voice_reply_mode == "on_voice"


async def test_schedule_is_day_buttons_then_entries_then_change_or_remove(rig):
    r, store, state = rig
    from bot.knowledge.models import Event
    from datetime import datetime
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
    store.add_series(Event(path="", title="CSCI 2670", start=datetime(2026, 9, 7, 9, 55, tzinfo=NY),
                           end=datetime(2026, 9, 7, 10, 50, tzinfo=NY), location="Dawson Hall", travel_minutes=30, repeat_days=["MO"]))
    store.add_series(Event(path="", title="Spanish", start=datetime(2026, 9, 7, 13, 15, tzinfo=NY), repeat_days=["MO", "FR"]))
    store.commit("classes")
    days = (await r.command("schedule", ""))[0]
    assert [b[0] for b in days.buttons] == ["Mon · 2", "Tue", "Wed", "Thu", "Fri · 1", "Sat", "Sun"]

    monday = (await r.on_callback("sched:day:MO", 5, "x", days.buttons))[0]
    assert monday.edit_message_id == 5 and "<b>Monday</b>" in monday.text and "9:55am–10:50am CSCI 2670 · Dawson Hall" in monday.text
    assert monday.buttons[0][0].startswith("9:55am CSCI 2670") and monday.buttons[-2][0] == "➕ Add to Monday"

    entry = (await r.on_callback(monday.buttons[0][1], 5, "x", monday.buttons))[0]
    assert "<b>CSCI 2670</b>" in entry.text and "30 min to get there" in entry.text
    assert [b[0] for b in entry.buttons] == ["✏️ Change", "🗑 Remove", "◀ Back"]

    after = (await r.on_callback(entry.buttons[1][1], 5, "x", entry.buttons))[0]
    assert after.toast.startswith("Removed CSCI 2670") and "CSCI 2670" not in after.text and [e.title for e in store.series()] == ["Spanish"]


async def test_schedule_add_and_change_go_through_the_model_with_a_hint(rig):
    r, store, state = rig
    ask = (await r.on_callback("sched:add:TU", 5, "x", []))[0]
    assert ask.text.startswith("Tuesday:") and state.pending_schedule == {"mode": "add", "day": "TU"}
    r.agent.client.responses.append(__import__("bot.agent.client", fromlist=["ModelResponse"]).ModelResponse(None, [
        __import__("bot.agent.client", fromlist=["ToolCall"]).ToolCall("add_event", {"title": "CSCI 2720", "start": "2026-09-08T08:15:00-04:00", "end": "2026-09-08T09:35:00-04:00", "location": "Cedar Street Building C, UGA, Athens GA", "travel_minutes": 30, "repeat_days": ["TU"]}),
        __import__("bot.agent.client", fromlist=["ToolCall"]).ToolCall("reply", {"text": "In."}),
    ]))
    outs = await r.on_text("CSCI 2720 8:15-9:35 at Cedar Street Building C, 30 min")
    sent = r.agent.client.calls[-1]["messages"][-1]["content"]
    assert sent.startswith("[Weekly schedule, adding to Tuesday") and 'repeat_days ["TU"]' in sent
    assert state.pending_schedule is None
    assert outs[0].text.startswith("In.") and "<b>Tuesday</b>" in outs[1].text and "CSCI 2720" in outs[1].text

    entry_index = [e.title for e in store.series()].index("CSCI 2720")
    ask = (await r.on_callback(f"sched:change:{entry_index}", 5, "x", []))[0]
    assert "what changes?" in ask.text and state.pending_schedule["mode"] == "change"
    path = state.pending_schedule["path"]
    r.agent.client.responses.append(__import__("bot.agent.client", fromlist=["ModelResponse"]).ModelResponse(None, [
        __import__("bot.agent.client", fromlist=["ToolCall"]).ToolCall("update_event", {"file": path, "travel_minutes": 25}),
        __import__("bot.agent.client", fromlist=["ToolCall"]).ToolCall("reply", {"text": "Changed."}),
    ]))
    outs = await r.on_text("25 min to get there")
    assert "editing the entry " + path in r.agent.client.calls[-1]["messages"][-1]["content"]
    assert store.series()[entry_index].travel_minutes == 25 and "<b>Tuesday</b>" in outs[1].text


async def test_hard_exchanges_are_part_of_the_conversation(tmp_path):
    cloud = FakeModelClient([ModelResponse("Only gap: the Cedar Street commute.", [])], model="deepseek")
    r, store, primary = _rig_with_hard(tmp_path, None)
    r.agent.cloud = cloud
    await r.command("hard", "here is my schedule ...", channel=DM)
    assert r.state.recent[-2][:2] == ["user", "/hard here is my schedule ..."]
    assert r.state.recent[-1][0] == "assistant" and "Cedar Street" in r.state.recent[-1][1]
    sent = "\n".join(m["content"] for m in cloud.calls[0]["messages"])
    assert "cannot add todos, events or notes" in sent
