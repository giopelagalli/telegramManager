"""The last two days of conversation, whole. The Spark has a 262k context and costs nothing per
token, so the working memory is days, not the last eight lines. Days turn over at 4am, and
anything older than yesterday is dropped; a gap of a few hours is marked so the model knows
time passed. Nightly notes distil today's part."""
from __future__ import annotations

from datetime import datetime, timedelta

THREAD_MAX_CHARS = 60_000
TEXT_MAX = 2_000
GAP_HOURS = 3
DAY_STARTS_AT = 4  # 4am: a chat that runs past midnight is still "today"
KEEP_DAYS = 2  # today and yesterday


def remember(state, role: str, text: str, now: datetime) -> None:
    state.recent.append([role, text[:TEXT_MAX], now.isoformat()])
    total = sum(len(e[1]) for e in state.recent)
    while state.recent and total > THREAD_MAX_CHARS:
        total -= len(state.recent[0][1])
        state.recent.pop(0)


def _when(entry) -> datetime | None:
    return datetime.fromisoformat(entry[2]) if len(entry) > 2 and entry[2] else None


def _day(when: datetime):
    return (when - timedelta(hours=DAY_STARTS_AT)).date()


def thread(state, now: datetime, only_today: bool = False) -> list[list[str]]:
    """The conversation as [role, text] pairs, ready for the model: the last two days, or
    just today's part (for the nightly notes)."""
    today = _day(now)
    state.recent = [
        e for e in state.recent
        if _when(e) is None or (today - _day(_when(e).astimezone(now.tzinfo))).days < KEEP_DAYS
    ]
    out: list[list[str]] = []
    prev: datetime | None = None
    for entry in state.recent:
        role, text = entry[0], entry[1]
        when = _when(entry)
        if only_today and when is not None and _day(when.astimezone(now.tzinfo)) != today:
            continue
        if role == "user" and prev is not None and when is not None and when - prev >= timedelta(hours=GAP_HOURS):
            text = f"[{int((when - prev).total_seconds() // 3600)}h later] {text}"
        out.append([role, text])
        if when is not None:
            prev = when
    return out


def user_turns(state, now: datetime) -> int:
    """How much they actually talked today."""
    return sum(1 for role, _ in thread(state, now, only_today=True) if role == "user")
