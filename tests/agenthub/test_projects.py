import asyncio
from datetime import datetime, timedelta, timezone

from bot.agenthub.projects import Projects
from bot.scheduler.state import RuntimeState

NOW = datetime(2033, 5, 18, 12, 0, tzinfo=timezone.utc)
NOW_MS = int(NOW.timestamp() * 1000)


def rig(client, label="JD"):
    return Projects(client, label), RuntimeState()


async def test_context_block_is_bounded_and_says_what_matters(hub, client):
    hub.add("rosenroot", "Rosenroot", summary="Built the ingest worker. " * 30, progress={"done": 3, "total": 8},
            priority="interactive")
    hub.add("probability-engine", "Probability engine", status="blocked", blockers=["the odds API key"], priority="batch")
    projects, _ = rig(client)
    assert projects.context_block() is None  # nothing read yet
    await projects.refresh()
    block = projects.context_block()
    assert 'rosenroot "Rosenroot" [active, runs first, 3/8 milestones]: Built the ingest worker.' in block
    assert 'probability-engine "Probability engine" [blocked, when idle] Blocked on: the odds API key' in block
    assert len(block.splitlines()[1]) < 260  # one clipped line per project
    for i in range(30):
        hub.add(f"p{i}")
    await projects.refresh()
    block = projects.context_block()
    assert "- and 20 more" in block and len(block) <= 2500


async def test_find_by_slug_title_or_loose_words(hub, client):
    hub.add("rosenroot", "Rosenroot")
    hub.add("probability-engine", "Probability engine")
    projects, _ = rig(client)
    await projects.refresh()
    assert projects.find("rosenroot")["slug"] == "rosenroot"
    assert projects.find("the probability engine")["slug"] == "probability-engine"
    assert projects.find("Probability")["slug"] == "probability-engine"
    assert projects.find("tide clock") is None


async def test_project_new_creates_then_plans_in_the_background(hub, client):
    projects, _ = rig(client)
    line = await projects.new("Tide clock", "a tide clock for the harbour")
    assert "Started Tide clock on AgentHub (tide-clock)" in line
    assert hub.calls[0][2] == {"slug": "tide-clock", "title": "Tide clock", "intent": "a tide clock for the harbour",
                               "idea": "a tide clock for the harbour"}
    await asyncio.gather(*projects._tasks)
    [done] = projects.drain()
    assert done.text == ("<b>Tide clock</b> is planned: PRD “Tide Clock — PRD”, 3 milestones. "
                         "Say “run a turn on tide-clock” to start.")
    assert projects.drain() == []


async def test_project_new_reports_where_planning_failed(hub, client):
    projects, _ = rig(client)
    hub.add("tide-clock")
    assert "Couldn't start Tide clock: AgentHub: project already exists" == await projects.new("Tide clock", "x")
    del hub.projects["tide-clock"]
    hub.fail["POST /api/projects/tide-clock/roadmap/generate"] = (504, {"error": "roadmap run took longer than 10 minutes"})
    await projects.new("Tide clock", "x")
    await asyncio.gather(*projects._tasks)
    [failed] = projects.drain()
    assert "the roadmap failed. AgentHub: roadmap run took longer than 10 minutes" in failed.text


async def test_turn_pause_resume_priority(hub, client):
    hub.add("rosenroot", "Rosenroot")
    projects, state = rig(client)
    assert await projects.turn(state, "rosenroot", "focus on the ingest", NOW) == \
        "Turn on Rosenroot started. I'll message you when it lands."
    assert state.projects["watch"] == {"rosenroot": NOW_MS}
    assert await projects.pause("Rosenroot") == "Paused Rosenroot."
    assert projects.snapshot[0]["status"] == "paused"
    assert await projects.resume("rosenroot") == "Resumed Rosenroot."
    assert await projects.priority("rosenroot", "idle") == "Rosenroot when idle now."
    assert hub.projects["rosenroot"]["priority"] == "batch"
    assert await projects.priority("rosenroot", "first") == "Rosenroot runs first now."
    assert await projects.turn(state, "nothing-here", None, NOW) == "No project called nothing-here."


async def test_a_refused_turn_says_why_and_is_not_watched(hub, client):
    hub.add("rosenroot", "Rosenroot")
    hub.fail["POST /api/projects/rosenroot/turn"] = (409, {"error": "daily turn cap reached"})
    projects, state = rig(client)
    assert await projects.turn(state, "rosenroot", None, NOW) == "Rosenroot: AgentHub: daily turn cap reached"
    assert state.projects.get("watch", {}) == {}


async def test_a_turn_jd_started_is_reported_exactly_once(hub, client):
    hub.add("rosenroot", "Rosenroot", next_steps=["the stream view"])
    projects, state = rig(client)
    await projects.turn(state, "rosenroot", None, NOW)
    reports, _ = await projects.poll(state, NOW + timedelta(minutes=1), 3)
    assert reports == []  # still running
    hub.land("rosenroot", "Owner's turn.", requested_by=None, ended_at=NOW_MS + 30_000)
    hub.land("rosenroot", "Built the ingest worker, tests green.", ended_at=NOW_MS + 60_000)
    reports, _ = await projects.poll(state, NOW + timedelta(minutes=2), 3)
    assert [r.text for r in reports] == [
        "<b>Rosenroot</b> turn done: Built the ingest worker, tests green. Next: the stream view"]
    assert state.projects["watch"] == {}
    # Watched again (a second turn fired): the first one is not reported twice.
    state.projects["watch"]["rosenroot"] = NOW_MS
    reports, _ = await projects.poll(state, NOW + timedelta(minutes=3), 3)
    assert reports == []


async def test_reported_survives_a_restart(hub, client, tmp_path):
    hub.add("rosenroot")
    projects, state = rig(client)
    await projects.turn(state, "rosenroot", None, NOW)
    hub.land("rosenroot", "Done.", ended_at=NOW_MS + 1)
    assert len((await projects.poll(state, NOW, 3))[0]) == 1
    state.projects["watch"]["rosenroot"] = NOW_MS
    state.save(tmp_path / "s.json")
    again = RuntimeState.load(tmp_path / "s.json")
    assert (await Projects(client).poll(again, NOW, 3))[0] == []


async def test_a_watch_that_never_lands_expires(hub, client):
    hub.add("rosenroot")
    projects, state = rig(client)
    await projects.turn(state, "rosenroot", None, NOW)
    await projects.poll(state, NOW + timedelta(hours=4), 3)
    assert state.projects["watch"] == {}


async def test_blocked_and_errored_alert_once_within_the_budget(hub, client):
    hub.add("rosenroot", "Rosenroot")
    hub.add("prob", "Probability engine")
    projects, state = rig(client)
    assert await projects.poll(state, NOW, 3) == ([], [])  # first sight: recorded, not alerted
    hub.add("prob", "Probability engine", status="blocked", blockers=["the odds API key"])
    hub.add("rosenroot", "Rosenroot", last_turn={"outcome": "error", "endedAt": NOW_MS})
    _, alerts = await projects.poll(state, NOW, 0)
    assert alerts == []  # no budget: held for a later poll
    _, alerts = await projects.poll(state, NOW, 3)
    assert sorted(a.text for a in alerts) == ["<b>Probability engine</b> is blocked: the odds API key",
                                              "<b>Rosenroot</b>: a turn failed."]
    assert (await projects.poll(state, NOW, 3))[1] == []
    hub.add("prob", "Probability engine")  # unblocked, then blocked again: alerts again
    await projects.poll(state, NOW, 3)
    hub.add("prob", "Probability engine", status="blocked")
    assert [a.text for a in (await projects.poll(state, NOW, 3))[1]] == ["<b>Probability engine</b> is blocked."]


async def test_an_errored_turn_jd_reported_is_not_alerted_again(hub, client):
    hub.add("rosenroot", "Rosenroot")
    projects, state = rig(client)
    await projects.poll(state, NOW, 3)
    await projects.turn(state, "rosenroot", None, NOW)
    hub.land("rosenroot", "Gateway error.", outcome="error", ended_at=NOW_MS + 5)
    hub.add("rosenroot", "Rosenroot", last_turn={"outcome": "error", "endedAt": NOW_MS + 5})
    reports, alerts = await projects.poll(state, NOW, 3)
    assert [r.text for r in reports] == ["<b>Rosenroot</b> turn failed: Gateway error."]
    assert alerts == []


async def test_briefing_line_rolls_up_turns_jd_did_not_start(hub, client):
    hub.add("rosenroot")
    hub.add("probability-engine", status="blocked", blockers=["the odds API key"])
    hub.add("quiet")
    hub.land("rosenroot", "auto 1", requested_by=None, ended_at=NOW_MS + 1)
    hub.land("rosenroot", "auto 2", requested_by=None, ended_at=NOW_MS + 2)
    hub.land("rosenroot", "mine", ended_at=NOW_MS + 3)
    hub.land("rosenroot", "old", requested_by=None, ended_at=NOW_MS - 1)
    projects, _ = rig(client)
    assert await projects.briefing_line(NOW) == \
        "Projects: rosenroot 2 turns, probability-engine blocked on the odds API key."
    hub.projects.clear()
    assert await projects.briefing_line(NOW) is None


async def test_unreachable_hub_keeps_the_last_snapshot(hub, client):
    hub.add("rosenroot")
    projects, state = rig(client)
    await projects.refresh()
    hub.hang.add("GET /api/state")
    assert await projects.refresh() is None
    assert projects.snapshot[0]["slug"] == "rosenroot"
    assert await projects.briefing_line(NOW) is None
    assert await projects.poll(state, NOW, 3) == ([], [])


async def test_a_turn_that_never_reached_the_hub_is_an_error_not_a_watch(hub):
    import httpx
    from bot.agenthub.client import AgentHubClient
    from .conftest import TOKEN

    def handler(req):
        if req.method == "POST":
            raise httpx.ConnectTimeout("connect hung", request=req)
        return hub(req)
    hub.add("rosenroot", "Rosenroot")
    projects = Projects(AgentHubClient("http://hub.test", TOKEN, http=httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    state = RuntimeState()
    assert await projects.turn(state, "rosenroot", None, NOW) == "Rosenroot: AgentHub didn't answer in time."
    assert state.projects.get("watch", {}) == {}
