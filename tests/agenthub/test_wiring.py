"""The project tools through the router, /projects and its buttons, the engine's poll, the
briefing roll-up, and the settings — against the fake hub."""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from bot.agent.agent import Agent
from bot.agent.client import FakeModelClient, ModelResponse, ToolCall
from bot.agent.prompts import build_context
from bot.agenthub.projects import Projects
from bot.cluster import AgentHubStatus
from bot.config import Settings
from bot.knowledge.store import KnowledgeStore
from bot.scheduler import briefings
from bot.scheduler.clock import FakeClock
from bot.scheduler.engine import Engine
from bot.scheduler.state import RuntimeState
from bot.telegram.router import Router

from .conftest import TOKEN

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 3, 14, 0, tzinfo=NY)
NOW_MS = int(NOW.timestamp() * 1000)


def R(*calls):
    return ModelResponse(None, [ToolCall(n, a) for n, a in calls])


class Sink:
    def __init__(self):
        self.sent = []

    async def send(self, out):
        self.sent.append(out)


@pytest.fixture
def jd(tmp_path, hub, client):
    clock = FakeClock(NOW)
    store = KnowledgeStore(tmp_path / "k", clock=clock.now)
    store.init()
    model = FakeModelClient([])
    projects = Projects(client)
    agent = Agent(model, None, store, clock.now, projects=True)
    state = RuntimeState.load(tmp_path / "s.json")
    router = Router(store, agent, state, clock, None, projects=projects)
    yield router, model, projects, state, store, clock
    build_context.projects = None


def test_tools_only_when_agenthub_is_configured(tmp_path):
    store = KnowledgeStore(tmp_path / "k", clock=datetime.now)
    names = lambda agent: {t["function"]["name"] for t in agent.tools}
    assert "project_turn" not in names(Agent(FakeModelClient([]), None, store, datetime.now))
    assert {"project_new", "project_turn", "project_pause", "project_resume", "project_priority"} <= \
        names(Agent(FakeModelClient([]), None, store, datetime.now, projects=True))


async def test_run_a_turn_from_a_message(jd, hub):
    router, model, projects, state, _, _ = jd
    hub.add("rosenroot", "Rosenroot", summary="Ingest half done.")
    await projects.refresh()
    model.responses.append(R(("project_turn", {"slug": "rosenroot", "instruction": "focus on the ingest"}),
                             ("reply", {"text": "On it."})))
    outs = await router.on_text("run a turn on rosenroot, focus on the ingest")
    assert outs[0].text == "On it.\nTurn on Rosenroot started. I'll message you when it lands."
    assert ("POST", "/api/projects/rosenroot/turn", {"instruction": "focus on the ingest"}) in hub.calls
    assert state.projects["watch"] == {"rosenroot": {"since": NOW_MS, "last": NOW_MS, "pending": 1}}
    system, context = (m["content"] for m in model.calls[0]["messages"][:2])
    assert "project_new" in system and 'rosenroot "Rosenroot" [active, normal]: Ingest half done.' in context


async def test_start_pause_and_prioritize_from_messages(jd, hub):
    router, model, projects, _, _, _ = jd
    model.responses.append(R(("project_new", {"title": "Tide clock", "intent": "a tide clock for the harbour"}),
                             ("reply", {"text": "Starting it."})))
    outs = await router.on_text("start a project: a tide clock for the harbour")
    assert "Started Tide clock on AgentHub (tide-clock)" in outs[0].text
    await asyncio.gather(*projects._tasks)
    model.responses.append(R(("project_pause", {"slug": "tide-clock"}), ("project_priority", {"slug": "tide-clock", "order": "idle"}),
                             ("reply", {"text": "Done."})))
    outs = await router.on_text("pause the tide clock and run it only when idle")
    assert outs[0].text == "Done.\nPaused Tide clock.\nTide clock when idle now."


async def test_projects_command_and_buttons_edit_in_place(jd, hub):
    router, _, _, state, _, _ = jd
    hub.add("rosenroot", "Rosenroot", summary="Built the ingest worker.", next_steps=["the stream view"],
            progress={"done": 3, "total": 8})
    [listing] = await router.command("projects", "")
    assert "<b>Rosenroot</b> — active · normal · 3/8" in listing.text
    assert listing.buttons == [("Rosenroot", "proj:view:rosenroot")] and listing.kind == "reply"

    [view] = await router.on_callback("proj:view:rosenroot", message_id=7)
    assert view.kind == "edit" and view.edit_message_id == 7
    assert "Built the ingest worker." in view.text and "the stream view" in view.text
    assert [b[1] for b in view.buttons] == ["proj:turn:rosenroot", "proj:pause:rosenroot", "proj:prio:rosenroot", "proj:list"]

    [paused] = await router.on_callback("proj:pause:rosenroot", message_id=7)
    assert paused.text.startswith("<i>Paused Rosenroot.</i>") and ("▶ Resume", "proj:resume:rosenroot") in paused.buttons

    [chooser] = await router.on_callback("proj:prio:rosenroot", message_id=7)
    assert ("✓ Normal", "proj:setprio:rosenroot:normal") in chooser.buttons
    [set_] = await router.on_callback("proj:setprio:rosenroot:first", message_id=7)
    assert "Rosenroot runs first now." in set_.text and hub.projects["rosenroot"]["priority"] == "interactive"

    [turned] = await router.on_callback("proj:turn:rosenroot", message_id=7)
    assert "Turn on Rosenroot started." in turned.text and "rosenroot" in state.projects["watch"]
    assert all(len(b[1].encode()) <= 64 for b in view.buttons + chooser.buttons)


async def test_projects_without_agenthub(tmp_path):
    clock = FakeClock(NOW)
    store = KnowledgeStore(tmp_path / "k", clock=clock.now)
    store.init()
    router = Router(store, Agent(FakeModelClient([]), None, store, clock.now), RuntimeState(), clock, None)
    [out] = await router.command("projects", "")
    assert "isn't set up" in out.text
    assert "isn't set up" in (await router.on_callback("proj:list"))[0].text


async def test_engine_sends_reports_and_budgeted_alerts(jd, hub, tmp_path):
    router, _, projects, state, store, clock = jd
    hub.add("rosenroot", "Rosenroot")
    hub.add("prob", "Probability engine")
    sink = Sink()
    engine = Engine(store, router.agent, state, tmp_path / "s.json", clock, sink, None, projects=projects)
    await engine._projects(clock.now(), [])  # first sight
    await projects.turn(state, "rosenroot", None, clock.now())
    hub.land("rosenroot", "Built it.", ended_at=NOW_MS + 1)
    hub.add("prob", "Probability engine", status="blocked")
    clock.advance(seconds=61)
    await engine._projects(clock.now(), [])
    assert [o.text for o in sink.sent] == ["<b>Rosenroot</b> turn done: Built it.", "<b>Probability engine</b> is blocked."]
    assert len(state.proactive_sends) == 1  # the alert spent the budget; the report did not
    clock.advance(seconds=10)
    await engine._projects(clock.now(), [])  # within the poll interval: nothing new
    assert len(sink.sent) == 2


async def test_engine_holds_alerts_while_snoozed(jd, hub, tmp_path):
    router, _, projects, state, store, clock = jd
    hub.add("prob", "Probability engine")
    sink = Sink()
    engine = Engine(store, router.agent, state, tmp_path / "s.json", clock, sink, None, projects=projects)
    await engine._projects(clock.now(), [])
    hub.add("prob", "Probability engine", status="blocked")
    state.pause_until = clock.now().replace(hour=16)
    clock.advance(seconds=61)
    await engine._projects(clock.now(), [])
    assert sink.sent == []


async def test_briefings_carry_the_projects_line(jd, hub):
    _, _, projects, _, store, _ = jd
    hub.add("rosenroot")
    hub.land("rosenroot", "auto", requested_by=None, ended_at=NOW_MS)
    briefings.PROJECTS = projects
    try:
        morning = await briefings.morning_outbound(store, NOW, None)
        evening = await briefings.evening_outbound(store, NOW.replace(hour=21), None)
    finally:
        briefings.PROJECTS = None
    assert morning.text.endswith("Projects: rosenroot 1 turn.")
    assert evening.text.endswith("Projects: rosenroot 1 turn.")


def test_settings_read_the_token_and_label():
    base = {"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_USER_ID": "1", "DATA_DIR": "/tmp/d", "KNOWLEDGE_DIR": "/tmp/k",
            "KOKORO_MODEL_DIR": "/tmp/m", "OPENAI_BASE_URL": "http://localhost:8888/v1", "OPENAI_API_KEY": "x",
            "CHAT_MODEL": "m"}
    s = Settings.from_env({**base, "AGENTHUB_URL": "http://hub:4000", "AGENTHUB_TOKEN": "ah_fake"})
    assert (s.agenthub_token, s.agenthub_label) == ("ah_fake", "JD")
    assert Settings.from_env({**base, "AGENTHUB_LABEL": "Jarvis"}).agenthub_label == "Jarvis"
    assert Settings.from_env(base).agenthub_token is None


async def test_queue_prefers_the_token_over_the_password():
    seen = []

    def handler(req):
        seen.append((req.url.path, req.headers.get("authorization")))
        return httpx.Response(200, json={"projects": [], "jobs": [], "streams": {}})
    status = AgentHubStatus("http://hub", "pw", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)), token=TOKEN)
    assert await status.snapshot() is not None
    assert seen == [("/api/state", f"Bearer {TOKEN}")]
