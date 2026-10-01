import logging

import httpx
import pytest

from bot.agenthub.client import PLAN_TIMEOUT, TURN_FIRE_TIMEOUT, AgentHubClient, AgentHubError

from .conftest import TOKEN


async def test_every_route_with_the_bearer_token(hub, client):
    hub.add("rosenroot", "Rosenroot")
    assert (await client.state())["projects"][0]["slug"] == "rosenroot"
    assert (await client.briefings())[0]["slug"] == "rosenroot"
    assert (await client.turns("rosenroot", since=123))["turns"] == []
    assert (await client.create("tide-clock", "Tide clock", "a tide clock", idea="a tide clock"))["slug"] == "tide-clock"
    assert (await client.draft_prd("tide-clock"))["full"].startswith("# Tide Clock")
    assert len((await client.generate_roadmap("tide-clock"))["milestones"]) == 3
    assert (await client.start_turn("rosenroot", "focus on the ingest"))["slug"] == "rosenroot"
    assert (await client.pause("rosenroot"))["status"] == "paused"
    assert (await client.resume("rosenroot"))["status"] == "active"
    assert (await client.set_priority("rosenroot", "batch"))["priority"] == "batch"
    assert [c[:2] for c in hub.calls] == [
        ("GET", "/api/state"), ("GET", "/api/briefings"),
        ("GET", "/api/projects/rosenroot/turns?since=123"), ("POST", "/api/projects"),
        ("POST", "/api/projects/tide-clock/prd/draft?wait=1"), ("POST", "/api/projects/tide-clock/roadmap/generate?wait=1"),
        ("POST", "/api/projects/rosenroot/turn"), ("POST", "/api/projects/rosenroot/pause"),
        ("POST", "/api/projects/rosenroot/resume"), ("POST", "/api/projects/rosenroot/priority"),
    ]
    assert hub.calls[3][2] == {"slug": "tide-clock", "title": "Tide clock", "intent": "a tide clock", "idea": "a tide clock"}
    assert hub.calls[6][2] == {"instruction": "focus on the ingest"}
    assert hub.calls[9][2] == {"priority": "batch"}


async def test_timeouts_short_for_the_turn_long_for_plans(hub, client):
    hub.add("rosenroot")
    await client.state()
    await client.start_turn("rosenroot")
    await client.draft_prd("rosenroot")
    await client.generate_roadmap("rosenroot")
    assert hub.timeouts["GET /api/state"]["read"] == 8.0
    assert hub.timeouts["POST /api/projects/rosenroot/turn"]["read"] == TURN_FIRE_TIMEOUT == 10.0
    assert hub.timeouts["POST /api/projects/rosenroot/prd/draft"]["read"] == PLAN_TIMEOUT == 660.0
    assert hub.timeouts["POST /api/projects/rosenroot/roadmap/generate"]["read"] == PLAN_TIMEOUT
    assert hub.calls[1][2] == {}  # no instruction → empty body


async def test_a_turn_that_outlasts_the_fire_timeout_is_still_running(hub, client):
    hub.add("rosenroot")
    hub.hang.add("POST /api/projects/rosenroot/turn")
    assert await client.start_turn("rosenroot") is None


async def test_other_timeouts_and_unreachable_are_errors(hub, client):
    hub.hang.add("GET /api/state")
    with pytest.raises(AgentHubError, match="didn't answer in time"):
        await client.state()

    def refuse(req):
        raise httpx.ConnectError("refused", request=req)
    down = AgentHubClient("http://hub.test", TOKEN, http=httpx.AsyncClient(transport=httpx.MockTransport(refuse)))
    with pytest.raises(AgentHubError, match="isn't reachable"):
        await down.briefings()


@pytest.mark.parametrize("status,body,message", [
    (401, {"error": "invalid api token"}, "AgentHub token rejected."),
    (403, {"error": "a agent token cannot drive projects"}, "not allowed for the assistant"),
    (409, {"error": "daily turn cap reached (6/6)"}, "AgentHub: daily turn cap reached (6/6)"),
    (429, {"error": "too many bad tokens; try again later"}, "locked out"),
    (404, {"error": "project not found"}, "no such project"),
    (502, {}, "AgentHub answered 502."),
])
async def test_error_mapping(hub, client, status, body, message):
    hub.add("rosenroot")
    hub.fail["POST /api/projects/rosenroot/turn"] = (status, body)
    with pytest.raises(AgentHubError, match=message.replace("(", r"\(").replace(")", r"\)")) as err:
        await client.start_turn("rosenroot")
    assert err.value.status == status


async def test_a_wrong_token_is_rejected_and_never_logged(hub, caplog):
    bad = AgentHubClient("http://hub.test", "ah_wrong_secret", http=httpx.AsyncClient(transport=httpx.MockTransport(hub)))
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(AgentHubError, match="token rejected"):
            await bad.state()
    assert "ah_wrong_secret" not in caplog.text and "/api/state" in caplog.text
