from __future__ import annotations

import re
from datetime import datetime, time, timedelta

from bot.agent.prompts import build_context
from bot.knowledge.models import Channel
from bot.knowledge.ranking import top
from bot.knowledge.views import (
    esc,
    fmt_day,
    fmt_time,
    render_backlog,
    render_goals,
    render_today,
    render_todo,
    render_week,
)
from bot.scheduler.briefings import send_morning
from bot.scheduler.outbound import Outbound
from bot.study.select import select_sources
from bot.telegram.markdown import md_to_html

DEFAULT_PAUSE_MINUTES = 120
NOW_LEAD_MINUTES = 90
HARD_UNCONFIGURED_REPLY = "No hard model configured. Set HARD_MODEL in .env."
HARD_OFFLINE_REPLY = "The hard model didn't answer; try again."

COMMANDS: list[tuple[str, str, bool]] = [
    ("todo", "Top 5", True),
    ("backlog", "Everything parked in the backlog", False),
    ("goals", "Active goals and progress", False),
    ("today", "Today", True),
    ("week", "This week", True),
    ("now", "Do this next", True),
    ("brief", "Briefing", True),
    ("pause", "Quiet for 2h", True),
    ("undo", "Revert the last change", False),
    ("hard", "Ask the big cloud model (/hard why does X happen?)", False),
    ("think", "Model thinking on/off for the Spark model (/think on)", False),
    ("help", "List commands", False),
]

_MAIN_COMMANDS = [(name, desc) for name, desc, menu in COMMANDS if menu]
_MORE_COMMANDS = [(name, desc) for name, desc, menu in COMMANDS if not menu]
HELP_TEXT = (
    "\n".join(f"/{name} — {esc(desc)}" for name, desc in _MAIN_COMMANDS)
    + "\n\nEverything else, just say it: what you need to do, where you need to be, "
    "what you're working on. Drop in files to study them. Also: /backlog, /goals, /undo, /hard."
)

_DURATION_RE = re.compile(r"^(\d+)\s*([hm]?)$", re.IGNORECASE)
_TIME_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", re.IGNORECASE)


async def handle(
    name: str, arg: str, store, agent, state, now: datetime, channel: Channel | None = None
) -> list[Outbound]:
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

    if name == "now":
        return [Outbound(_render_now(store, now), kind="reply")]

    if name == "brief":
        return await _brief(arg, store, agent, state, now)








    if name == "pause":
        minutes = _parse_duration(arg)
        if minutes is None:
            return [Outbound("I don't understand that duration. Try /pause 2h.", kind="reply")]
        state.pause_until = now + timedelta(minutes=minutes)
        return [Outbound(
            f"Paused for {_label(minutes)}. Reminders and critical alerts still come through.",
            buttons=[("▶️ Resume", "resume")],
            kind="reply",
        )]



    if name == "undo":
        subject = store.undo()
        if subject is None:
            return [Outbound("Nothing to undo.", kind="reply")]
        return [Outbound(f"Reverted: {esc(subject)}", kind="reply")]

    if name == "hard":
        return await _hard(arg, store, agent, now, channel)

    if name == "think":
        return _think(arg, store, agent)

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


async def _hard(text: str, store, agent, now: datetime, channel: Channel | None) -> list[Outbound]:
    if agent.hard is None:
        return [Outbound(HARD_UNCONFIGURED_REPLY, kind="reply")]
    model_name = getattr(agent.hard, "model", "the hard model")

    if channel is not None and channel.kind == "course":
        try:
            course = store.get_course(channel.course)
        except KeyError:
            return [Outbound("This topic's course file is gone.", kind="reply")]
        sources = select_sources(text, store.sources(channel.course), store.profile().tutor_context_chars)
        answer, _note = await agent.tutor(text, course, sources, notes_tool=False, client=agent.hard)
    else:
        answer = await agent.answer(text, build_context(store, now), client=agent.hard)

    if answer is None:
        return [Outbound(HARD_OFFLINE_REPLY, kind="reply")]
    via = f"<i>via {esc(model_name)}</i>"
    return [Outbound(f"{via}\n{md_to_html(answer)}", kind="reply")]


def _think(arg: str, store, agent) -> list[Outbound]:
    profile = store.profile()
    if not arg:
        return [Outbound(f"Thinking is {'on' if profile.thinking else 'off'}.", kind="reply")]
    word = arg.strip().lower()
    if word not in ("on", "off"):
        return [Outbound("Usage: /think on or /think off.", kind="reply")]
    value = word == "on"
    profile.thinking = value
    store.save_profile(profile)
    store.commit(f"profile: thinking {word}")
    client = getattr(agent.client, "primary", agent.client)
    client.enable_thinking = value
    text = "Thinking on — answers are slower but deeper." if value else "Thinking off — fast mode."
    return [Outbound(text, kind="reply")]


def _render_now(store, now: datetime) -> str:
    """One deterministic line: the next thing, no model involved."""
    profile = store.profile()
    soon = [
        e
        for e in store.events()
        if e.status == "upcoming" and timedelta() <= e.start - now <= timedelta(minutes=NOW_LEAD_MINUTES)
    ]
    if soon:
        event = min(soon, key=lambda e: e.start)
        leave_by = event.times(profile).leave_by
        return f"Get ready: {esc(event.title)} at {fmt_time(event.start)}, leave by {fmt_time(leave_by)}."

    ranked = top(store.todos(), now.date(), 1)
    if ranked:
        todo = ranked[0]
        due = f" (due {fmt_day(todo.due)})" if todo.due else ""
        return f"Do this: {esc(todo.title)}{due}"
    return "Nothing urgent. Rest, or pick something from /backlog."


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
