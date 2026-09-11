from __future__ import annotations

import re
from datetime import datetime, time, timedelta

from bot.agent.prompts import build_context
from bot.knowledge.models import Channel, Course, slugify
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
BIND_KINDS = ("assignments", "exams", "review")
BIND_USAGE = "Usage: /bind course <CODE> <title>, /bind assignments, /bind exams or /bind review."
BIND_IN_DM = "Bind topics inside your group, not here."
MOVE_USAGE = "Usage: /move <slug> — the course slug from /courses."
NOW_LEAD_MINUTES = 90
HARD_UNCONFIGURED_REPLY = "No hard model configured. Set HARD_MODEL in .env."
HARD_OFFLINE_REPLY = "The hard model didn't answer; try again."
HARD_COURSE_GONE_REPLY = "This topic's course file is gone; /bind again."

COMMANDS: list[tuple[str, str, bool]] = [
    ("todo", "Top 5 open todos (/todo all for everything)", True),
    ("backlog", "Everything parked in the backlog", False),
    ("goals", "Active goals and progress", False),
    ("today", "Today's events and top todos", True),
    ("week", "The next 7 days", True),
    ("now", "The one thing to do right now", True),
    ("brief", "Morning briefing now (/brief 9am to shift today's)", True),
    ("courses", "Courses and their topic counts", False),
    ("sources", "Course material stored here", False),
    ("summary", "Summary of a source from the last /sources (/summary 2)", False),
    ("move", "Move the last ingested source to another course (/move cs101)", False),
    ("channels", "Which topic is bound to what", False),
    ("bind", "Bind this topic (/bind course CS101 Intro to CS)", False),
    ("unbind", "Unbind this topic", False),
    ("pause", "Quiet for a while (/pause 2h)", True),
    ("quiet", "Quiet until the end of the day", False),
    ("resume", "Cancel the pause", False),
    ("undo", "Revert the last change", False),
    ("hard", "Ask the big cloud model (/hard why does X happen?)", False),
    ("think", "Model thinking on/off for the Spark model (/think on)", False),
    ("help", "List commands", True),
]

_MAIN_COMMANDS = [(name, desc) for name, desc, menu in COMMANDS if menu]
_MORE_COMMANDS = [(name, desc) for name, desc, menu in COMMANDS if not menu]
HELP_TEXT = (
    "<b>Commands</b>\n"
    + "\n".join(f"/{name} — {esc(desc)}" for name, desc in _MAIN_COMMANDS)
    + "\n\n<b>More</b>\n"
    + "\n".join(f"/{name} — {esc(desc)}" for name, desc in _MORE_COMMANDS)
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

    if name == "sources":
        return [Outbound(_render_sources(store, state, channel), kind="reply")]

    if name == "summary":
        return [Outbound(_render_summary(arg, store, state), kind="reply")]

    if name == "move":
        return _move(arg, store, channel)

    if name == "courses":
        return [Outbound(_render_courses(store.courses()), kind="reply")]

    if name == "channels":
        return [Outbound(_render_channels(store), kind="reply")]

    if name == "bind":
        return _bind(arg, store, channel)

    if name == "unbind":
        return _unbind(store, channel)

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
            return [Outbound(esc(HARD_COURSE_GONE_REPLY), kind="reply")]
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
    return "Nothing urgent. Pick something from /backlog or rest."


def _render_sources(store, state, channel: Channel | None) -> str:
    slug = channel.course if channel is not None and channel.kind == "course" else None
    sources = store.sources(slug)
    if not sources:
        return "No sources yet. Drop a PDF, slides or a photo in a course topic."

    state.last_sources_listing = [s.path for s in sources]
    titles = {c.slug: c.title for c in store.courses()}
    lines: list[str] = []
    if slug is not None:
        lines.append(f"<b>Sources — {esc(titles.get(slug, slug))}</b>")
    current = None
    for i, source in enumerate(sources, 1):
        if slug is None and source.course != current:
            current = source.course
            lines.append(f"<b>{esc(titles.get(current, current))}</b>")
        pages = f", {source.pages} pages" if source.pages else ""
        lines.append(f"{i}. {esc(source.title)} ({esc(source.kind)}{pages})")
    return "\n".join(lines)


def _render_summary(arg: str, store, state) -> str:
    if not state.last_sources_listing:
        return "Run /sources first, then /summary 2."
    try:
        index = int(arg)
    except ValueError:
        return "Which one? Try /summary 2."
    if not 1 <= index <= len(state.last_sources_listing):
        return f"There's no {index} in the last /sources list."
    try:
        source = store.get_source(state.last_sources_listing[index - 1])
    except KeyError:
        return "That source is gone. Run /sources again."
    summary = source.summary.strip() or "No summary was written for this one."
    return f"<b>{esc(source.title)}</b>\n{esc(summary)}"


def _move(arg: str, store, channel: Channel | None) -> list[Outbound]:
    if channel is None or channel.kind != "course":
        return [Outbound("Run /move inside a course topic.", kind="reply")]
    slug = slugify(arg)
    if not slug:
        return [Outbound(esc(MOVE_USAGE), kind="reply")]
    try:
        course = store.get_course(slug)
    except KeyError:
        return [Outbound(f"No course {esc(slug)}. Run /courses to see them.", kind="reply")]

    sources = store.sources(channel.course)
    if not sources:
        return [Outbound("Nothing to move here.", kind="reply")]
    latest = max(sources, key=lambda s: (s.timestamp.timestamp() if s.timestamp else 0.0, s.path))
    store.move_source(latest.path, slug)
    store.commit(f"move: {latest.title} -> {slug}")
    return [Outbound(f"Moved {esc(latest.title)} to {esc(course.title)}.", kind="reply")]


def _render_courses(courses) -> str:
    if not courses:
        return "No courses yet. Run /bind course CS101 Intro to CS inside a topic."
    lines = ["<b>Courses</b>"]
    for course in courses:
        lines.append(f"- {esc(course.title)} ({esc(course.slug)}) — {len(course.topics)} topics")
    return "\n".join(lines)


def _render_channels(store) -> str:
    bindings = store.channels().bindings
    if not bindings:
        return "Nothing bound yet. Run /bind inside a topic."
    titles = {c.slug: c.title for c in store.courses()}
    lines = ["<b>Channels</b>"]
    for channel in bindings.values():
        label = channel.name
        if channel.kind == "course":
            label = f"{label} — {titles.get(channel.course, channel.course)}"
        lines.append(f"- {esc(label)}")
    return "\n".join(lines)


def _bind(arg: str, store, channel: Channel | None) -> list[Outbound]:
    if channel is None or channel.kind == "life":
        return [Outbound(BIND_IN_DM, kind="reply")]
    kind, _, rest = arg.partition(" ")
    kind = kind.lower()
    if kind in BIND_KINDS:
        bound = Channel(channel.chat_id, channel.thread_id, kind)
        _save_binding(store, bound, f"bind: {kind}")
        return [Outbound(f"Bound this topic to {kind}.", kind="reply", channel=bound.name)]
    if kind == "course":
        return _bind_course(rest.strip(), store, channel)
    return [Outbound(esc(BIND_USAGE), kind="reply", target=_here(channel))]


def _bind_course(rest: str, store, channel: Channel) -> list[Outbound]:
    code, _, title = rest.partition(" ")
    slug = slugify(code)
    if not slug:
        return [Outbound(esc(BIND_USAGE), kind="reply", target=_here(channel))]
    try:
        course = store.get_course(slug)
    except KeyError:
        # New course and its binding land in one commit, so /undo takes both.
        course = Course(path=f"courses/{slug}.md", title=title.strip() or code)
        store.add_course(course)
    bound = Channel(channel.chat_id, channel.thread_id, "course", slug)
    _save_binding(store, bound, f"bind: course {slug}")
    text = f"Bound this topic to {esc(code)} ({esc(course.title)})."
    return [Outbound(text, kind="reply", channel=bound.name)]


def _unbind(store, channel: Channel | None) -> list[Outbound]:
    channels = store.channels()
    existing = channels.by_key(channel.key) if channel is not None else None
    if existing is None:
        return [Outbound("Nothing is bound here.", kind="reply", target=_here(channel))]
    channels.unbind(channel.key)
    store.save_channels(channels)
    store.commit(f"bind: removed {existing.name}")
    return [Outbound(f"Unbound this topic from {esc(existing.name)}.", kind="reply")]


def _here(channel: Channel | None) -> tuple[int, int | None] | None:
    """Answer in the topic that asked, which no channel name maps to yet."""
    return None if channel is None else (channel.chat_id, channel.thread_id)


def _save_binding(store, bound: Channel, message: str) -> None:
    channels = store.channels()
    channels.bind(bound)
    store.save_channels(channels)
    store.commit(message)


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
