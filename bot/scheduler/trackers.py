"""Reminders a tracker asked for: at fixed times, or every N minutes since the last entry, inside
waking hours. One line with where the day stands. Respects pause; never repeats a slot."""
from __future__ import annotations

from datetime import datetime, timedelta

from bot.knowledge.trackers import status_line
from bot.scheduler.outbound import Outbound
from bot.scheduler.reminders import is_due


def due_tracker_reminders(now: datetime, store, state) -> list[Outbound]:
    if state.pause_until and now < state.pause_until:
        return []
    profile = store.profile()
    start, end = profile.waking_window(now.date())
    if not (start <= now < end):
        return []
    out: list[Outbound] = []
    day = now.date()
    for t in store.trackers():
        if not t.active:
            continue
        for i, hm in enumerate(t.remind_at):
            hh, mm = (int(x) for x in hm.split(":"))
            when = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if is_due(f"track:{t.name}:{day}:{i}", when, now, state):
                out.append(_nudge(store, t, now))
        if t.remind_every_minutes:
            slot = int((now - start).total_seconds() // 60) // t.remind_every_minutes
            due_at = start + timedelta(minutes=slot * t.remind_every_minutes)
            if slot >= 1 and is_due(f"track:{t.name}:{day}:e{slot}", due_at, now, state):
                last = store.tracking_last_at(day, t.name)
                if last is None or now - now.replace(hour=int(last[:2]), minute=int(last[3:]), second=0, microsecond=0) >= timedelta(minutes=t.remind_every_minutes):
                    out.append(_nudge(store, t, now))
    return out


def _nudge(store, t, now: datetime) -> Outbound:
    line = status_line(store, now.date(), t)
    verb = {"water": "Drink.", "meals": "Eat.", "steps": "Walk."}.get(t.name, f"Log your {t.label}.")
    return Outbound(f"{line[0].upper()}{line[1:]}. {verb}", kind="reminder")
