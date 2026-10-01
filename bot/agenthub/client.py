"""AgentHub's /api as JD's `assistant` token may call it (AgentHub decision 0065): read the
projects, create one, draft its PRD and roadmap, run, pause, resume and re-prioritize turns.
Every failure is an `AgentHubError` whose message can go to the owner as it is."""
from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

READ_TIMEOUT = 8.0
# A turn runs for many minutes and keeps running when we hang up (0065): fire, then poll /turns.
TURN_FIRE_TIMEOUT = 10.0
# `?wait=1` plan runs: the hub gives up itself after 10 minutes (504); wait a little longer.
PLAN_TIMEOUT = 11 * 60.0


class AgentHubError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class AgentHubClient:
    def __init__(self, url: str, token: str, http: httpx.AsyncClient | None = None):
        self._url = url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}  # never logged: errors name the route only
        self._http = http or httpx.AsyncClient()

    # -- reads -----------------------------------------------------------

    async def state(self) -> dict:
        return await self._call("GET", "/api/state")

    async def briefings(self) -> list[dict]:
        return await self._call("GET", "/api/briefings")

    async def projects(self) -> list[dict]:
        return await self._call("GET", "/api/projects")

    async def turns(self, slug: str, since: int | None = None) -> dict:
        query = "" if since is None else f"?since={int(since)}"
        return await self._call("GET", f"/api/projects/{_slug(slug)}/turns{query}")

    # -- writes ----------------------------------------------------------

    async def create(self, slug: str, title: str, intent: str, idea: str | None = None) -> dict:
        body = {"slug": slug, "title": title, "intent": intent, **({"idea": idea} if idea else {})}
        return await self._call("POST", "/api/projects", body)

    async def draft_prd(self, slug: str) -> dict:
        return await self._call("POST", f"/api/projects/{_slug(slug)}/prd/draft?wait=1", {}, timeout=PLAN_TIMEOUT)

    async def generate_roadmap(self, slug: str) -> dict:
        return await self._call("POST", f"/api/projects/{_slug(slug)}/roadmap/generate?wait=1", {}, timeout=PLAN_TIMEOUT)

    async def start_turn(self, slug: str, instruction: str | None = None) -> dict | None:
        """Fire a turn. The briefing when it landed within the timeout, None while it still runs."""
        body = {"instruction": instruction} if instruction else {}
        try:
            return await self._call("POST", f"/api/projects/{_slug(slug)}/turn", body,
                                    timeout=TURN_FIRE_TIMEOUT, quiet_timeout=True)
        except _StillRunning:
            return None

    async def pause(self, slug: str) -> dict:
        return await self._call("POST", f"/api/projects/{_slug(slug)}/pause", {})

    async def resume(self, slug: str) -> dict:
        return await self._call("POST", f"/api/projects/{_slug(slug)}/resume", {})

    async def set_priority(self, slug: str, priority: str) -> dict:
        return await self._call("POST", f"/api/projects/{_slug(slug)}/priority", {"priority": priority})

    # -- plumbing --------------------------------------------------------

    async def _call(self, method: str, path: str, body: dict | None = None, *,
                    timeout: float = READ_TIMEOUT, quiet_timeout: bool = False):
        route = path.split("?", 1)[0]
        try:
            r = await self._http.request(method, self._url + path, json=body, headers=self._headers, timeout=timeout)
        except httpx.TimeoutException as exc:
            # Only a read timeout means the hub took the request and is still working on it; a
            # connect/write/pool timeout means it never got there.
            if quiet_timeout and isinstance(exc, httpx.ReadTimeout):
                raise _StillRunning()
            logger.warning("agenthub %s %s timed out", method, route)
            raise AgentHubError("AgentHub didn't answer in time.")
        except httpx.HTTPError as exc:
            logger.warning("agenthub %s %s failed: %s", method, route, type(exc).__name__)
            raise AgentHubError("AgentHub isn't reachable.")
        if r.status_code >= 400:
            logger.warning("agenthub %s %s → %s", method, route, r.status_code)
            raise AgentHubError(_message(r), r.status_code)
        try:
            return r.json()
        except ValueError:
            raise AgentHubError("AgentHub sent something that isn't JSON.", r.status_code)


class _StillRunning(Exception):
    pass


def _slug(slug: str) -> str:
    return quote(slug, safe="")


def _message(r: httpx.Response) -> str:
    try:
        said = str(r.json().get("error") or "").strip()
    except (ValueError, AttributeError):
        said = ""
    if r.status_code == 401:
        return "AgentHub token rejected."
    if r.status_code == 403:
        return "That's not allowed for the assistant" + (f" ({said})." if said else ".")
    if r.status_code == 429:
        return "AgentHub locked out bad tokens for now; try later."
    if r.status_code == 404:
        return "AgentHub has no such project."
    if said:
        return f"AgentHub: {said}"
    return f"AgentHub answered {r.status_code}."
