"""Web search via Brave; returns a compact text digest the model can answer from."""
from __future__ import annotations

import logging
import re
import html

import httpx

logger = logging.getLogger(__name__)
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
MAX_RESULTS = 6
READ_PAGES = 3
PAGE_CHARS = 6000
UA = "Mozilla/5.0 (compatible; JD-assistant/1.0)"


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
        if not lines:
            return None
        # Snippets rarely hold the number; the pages do. Read the top few.
        pages = []
        for r in results[:READ_PAGES]:
            text = await self.read(r.get("url", ""))
            if text:
                pages.append(f"=== {r.get('title', '')} ({r.get('url', '')}) ===\n{text}")
        return "\n".join(lines) + ("\n\nPage contents:\n" + "\n\n".join(pages) if pages else "")

    async def read(self, url: str, limit: int = PAGE_CHARS) -> str | None:
        """The readable text of a page (HTML or PDF), trimmed; None on any failure."""
        if not url.startswith("http"):
            return None
        try:
            resp = await self._http.get(url, headers={"User-Agent": UA}, follow_redirects=True, timeout=8.0)
            resp.raise_for_status()
        except (httpx.HTTPError, ValueError) as exc:
            logger.info("read %s failed: %s", url, type(exc).__name__)
            return None
        ctype = resp.headers.get("content-type", "")
        try:
            if "pdf" in ctype or url.lower().endswith(".pdf"):
                from bot.study.extract import extract_pdf
                text = "\n".join(t for _, t in extract_pdf(resp.content))
            else:
                text = html_to_text(resp.text)
        except Exception as exc:
            logger.info("read %s could not extract: %s", url, type(exc).__name__)
            return None
        text = text.strip()
        return text[:limit] + ("…" if len(text) > limit else "") if text else None


_SCRIPT_RE = re.compile(r"<(script|style|noscript|svg|nav|footer|header)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_RE = re.compile(r"</?(p|div|br|li|tr|h[1-6]|td|th|section|article)[^>]*>", re.IGNORECASE)


def html_to_text(html_src: str) -> str:
    s = _SCRIPT_RE.sub(" ", html_src)
    s = _BLOCK_RE.sub("\n", s)
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in s.splitlines()]
    return "\n".join(line for line in lines if line)
