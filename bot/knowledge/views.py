from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Iterable

from bot.knowledge.models import Event, Goal, Profile, Todo
from bot.knowledge.ranking import goal_progress, rank_todos


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fmt_time(dt: datetime) -> str:
    return dt.strftime("%-I:%M%p").lower()


def fmt_day(d: date) -> str:
    return d.strftime("%a %b %-d")


def fmt_long_day(d: date) -> str:
    return d.strftime("%A, %B %-d")


def render_todo(todos: Iterable[Todo], today: date, show_all: bool = False) -> str:
    open_todos = rank_todos(todos, today)
    if show_all:
        selected = open_todos
        header = f"<b>All open ({len(open_todos)})</b>"
    else:
        selected = open_todos[:5]
        header = "<b>Top 5</b>"

    if not selected:
        return "Nothing on your list."

    lines = [header]
    for i, t in enumerate(selected, 1):
        overdue = "⚠️ " if t.due and t.due < today else ""
        due_part = f", due {fmt_day(t.due)}" if t.due else ""
        lines.append(f"{i}. {overdue}{esc(t.title)} — P{t.priority}{due_part}")
    return "\n".join(lines)


def render_backlog(todos: list[Todo]) -> str:
    if not todos:
        return "Backlog is empty."
    ordered = sorted(todos, key=lambda t: (t.created, t.path))
    lines = [f"<b>Backlog ({len(ordered)})</b>"]
    for t in ordered:
        lines.append(f"• {esc(t.title)}")
    return "\n".join(lines)


_YEAR_RE = re.compile(r"^\d{4}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def render_goals(goals: list[Goal], todos: list[Todo], today: date) -> str:
    active = [g for g in goals if g.status == "active"]
    if not active:
        return "No goals yet. Tell me one."

    def bucket(g: Goal) -> int:
        if _YEAR_RE.match(g.period):
            return 0
        if _MONTH_RE.match(g.period):
            return 1
        return 2

    ordered = sorted(active, key=lambda g: (bucket(g), g.period))

    blocks = []
    lines: list[str] = []
    current_period = None
    for g in ordered:
        if g.period != current_period:
            if lines:
                blocks.append("\n".join(lines))
            lines = [f"<b>{esc(g.period)}</b>"]
            current_period = g.period
        done, total = goal_progress(g, todos)
        lines.append(f"• {esc(g.title)} — {done}/{total}")
    if lines:
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def render_today(events: list[Event], todos: list[Todo], profile: Profile, now: datetime) -> str:
    today = now.date()
    header = f"<b>{fmt_long_day(today)}</b>"
    todays_events = [e for e in events if e.start.date() == today]

    lines = [header]
    if not todays_events:
        lines.append("No events today.")
    else:
        for e in sorted(todays_events, key=lambda e: e.start):
            times = e.times(profile)
            loc = f" ({esc(e.location)})" if e.location else ""
            critical = " ‼️" if e.importance == "critical" else ""
            lines.append(f"• {fmt_time(e.start)} {esc(e.title)}{loc} — leave by {fmt_time(times.leave_by)}{critical}")

    top_block = render_todo(todos, today)
    return "\n".join(lines) + "\n\n" + top_block


def render_week(events: list[Event], profile: Profile, now: datetime, todos: list[Todo] | None = None) -> str:
    today = now.date()
    days = [today + timedelta(days=i) for i in range(7)]

    blocks = []
    for d in days:
        day_events = sorted((e for e in events if e.start.date() == d), key=lambda e: e.start)
        due = sorted((t for t in (todos or []) if t.status == "open" and t.due == d), key=lambda t: (t.priority, t.title))
        lines = [f"<b>{fmt_day(d)}</b>"]
        if not day_events and not due:
            lines.append("  free")
        for e in day_events:
            times = e.times(profile)
            time_range = fmt_time(e.start) + (f"–{fmt_time(e.end)}" if e.end else "")
            critical = " ‼️" if e.importance == "critical" else ""
            lines.append(f"  {time_range} {esc(e.title)} — leave by {fmt_time(times.leave_by)}{critical}")
        for t in due:
            lines.append(f"  due: {esc(t.title)}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
