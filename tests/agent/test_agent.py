from datetime import datetime, date
from zoneinfo import ZoneInfo
import pytest
from bot.agent.client import FakeModelClient, ModelResponse, ToolCall
from bot.agent.agent import Agent, apply_actions
from bot.knowledge.store import KnowledgeStore
from bot.knowledge.models import Course, Source, Todo

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 3, 14, 0, tzinfo=NY)

@pytest.fixture
def store(tmp_path):
    s = KnowledgeStore(tmp_path / "k", clock=lambda: NOW); s.init(); return s

def R(*calls, text=None):
    return ModelResponse(text=text, tool_calls=[ToolCall(n, a) for n, a in calls])

async def test_capture_happy_path_applies_and_commits(store):
    client = FakeModelClient([R(("add_todo", {"title": "Call dentist", "priority": 1, "due": "2026-09-05"}),
                                 ("reply", {"text": "Got it."}))])
    agent = Agent(client, None, store, lambda: NOW)
    res = await agent.capture("call dentist by friday")
    assert res.parsed and res.reply == "Got it."
    applied = apply_actions(store, res.actions, NOW)
    assert applied.summary == ["Added todo: Call dentist (P1, due Sat Sep 5)"]
    assert store.todos()[0].title == "Call dentist"
    assert "capture" in __import__("subprocess").run(["git","log","-1","--format=%s"], cwd=store.root, capture_output=True, text=True).stdout

async def test_capture_retries_then_inbox(store):
    client = FakeModelClient([R(("add_todo", {"priority": 1})), R(("add_todo", {"priority": 1}))])
    agent = Agent(client, None, store, lambda: NOW)
    res = await agent.capture("???")
    assert not res.parsed and "Say it again" in res.reply
    assert len(client.calls) == 2 and "Tool call errors" in client.calls[1]["messages"][-1]["content"]
    assert list((store.root / "inbox").glob("*.md"))
    assert store.undo() == "inbox: saved unparsed message"

async def test_capture_model_offline_goes_to_inbox(store):
    class Boom:
        async def chat(self, *a, **k): raise ConnectionError("down")
    res = await Agent(Boom(), None, store, lambda: NOW).capture("hi")
    assert not res.parsed and "Model's down" in res.reply
    assert store.undo() == "inbox: saved unparsed message"

async def test_compose_fallback_on_failure(store):
    class Boom:
        async def chat(self, *a, **k): raise ConnectionError("down")
    out = await Agent(Boom(), None, store, lambda: NOW).compose("checkin", "ctx", "fallback text")
    assert out == "fallback text"

async def test_compose_truncates_at_last_sentence_end(store):
    long_text = "Short intro." + "x" * 577 + "!" + "y" * 100
    client = FakeModelClient([R(text=long_text)])
    out = await Agent(client, None, store, lambda: NOW).compose("briefing", "ctx", "fallback")
    assert out.endswith("!") and len(out) > 500

def test_apply_done_with_verify_sets_unconfirmed(store):
    p = store.add(Todo(path="", title="Wash car", verify="photo")); store.commit("x")
    applied = apply_actions(store, [ToolCall("update_todo", {"file": p, "status": "done"})], NOW)
    t = store.get_todo(p)
    assert t.status == "done" and t.confirmed is False and t.done_at == NOW
    assert applied.summary == ["Marked done: Wash car"]

def test_apply_set_profile_coerces_and_snooze(store):
    applied = apply_actions(store, [ToolCall("set_profile", {"field": "checkin_interval_minutes", "value": "90"}),
                                    ToolCall("set_profile", {"field": "morning_briefing", "value": "09:00"}),
                                    ToolCall("snooze", {"minutes": 120})], NOW)
    assert store.profile().checkin_interval_minutes == 90
    assert applied.snooze_minutes == 120 and applied.changed_schedule


def test_apply_actions_reports_bad_action_and_applies_the_rest(store):
    applied = apply_actions(store, [
        ToolCall("update_todo", {"file": "todos/nope.md", "status": "done"}),
        ToolCall("add_todo", {"title": "Buy milk", "priority": 3}),
    ], NOW)
    assert applied.summary[0].startswith("Couldn't apply update_todo:")
    assert applied.summary[1] == "Added todo: Buy milk (P3)"
    assert [t.title for t in store.todos()] == ["Buy milk"]
    subject = __import__("subprocess").run(["git", "log", "-1", "--format=%s"], cwd=store.root,
                                           capture_output=True, text=True).stdout
    assert subject.strip() == "capture: Added todo: Buy milk (P3)"


def test_apply_actions_bad_profile_value_leaves_profile_alone(store):
    before = store.profile().checkin_interval_minutes
    applied = apply_actions(store, [
        ToolCall("set_profile", {"field": "checkin_interval_minutes", "value": "soon"}),
    ], NOW)
    assert applied.summary == ["Couldn't apply set_profile: invalid literal for int() with base 10: 'soon'"]
    assert store.profile().checkin_interval_minutes == before


def test_move_todo_remaps_later_actions_in_the_same_batch(store):
    path = store.add(Todo(path="", title="Later", priority=3))
    store.commit("t")
    applied = apply_actions(store, [
        ToolCall("move_todo", {"file": path, "to": "backlog"}),
        ToolCall("update_todo", {"file": path, "priority": 1}),
    ], NOW)
    assert applied.summary == ["Moved to backlog: Later", "Updated todo: Later"]
    backlog = store.todos(include_backlog=True)
    assert len(backlog) == 1 and backlog[0].priority == 1 and backlog[0].path.startswith("backlog/")


class FakeVision:
    def __init__(self, text=None, boom=False):
        self.text = text
        self.boom = boom
        self.calls = []

    async def chat(self, messages, tools=None, temperature=0.2):
        if self.boom:
            raise ConnectionError("down")
        self.calls.append(messages)
        return ModelResponse(text=self.text, tool_calls=[])


DESCRIBED = '{"title": "Lecture 7", "kind": "slides", "topics": ["pointers"], "summary": "Pointers."}'


async def test_describe_source_parses_json_out_of_prose(store):
    client = FakeModelClient([ModelResponse("Sure:\n" + DESCRIBED + "\nHope that helps.", [])])
    described = await Agent(client, None, store, lambda: NOW).describe_source("x" * 20000, "l7.pptx", "Intro to CS")
    assert described == {"title": "Lecture 7", "kind": "slides", "topics": ["pointers"], "summary": "Pointers."}
    assert len(client.calls[0]["messages"][1]["content"]) < 13000  # only the first 12k characters


async def test_describe_source_retries_once_then_falls_back(store):
    bad = ModelResponse('{"title": "X", "kind": "lecture", "topics": [], "summary": ""}', [])
    client = FakeModelClient([bad, ModelResponse("no json here", [])])
    described = await Agent(client, None, store, lambda: NOW).describe_source("text", "ch4.pdf", "Intro to CS")
    assert len(client.calls) == 2
    assert described == {"title": "ch4", "kind": "chapter", "topics": [], "summary": ""}


async def test_describe_source_caps_topics_and_survives_the_model_being_down(store):
    many = '{"title": "T", "kind": "notes", "topics": %s, "summary": "s"}' % str([f"t{i}" for i in range(15)]).replace("'", '"')
    client = FakeModelClient([ModelResponse(many, [])])
    described = await Agent(client, None, store, lambda: NOW).describe_source("text", "n.txt", "Intro to CS")
    assert len(described["topics"]) == 10

    class Boom:
        async def chat(self, *a, **k): raise ConnectionError("down")

    assert (await Agent(Boom(), None, store, lambda: NOW).describe_source("t", "n.txt", "C"))["title"] == "n"


async def test_ocr_needs_a_vision_model(store):
    assert await Agent(FakeModelClient([]), None, store, lambda: NOW).ocr(b"img") is None

    vision = FakeVision(" board text ")
    assert await Agent(FakeModelClient([]), vision, store, lambda: NOW).ocr(b"img") == "board text"
    assert "Transcribe all text" in vision.calls[0][0]["content"][0]["text"]

    assert await Agent(FakeModelClient([]), FakeVision(boom=True), store, lambda: NOW).ocr(b"img") is None
    assert await Agent(FakeModelClient([]), FakeVision(""), store, lambda: NOW).ocr(b"img") is None


async def test_tutor_grounds_the_answer_in_the_selected_sources(store):
    client = FakeModelClient([ModelResponse("A pointer holds an address [Lecture 7, p.3].", [])])
    course = Course(path="courses/cs101.md", title="Intro to CS")
    source = Source(path="sources/cs101/a.md", title="Lecture 7", course="cs101", kind="slides",
                    body="## p.3\nPointers hold addresses")
    answer, note = await Agent(client, None, store, lambda: NOW).tutor("what is a pointer?", course, [source])
    assert answer == "A pointer holds an address [Lecture 7, p.3]." and note is None
    call = client.calls[0]
    assert call["temperature"] == 0.3
    assert [t["function"]["name"] for t in call["tools"]] == ["save_note"]
    assert "tutor for Intro to CS" in call["messages"][0]["content"]
    assert "### Lecture 7 (slides)\n## p.3\nPointers hold addresses" in call["messages"][1]["content"]


async def test_tutor_returns_the_save_note_call_and_survives_a_dead_model(store):
    client = FakeModelClient([R(("save_note", {"text": " Big-O of quicksort is n log n ", "topics": ["sorting", 7]}))])
    course = Course(path="courses/cs101.md", title="Intro to CS")
    answer, note = await Agent(client, None, store, lambda: NOW).tutor("quicksort is n log n", course, [])
    assert answer is None
    assert note == {"text": "Big-O of quicksort is n log n", "topics": ["sorting"]}

    class Boom:
        async def chat(self, *a, **k): raise ConnectionError("down")

    assert await Agent(Boom(), None, store, lambda: NOW).tutor("q", course, []) == (None, None)


async def test_tutor_without_the_notes_tool(store):
    client = FakeModelClient([ModelResponse("Answer.", [])])
    course = Course(path="courses/cs101.md", title="Intro to CS")
    await Agent(client, None, store, lambda: NOW).tutor("q", course, [], notes_tool=False)
    assert client.calls[0]["tools"] is None


async def test_capture_keeps_a_prose_answer_instead_of_sending_it_to_the_inbox(store):
    client = FakeModelClient([ModelResponse(text="Because it's due tonight.", tool_calls=[])])
    agent = Agent(client, None, store, lambda: NOW)
    res = await agent.capture("Why")
    assert res.parsed and res.reply == "Because it's due tonight."
    assert not list((store.root / "inbox").glob("*.md"))
    assert len(client.calls) == 1


async def test_capture_keeps_prose_alongside_tool_calls_without_a_reply_call(store):
    client = FakeModelClient([R(("add_todo", {"title": "Call dentist", "priority": 2}), text="Added.")])
    agent = Agent(client, None, store, lambda: NOW)
    res = await agent.capture("call the dentist")
    assert res.parsed and res.reply == "Added." and res.actions[0].name == "add_todo"
