"""Web search via Brave; returns a compact text digest the model can answer from."""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
MAX_RESULTS = 6


class BraveSearch:
    def __init__(self, api_key: str, http: httpx.AsyncClient | None = None):
        self._key = api_key
        self._http = http or httpx.AsyncClient(timeout=10.0)

    async def search(self, query: str) -> str | None:
        """Titles, URLs and snippets as text; None on any failure (never raises)."""
        try:
            resp = await self._http.get(
                BRAVE_URL,
                params={"q": query, "count": MAX_RESULTS},
                headers={"X-Subscription-Token": self._key, "Accept": "application/json"},
            )
            resp.raise_for_status()
            results = resp.json().get("web", {}).get("results", [])
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("search failed: %s", type(exc).__name__)
            return None
        lines = []
        for r in results[:MAX_RESULTS]:
            title, url, desc = r.get("title", ""), r.get("url", ""), r.get("description", "")
            lines.append(f"- {title}\n  {url}\n  {desc}")
        return "\n".join(lines) or None
