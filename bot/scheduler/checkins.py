from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta

from bot.agent.prompts import build_context
from bot.knowledge.models import Event, Profile
from bot.knowledge.ranking import top
from bot.knowledge.store import KnowledgeStore
from bot.knowledge.views import esc, fmt_time
from bot.scheduler.outbound import Outbound
from bot.scheduler.reminders import is_due
from bot.scheduler.state import Chain, RuntimeState


def checkin_slots(profile: Profile, day: date) -> list[tuple[str, datetime]]:
    start, end = profile.waking_window(day)
    interval = profile.checkin_interval_minutes
    slots: list[tuple[str, datetime]] = []
    i = 0
    while True:
        slot_start = start + timedelta(minutes=interval * i)
        if slot_start >= end:
            break
        jitter = int(hashlib.sha256(f"{day}:{i}".encode()).hexdigest(), 16) % interval
        due = slot_start + timedelta(minutes=jitter)
        slots.append((f"checkin:{day}:{i}", due))
        i += 1
    return slots


def should_skip_checkin(
    now: datetime, profile: Profile, state: RuntimeState, events: list[Event]
) -> str | None:
    if state.pause_until and now < state.pause_until:
        return "paused"
    if state.last_user_message_at and now - state.last_user_message_at <= timedelta(
        minutes=profile.checkin_skip_if_active_minutes
    ):
        return "active"
    for ev in events:
        end = ev.end or ev.start + timedelta(hours=1)
        if ev.start <= now < end:
            return "in_event"
    return None


def consume_checkin_slot(now: datetime, state: RuntimeState, profile: Profile) -> bool:
    """Mark a due check-in slot as fired without composing anything."""
    for key, due in checkin_slots(profile, now.date()):
        if is_due(key, due, now, state):
            return True
    return False


async def due_checkin(now: datetime, store: KnowledgeStore, state: RuntimeState, agent) -> Outbound | None:
    profile = store.profile()
    day = now.date()
    events = store.events()

    for key, due in checkin_slots(profile, day):
        if not is_due(key, due, now, state):
            continue

        if should_skip_checkin(now, profile, state, events) is not None:
            return None

        todos = top(store.todos(), day, n=1)
        top1 = todos[0] if todos else None
        next_event = next((e for e in events if e.status == "upcoming" and e.start >= now), None)

        parts = []
        if next_event:
            parts.append(f"Next up: {next_event.title} at {fmt_time(next_event.start)}.")
        if top1:
            parts.append(f"Top todo: {top1.title}.")
        parts.append("Done yet?")
        fallback = " ".join(parts)

        context = build_context(store, now)
        text = esc(await agent.compose("checkin", context, fallback))

        item = (top1.title if top1 else None) or (next_event.title if next_event else None) or ""
        state.chain = Chain("checkin", now, now, 0, item=item, history=[text])

        buttons = [("✅ Done", f"done:{top1.path}")] if top1 else []
        buttons += [("⏳ Still on it", "ack:still"), ("⏭ Skip today", "ack:skip")]
        return Outbound(
            text, voice=profile.voice_on_proactive, buttons=buttons, silent=True, kind="checkin"
        )

    return None
