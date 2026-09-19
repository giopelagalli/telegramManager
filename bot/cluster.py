"""Who is using the Spark right now: vLLM's own counters, plus AgentHub's projects when the hub
is reachable. Read-only; for /queue."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

_METRIC = re.compile(r"^(vllm:[a-z_]+)(?:\{[^}]*\})?\s+([0-9.eE+-]+)\s*$", re.MULTILINE)


class SparkStatus:
    """vLLM's /metrics: requests running and waiting, and how full the KV cache is."""

    def __init__(self, base_url: str, http: httpx.AsyncClient | None = None):
        root = base_url.rstrip("/")
        if root.endswith("/v1"):
            root = root[:-3]
        self._url = f"{root}/metrics"
        self._http = http or httpx.AsyncClient(timeout=5.0)

    async def snapshot(self) -> dict | None:
        try:
            r = await self._http.get(self._url)
            r.raise_for_status()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("spark metrics failed: %s", type(exc).__name__)
            return None
        found: dict[str, float] = {}
        for name, value in _METRIC.findall(r.text):
            found[name] = found.get(name, 0.0) + float(value)  # sum over labels (one model, so one line)
        if "vllm:num_requests_running" not in found:
            return None
        kv = found.get("vllm:kv_cache_usage_perc", found.get("vllm:gpu_cache_usage_perc"))
        return {
            "running": int(found["vllm:num_requests_running"]),
            "waiting": int(found.get("vllm:num_requests_waiting", 0)),
            "kv_pct": None if kv is None else int(round(kv * 100)),
        }


class AgentHubStatus:
    """AgentHub's /api/state: projects (status, priority) and its job queue."""

    def __init__(self, url: str, password: str | None = None, http: httpx.AsyncClient | None = None):
        self._url = url.rstrip("/")
        self._password = password
        self._http = http or httpx.AsyncClient(timeout=8.0)

    async def snapshot(self) -> dict | None:
        try:
            if self._password and not self._http.cookies.get("hub_session"):
                login = await self._http.post(f"{self._url}/api/login", json={"password": self._password})
                login.raise_for_status()
            r = await self._http.get(f"{self._url}/api/state")
            r.raise_for_status()
            state = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("agenthub state failed: %s", type(exc).__name__)
            return None
        projects = [
            {
                "slug": p.get("slug", "?"),
                "title": p.get("title") or p.get("slug", "?"),
                "status": p.get("status", "?"),
                "priority": p.get("priority"),
                "updated_at": p.get("updatedAt"),
            }
            for p in state.get("projects") or []
        ]
        jobs: dict[str, int] = {}
        for j in state.get("jobs") or []:
            jobs[str(j.get("status", "?"))] = jobs.get(str(j.get("status", "?")), 0) + 1
        return {"projects": projects, "jobs": jobs, "streams": state.get("streams") or {}}


class ClusterStatus:
    def __init__(self, spark: SparkStatus | None = None, hub: AgentHubStatus | None = None):
        self.spark = spark
        self.hub = hub

    async def report(self, now: datetime | None = None) -> str:
        lines: list[str] = []
        if self.spark is not None:
            s = await self.spark.snapshot()
            if s is None:
                lines.append("Spark: not answering.")
            else:
                kv = f", KV cache {s['kv_pct']}%" if s["kv_pct"] is not None else ""
                busy = s["running"] + s["waiting"]
                head = "Spark: idle" if busy == 0 else f"Spark: {s['running']} running, {s['waiting']} waiting"
                lines.append(f"{head}{kv}.")
        if self.hub is not None:
            h = await self.hub.snapshot()
            if h is None:
                lines.append("AgentHub: not reachable.")
            else:
                streams = sum(int(v) for v in h["streams"].values() if isinstance(v, (int, float)))
                jobs = ", ".join(f"{n} {k}" for k, n in sorted(h["jobs"].items())) or "no jobs"
                lines.append(f"AgentHub: {streams} model streams open, {jobs}.")
                for p in sorted(h["projects"], key=lambda p: (str(p["status"]) != "active", str(p["priority"]))):
                    when = ""
                    if p["updated_at"] and now is not None:
                        try:
                            touched = datetime.fromtimestamp(float(p["updated_at"]) / 1000, tz=timezone.utc)
                            mins = int((now.astimezone(timezone.utc) - touched).total_seconds() // 60)
                            when = f", touched {mins // 60}h ago" if mins >= 60 else f", touched {mins}m ago"
                        except (ValueError, OverflowError, OSError):
                            when = ""
                    prio = f", priority {p['priority']}" if p["priority"] is not None else ""
                    lines.append(f"• {p['title']} — {p['status']}{prio}{when}")
        return "\n".join(lines) if lines else "Nothing to report: no Spark or AgentHub configured."
