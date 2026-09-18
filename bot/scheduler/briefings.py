from __future__ import annotations

import logging

from datetime import date, datetime, timedelta

from bot.knowledge.models import Profile, hm_to_time
from bot.knowledge.ranking import goal_progress, top
from bot.knowledge.store import KnowledgeStore
from bot.knowledge.views import esc, fmt_time, render_today
from bot.scheduler.outbound import Outbound
from bot.scheduler.reminders import is_due
from bot.memory.thread import thread, user_turns
from bot.scheduler.state import Chain, RuntimeState
from bot.telegram.markdown import md_to_html

_FALLBACK_PROSE = "Have a good one."


def briefing_time(profile: Profile, state: RuntimeState, day: date, which: str) -> datetime:
    override = state.briefing_override.get(f"{which}:{day}")
    hm = override if override else (profile.morning_briefing if which == "morning" else profile.evening_briefing)
    return datetime.combine(day, hm_to_time(hm), tzinfo=profile.tz)

logger = logging.getLogger(__name__)


def morning_text(store: KnowledgeStore, now: datetime) -> str:
    profile = store.profile()
    events = store.events()
    broken = list(store.broken_files)
    todos = store.todos()
    broken += store.broken_files
    goals = store.goals()
    broken += store.broken_files
    broken_files = sorted(set(broken))

    iso = now.isocalendar()
    current_week = f"{iso[0]}-W{iso[1]:02d}"
    week_goals = [g for g in goals if g.status == "active" and g.period == current_week]

    goal_lines = ["<b>This week</b>"]
    if week_goals:
        for g in week_goals:
            done, tot = goal_progress(g, todos)
            goal_lines.append(f"• {esc(g.title)} — {done}/{tot}")
    else:
        goal_lines.append("No goals this week.")

    parts = [
        f"<b>Good morning, {profile.name}.</b>",
        render_today(events, todos, profile, now),
        "\n".join(goal_lines),
        "{prose}",
    ]
    if broken_files:
        parts.append(f"⚠️ {len(broken_files)} file(s) in the bundle couldn't be read; see the log.")
    return "\n\n".join(parts)


def evening_text(store: KnowledgeStore, now: datetime) -> tuple[str, list[tuple[str, str]]]:
    profile = store.profile()
    today = now.date()
    tomorrow = today + timedelta(days=1)
    todos = store.todos()
    events = store.events()

    done_today = [t for t in todos if t.status == "done" and t.done_at and t.done_at.date() == today]
    slipped = [t for t in todos if t.due is not None and t.due <= today and t.status == "open"]
    unconfirmed = [
        t for t in todos if t.status == "done" and not t.confirmed and t.done_at and t.done_at.date() == today
    ]
    missed_today = [e for e in events if e.start.date() == today and e.status == "missed"]

    lines = ["<b>Evening wrap-up</b>"]

    if done_today:
        lines.append("Done today:")
        for t in done_today:
            lines.append(f"• {esc(t.title)}")
    else:
        lines.append("Nothing marked done today.")

    buttons: list[tuple[str, str]] = []
    if slipped:
        lines.append("")
        lines.append("Slipped:")
        for t in slipped:
            lines.append(f"• {esc(t.title)}")
            buttons.append((f"→ tomorrow: {t.title[:20]}", f"defer:{t.path}"))

    if unconfirmed:
        lines.append("")
        lines.append("Unconfirmed:")
        for t in unconfirmed:
            lines.append(f"• {esc(t.title)}")

    if missed_today:
        lines.append("")
        lines.append("Missed today:")
        for e in missed_today:
            lines.append(f"• {esc(e.title)}")

    starving = _starving_goals(store.goals(), todos, today)
    if starving:
        lines.append("")
        lines.append(f"Nothing toward {esc(starving)} yet. Tomorrow?")

    tomorrow_events = [e for e in events if e.start.date() == tomorrow]
    if tomorrow_events:
        e = tomorrow_events[0]
        times = e.times(profile)
        lines.append("")
        lines.append(
            f"Tomorrow: {esc(e.title)} at {fmt_time(e.start)} — "
            f"get ready {fmt_time(times.get_ready_at)}, leave by {fmt_time(times.leave_by)}."
        )

    return "\n".join(lines), buttons


def _starving_goals(goals, todos, today: date) -> str:
    """This week's and this month's goals with nothing done toward them, as one phrase."""
    iso = today.isocalendar()
    periods = {f"{iso[0]}-W{iso[1]:02d}": "this week", f"{today:%Y-%m}": "this month"}
    names = [
        f"{g.title} {periods[g.period]}"
        for g in goals
        if g.status == "active" and g.period in periods and goal_progress(g, todos)[0] == 0
    ]
    return ", ".join(names[:2]) if names else ""


def _chain_item(store: KnowledgeStore, day: date) -> str:
    todos = store.todos()
    slipped = [t for t in todos if t.due is not None and t.due <= day and t.status == "open"]
    top_todos = top(todos, day, n=1)
    return (slipped[0].title if slipped else None) or (top_todos[0].title if top_todos else None) or ""


async def _compose_prose(store: KnowledgeStore, now: datetime, agent) -> str:
    context = agent.context(now)
    return await agent.compose("briefing", context, _FALLBACK_PROSE)


# Set by the entrypoint: an object with `async line(latlng) -> str | None` (see bot/weather.py).
WEATHER = None


async def _weather_line(profile) -> str | None:
    if WEATHER is None or profile.home_latlng is None:
        return None
    try:
        return await WEATHER.line(profile.home_latlng)
    except Exception:  # a weather outage must never block the briefing
        logger.exception("weather line failed")
        return None


async def morning_outbound(store: KnowledgeStore, now: datetime, agent, note: str | None = None) -> Outbound:
    prose = await _compose_prose(store, now, agent)
    text = morning_text(store, now).replace("{prose}", md_to_html(prose))
    weather = await _weather_line(store.profile())
    if weather:
        head, _, rest = text.partition("\n")
        text = f"{head}\n{esc(weather)}\n{rest}"
    if note:
        text = f"{note}\n\n{text}"
    profile = store.profile()
    top_todos = top(store.todos(), now.date(), n=5)
    buttons = [("✅ " + t.title[:24], f"done:{t.path}") for t in top_todos]
    return Outbound(text, voice=profile.voice_on_proactive, buttons=buttons, kind="briefing")


async def evening_outbound(store: KnowledgeStore, now: datetime, agent, notes: list[str] | None = None) -> Outbound:
    body, buttons = evening_text(store, now)
    prose = await _compose_prose(store, now, agent)
    noted = ("\n\nNoted today:\n" + "\n".join(f"• {esc(n)}" for n in notes)) if notes else ""
    text = f"{body}{noted}\n\n{md_to_html(prose)}"
    return Outbound(text, voice=store.profile().voice_on_proactive, buttons=buttons, kind="briefing")


async def nightly_notes(store: KnowledgeStore, state: RuntimeState, now: datetime, agent) -> list[str]:
    """Distil today's conversation into memories, once, before the evening wrap-up."""
    if user_turns(state) < 4:
        return []
    notes = await agent.consolidate(thread(state, now), [m.text for m in store.memories()])
    for note in notes:
        store.add_memory(note["text"], kind=note["kind"])
    if notes:
        store.commit("memory: nightly notes")
    return [n["text"] for n in notes]


async def send_morning(
    now: datetime, store: KnowledgeStore, state: RuntimeState, agent, note: str | None = None
) -> Outbound:
    day = now.date()
    state.fired.add(f"morning:{day}")

    outbound = await morning_outbound(store, now, agent, note)
    state.chain = Chain("briefing", now, now, 0, item=_chain_item(store, day), history=[outbound.text])
    return outbound


async def due_briefings(now: datetime, store: KnowledgeStore, state: RuntimeState, agent) -> list[Outbound]:
    profile = store.profile()
    day = now.date()
    out: list[Outbound] = []

    morning_key = f"morning:{day}"
    morning_at = briefing_time(profile, state, day, "morning")
    if is_due(morning_key, morning_at, now, state):
        outbound = await morning_outbound(store, now, agent)
        out.append(outbound)
        state.chain = Chain("briefing", now, now, 0, item=_chain_item(store, day), history=[outbound.text])

    evening_key = f"evening:{day}"
    evening_at = briefing_time(profile, state, day, "evening")
    if is_due(evening_key, evening_at, now, state):
        outbound = await evening_outbound(store, now, agent, notes=await nightly_notes(store, state, now, agent))
        out.append(outbound)
        state.chain = Chain("briefing", now, now, 0, item=_chain_item(store, day), history=[outbound.text])

    return out
