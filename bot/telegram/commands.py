from __future__ import annotations

import re
from datetime import datetime, time, timedelta

from bot.knowledge.ranking import top
from bot.knowledge.views import esc, fmt_time, render_backlog, render_goals, render_today, render_todo, render_week
from bot.scheduler.briefings import send_morning
from bot.scheduler.outbound import Outbound

DEFAULT_PAUSE_MINUTES = 120

COMMANDS: list[tuple[str, str]] = [
    ("todo", "Top 5 open todos (/todo all for everything)"),
    ("backlog", "Everything parked in the backlog"),
    ("goals", "Active goals and progress"),
    ("today", "Today's events and top todos"),
    ("week", "The next 7 days"),
    ("brief", "Morning briefing now (/brief 9am to shift today's)"),
    ("pause", "Quiet for a while (/pause 2h)"),
    ("quiet", "Quiet until the end of the day"),
    ("resume", "Cancel the pause"),
    ("undo", "Revert the last change"),
    ("help", "List commands"),
]

HELP_TEXT = "<b>Commands</b>\n" + "\n".join(f"/{name} — {esc(desc)}" for name, desc in COMMANDS)

_DURATION_RE = re.compile(r"^(\d+)\s*([hm]?)$", re.IGNORECASE)
_TIME_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", re.IGNORECASE)


async def handle(name: str, arg: str, store, agent, state, now: datetime) -> list[Outbound]:
    arg = (arg or "").strip()
    today = now.date()

    if name == "todo":
        todos = store.todos()
        text = render_todo(todos, today, show_all=arg.lower() == "all")
        buttons = [("✅ " + t.title[:24], f"done:{t.path}") for t in top(todos, today)]
        return [Outbound(text, buttons=buttons, kind="reply")]

    if name == "backlog":
        return [Outbound(render_backlog([t for t in store.todos(include_backlog=True) if t.in_backlog]), kind="reply")]

    if name == "goals":
        return [Outbound(render_goals(store.goals(), store.todos(), today), kind="reply")]

    if name == "today":
        return [Outbound(render_today(store.events(), store.todos(), store.profile(), now), kind="reply")]

    if name == "week":
        return [Outbound(render_week(store.events(), store.profile(), now), kind="reply")]

    if name == "brief":
        return await _brief(arg, store, agent, state, now)

    if name == "pause":
        minutes = _parse_duration(arg)
        if minutes is None:
            return [Outbound("I don't understand that duration. Try /pause 2h.", kind="reply")]
        state.pause_until = now + timedelta(minutes=minutes)
        return [Outbound(f"Paused for {_label(minutes)}. Reminders and critical alerts still come through.", kind="reply")]

    if name == "quiet":
        _, end = store.profile().waking_window(today)
        state.pause_until = end
        return [Outbound(f"Quiet until {fmt_time(end)}.", kind="reply")]

    if name == "resume":
        state.pause_until = None
        return [Outbound("Back on.", kind="reply")]

    if name == "undo":
        subject = store.undo()
        if subject is None:
            return [Outbound("Nothing to undo.", kind="reply")]
        return [Outbound(f"Reverted: {esc(subject)}", kind="reply")]

    if name == "help":
        return [Outbound(HELP_TEXT, kind="reply")]

    return [Outbound("I don't know that command. Try /help.", kind="reply")]


async def _brief(arg: str, store, agent, state, now: datetime) -> list[Outbound]:
    if not arg:
        return [await send_morning(now, store, state, agent)]
    at = _parse_time(arg)
    if at is None:
        return [Outbound("I don't understand that time. Try /brief 9am.", kind="reply")]
    day = now.date()
    state.briefing_override[f"morning:{day}"] = f"{at.hour:02d}:{at.minute:02d}"
    when = datetime.combine(day, at, tzinfo=store.profile().tz)
    return [Outbound(f"Morning briefing moved to {fmt_time(when)} today.", kind="reply")]


def _parse_duration(arg: str) -> int | None:
    if not arg:
        return DEFAULT_PAUSE_MINUTES
    match = _DURATION_RE.match(arg)
    if match is None:
        return None
    value = int(match.group(1))
    return value * 60 if match.group(2).lower() == "h" else value


def _label(minutes: int) -> str:
    return f"{minutes // 60}h" if minutes % 60 == 0 else f"{minutes}m"


def _parse_time(arg: str) -> time | None:
    match = _TIME_RE.match(arg)
    if match is None:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or "").lower()
    if meridiem == "pm" and hour != 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)
