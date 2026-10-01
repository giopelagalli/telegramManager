"""A fake AgentHub: the assistant routes over httpx.MockTransport, in memory, no network."""
import json

import httpx
import pytest

from bot.agenthub.client import AgentHubClient

TOKEN = "ah_test_not_a_real_token"


class FakeHub:
    def __init__(self):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.timeouts: dict[str, object] = {}
        self.projects: dict[str, dict] = {}
        self.briefings: dict[str, dict] = {}
        self.turns: dict[str, list[dict]] = {}
        self.fail: dict[str, tuple[int, dict]] = {}  # "METHOD /path" → (status, body)
        self.hang: set[str] = set()  # routes that time out
        self.session = 100

    def add(self, slug, title=None, status="active", priority="project", summary="", blockers=(), next_steps=(),
            last_turn=None, progress=None):
        self.projects[slug] = {"slug": slug, "title": title or slug.title(), "status": status, "priority": priority,
                               **({"lastTurn": last_turn} if last_turn else {})}
        self.briefings[slug] = {"slug": slug, "title": title or slug.title(), "status": status, "priority": priority,
                                "summary": summary, "progress": progress or {"done": 0, "total": 0},
                                "blockers": list(blockers), "nextSteps": list(next_steps), "updatedAt": 1}

    def land(self, slug, summary, requested_by="JD", outcome="stop", ended_at=2_000_000_000_000):
        self.session += 1
        self.turns.setdefault(slug, []).append({
            "sessionId": self.session, "startedAt": ended_at - 1000, "endedAt": ended_at, "outcome": outcome,
            "summary": summary, "toolCalls": 3, "cost": {"usd": 0, "tokens": 0}, "events": [],
            **({"requestedBy": requested_by} if requested_by else {}),
        })
        return self.session

    def __call__(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else None
        path = req.url.path
        self.calls.append((req.method, req.url.path + (f"?{req.url.query.decode()}" if req.url.query else ""), body))
        self.timeouts[f"{req.method} {path}"] = req.extensions.get("timeout")
        if req.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"error": "invalid api token"})
        key = f"{req.method} {path}"
        if key in self.hang:
            raise httpx.ReadTimeout("hung", request=req)
        if key in self.fail:
            status, payload = self.fail[key]
            return httpx.Response(status, json=payload)
        parts = path.strip("/").split("/")  # api, projects, slug, action...
        if key == "GET /api/state":
            return httpx.Response(200, json={"nodes": [], "jobs": [], "streams": {}, "projects": list(self.projects.values())})
        if key == "GET /api/briefings":
            return httpx.Response(200, json=list(self.briefings.values()))
        if key == "GET /api/projects":
            return httpx.Response(200, json=list(self.projects.values()))
        if key == "POST /api/projects":
            if body["slug"] in self.projects:
                return httpx.Response(409, json={"error": "project already exists"})
            self.add(body["slug"], body["title"])
            return httpx.Response(201, json=self.projects[body["slug"]])
        slug = parts[2] if len(parts) > 2 else ""
        if slug not in self.projects:
            return httpx.Response(404, json={"error": "project not found"})
        action = "/".join(parts[3:])
        if req.method == "GET" and action == "turns":
            since = req.url.params.get("since")
            turns = [t for t in self.turns.get(slug, []) if since is None or t["endedAt"] >= int(since)]
            return httpx.Response(200, json={"running": None, "turns": turns, "budget": {}})
        if action == "prd/draft":
            return httpx.Response(200, json={"done": True, "full": "# Tide Clock — PRD\n\nBody.", "questions": [], "audit": {}})
        if action == "roadmap/generate":
            return httpx.Response(200, json={"done": True, "full": "", "milestones": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]})
        if action == "turn":
            return httpx.Response(200, json=self.briefings[slug])
        if action in ("pause", "resume"):
            self.projects[slug]["status"] = "paused" if action == "pause" else "active"
            return httpx.Response(200, json=self.projects[slug])
        if action == "priority":
            self.projects[slug]["priority"] = body["priority"]
            return httpx.Response(200, json=self.projects[slug])
        return httpx.Response(404, json={"error": "not found"})


@pytest.fixture
def hub():
    return FakeHub()


@pytest.fixture
def client(hub):
    return AgentHubClient("http://hub.test:4000/", TOKEN, http=httpx.AsyncClient(transport=httpx.MockTransport(hub)))
