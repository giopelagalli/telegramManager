from datetime import datetime, date
from zoneinfo import ZoneInfo
import pytest
from bot.agent.client import FakeModelClient, ModelResponse, ToolCall
from bot.agent.agent import Agent, apply_actions
from bot.knowledge.store import KnowledgeStore
from bot.knowledge.models import Todo

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
    assert not res.parsed and "inbox" in res.reply
    assert len(client.calls) == 2 and "Tool call errors" in client.calls[1]["messages"][-1]["content"]
    assert list((store.root / "inbox").glob("*.md"))
    assert store.undo() == "inbox: saved unparsed message"

async def test_capture_model_offline_goes_to_inbox(store):
    class Boom:
        async def chat(self, *a, **k): raise ConnectionError("down")
    res = await Agent(Boom(), None, store, lambda: NOW).capture("hi")
    assert not res.parsed and "offline" in res.reply
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
