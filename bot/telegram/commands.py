from __future__ import annotations

import re
from datetime import datetime, time, timedelta

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
    ("todo", "Your personal list", True),
    ("backlog", "Parked todos, not on the list", False),
    ("today", "Today", True),
    ("week", "This week", True),
    ("now", "Do this next", True),
    ("brief", "Briefing", True),
    ("due", "What's due, soonest first", True),
    ("courses", "Each class: slots, tests, assignments", True),
    ("schedule", "Your classes, day by day", True),
    ("goals", "Your goals and where you stand", True),
    ("calories", "Today's food and the total against your target", True),
    ("track", "Water, cholesterol, steps — whatever you're tracking, today", False),
    ("reflect", "The week in numbers, and the weeks before", False),
    ("notes", "This week's scratchpad, by day", False),
    ("pause", "Quiet for 2h", True),
    ("undo", "Take back the last thing he changed", False),
    ("queue", "What's using the Spark right now", False),
    ("projects", "AgentHub projects: status, run a turn, pause", False),
    ("voice", "Voice notes on every reply, on/off (/voice on)", False),
    ("hard", "Same JD, on the bigger cloud model (/hard …)", False),
    ("think", "Slower, more careful answers on/off (/think on)", False),
    ("help", "List commands", False),
]

_MAIN_COMMANDS = [(name, desc) for name, desc, menu in COMMANDS if menu]
_MORE_COMMANDS = [(name, desc) for name, desc, menu in COMMANDS if not menu]
HELP_TEXT = (
    "\n".join(f"/{name} — {esc(desc)}" for name, desc in _MAIN_COMMANDS)
    + "\n\nEverything else, just say it: what you need to do, where you need to be, "
    "what you're working on. Drop in files to study them. Like:\n"
    "“directions to the gym” — Maps link with traffic\n"
    "“look up …” — web search\n"
    "“remember …” / “what did I say about …”\n"
    "“don't text me for 2h”\n\nAlso:\n"
    + "\n".join(f"/{name} — {esc(desc)}" for name, desc in _MORE_COMMANDS if name not in ("help", "think"))
)

_DURATION_RE = re.compile(r"^(\d+)\s*([hm]?)$", re.IGNORECASE)
_TIME_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", re.IGNORECASE)


async def handle(
    name: str, arg: str, store, agent, state, now: datetime, channel: Channel | None = None,
    recall=None, recent=None, search=None, cluster=None, projects=None,
) -> list[Outbound]:
    arg = (arg or "").strip()
    today = now.date()

    if name == "todo":
        from bot.telegram.todo_ui import list_view
        if arg.lower() == "all":
            return [Outbound(render_todo(store.todos(), today, show_all=True), kind="reply")]
        return [list_view(store, now, "personal")]

    if name == "courses":
        from bot.telegram.courses_ui import list_view
        return [list_view(store, now=now)]

    if name == "backlog":
        return [Outbound(render_backlog([t for t in store.todos(include_backlog=True) if t.in_backlog]), kind="reply")]

    if name == "goals":
        return [Outbound(render_goals(store.goals(), store.todos(), today), kind="reply")]

    if name == "today":
        return [Outbound(render_today(store.events(), store.todos(), store.profile(), now), kind="reply")]

    if name == "week":
        return [Outbound(render_week(store.events(), store.profile(), now, store.todos()), kind="reply")]

    if name == "now":
        return [_render_now(store, now)]

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



    if name == "voice":
        profile = store.profile()
        word = arg.lower()
        if word not in ("on", "off"):
            mode = "on every reply" if profile.voice_reply_mode == "always" else "when you send voice, or when you ask"
            return [Outbound(f"Voice is {mode}. /voice on or /voice off.", kind="reply")]
        profile.voice_reply_mode = "always" if word == "on" else "on_voice"
        store.save_profile(profile)
        store.commit(f"profile: voice {word}")
        return [Outbound("Voice on every reply." if word == "on" else "Voice only when you send voice or ask for it.", kind="reply")]

    if name == "schedule":
        from bot.telegram.schedule_ui import days_view
        return [days_view(store)]

    if name == "due":
        from bot.telegram.todo_ui import list_view
        return [list_view(store, now, "due")]

    if name == "calories":
        from bot.telegram.food_ui import day_view
        return [day_view(store, now)]

    if name == "track":
        from bot.knowledge.trackers import status_line
        active = [t for t in store.trackers() if t.active]
        if not active:
            return [Outbound("Nothing tracked. Say \"track my water, target 100 oz\" or just \"drank 16 oz of water\".", kind="reply")]
        lines = ["<b>Today</b>"]
        for t in active:
            entries = store.tracking(today, t.name)
            line = f"• <b>{esc(status_line(store, today, t))}</b>"
            if t.remind_at:
                line += f" · reminders {', '.join(t.remind_at)}"
            elif t.remind_every_minutes:
                line += f" · every {t.remind_every_minutes} min"
            lines.append(line)
            for e in entries:
                amount = str(int(e["amount"])) if e["amount"].is_integer() else f"{e['amount']:g}"
                lines.append(f"    {e['time']} {amount} {e['unit']}" + (f" — {esc(e['note'])}" if e["note"] else ""))
        return [Outbound("\n".join(lines), kind="reply")]

    if name == "notes":
        from bot.telegram.notes_ui import days_view
        return [days_view(store, now)]

    if name == "reflect":
        from bot.scheduler.reflect import reflect_text
        return [Outbound(reflect_text(store, now), kind="reply")]

    if name == "queue":
        if cluster is None:
            return [Outbound("Nothing to report: no Spark or AgentHub configured.", kind="reply")]
        return [Outbound(esc(await cluster.report(now)), kind="reply")]

    if name == "projects":
        from bot.telegram import projects_ui
        if projects is None:
            return [Outbound("AgentHub isn't set up (AGENTHUB_URL and AGENTHUB_TOKEN).", kind="reply")]
        await projects.refresh()
        return [projects_ui.list_view(projects)]

    if name == "undo":
        subject = store.undo()
        if subject is None:
            return [Outbound("Nothing to undo.", kind="reply")]
        return [Outbound(f"Reverted: {esc(subject)}", kind="reply")]

    if name == "hard":
        return await _hard(arg, store, agent, now, channel, recent=list(recent or []), recall=recall, search=search)

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


async def _hard(
    text: str, store, agent, now: datetime, channel: Channel | None, recent: list | None = None, recall=None, search=None
) -> list[Outbound]:
    """/hard is the one explicit door to the cloud model when there is one: the user typed it,
    so they chose it. It gets the blind view (schedule, todos, goals), the conversation so far
    (so "what do you think" means something), and only the few memories that match the question
    — never the whole vault."""
    client = agent.cloud or agent.hard
    if client is None:
        return [Outbound(HARD_UNCONFIGURED_REPLY, kind="reply")]
    remote = agent.cloud is not None or agent.hard_remote
    model_name = str(getattr(client, "model", "the hard model")).rsplit("/", 1)[-1]

    if channel is not None and channel.kind == "course":
        try:
            course = store.get_course(channel.course)
        except KeyError:
            return [Outbound("This topic's course file is gone.", kind="reply")]
        sources = select_sources(text, store.sources(channel.course), store.profile().tutor_context_chars)
        answer, _note = await agent.tutor(text, course, sources, notes_tool=False, client=client)
    else:
        context = agent.context(now, remote=remote)
        hits = (await recall(text))[:6] if recall is not None else []
        if hits:
            context += "\n\nWhat JD knows that seems relevant:\n" + "\n".join(f"- {h}" for h in hits)
        if recent:
            context += "\n\nThe conversation so far (the question refers to it):\n" + "\n".join(
                f"{role}: {body}" for role, body in recent
            )
        if search is not None:
            results = await search.search(text)  # one web search per /hard, so "what's new" questions have something real
            if results:
                context += f"\n\nWeb search results for the question (use if relevant, cite the URL you used):\n{results}"
        answer = await agent.answer(text, context, client=client)

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


def _render_now(store, now: datetime) -> Outbound:
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
        return Outbound(
            f"Get ready: <b>{esc(event.title)}</b> at {fmt_time(event.start)}, leave by <b>{fmt_time(leave_by)}</b>.", kind="reply"
        )

    ranked = top(store.todos(), now.date(), 1)
    if ranked:
        todo = ranked[0]
        due = f" (due {fmt_day(todo.due)})" if todo.due else ""
        return Outbound(
            f"Do this: <b>{esc(todo.title)}</b>{due}",
            buttons=[("✅ Done", f"done:{todo.path}"), ("🔥 Do it now", f"sprint:{todo.path}")],
            kind="reply",
        )
    return Outbound("Nothing urgent. Rest, or pick something from /backlog.", kind="reply")


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
