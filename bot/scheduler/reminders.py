from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from bot.knowledge.models import hm_to_time
from bot.knowledge.store import KnowledgeStore
from bot.knowledge.views import esc, fmt_time
from bot.maps.client import directions_url
from bot.scheduler.outbound import Outbound
from bot.scheduler.state import CriticalLeaveState, RuntimeState

LATE_WINDOW = timedelta(minutes=15)
MISSED_AFTER = timedelta(minutes=15)
DEADLINE_LEADS = ((60, "an hour"), (30, "30 minutes"))  # before a todo's due time


class MapsClient(Protocol):
    async def travel_minutes(self, origin, dest, depart_at: datetime) -> int | None: ...


def is_due(key: str, when: datetime, now: datetime, state: RuntimeState) -> bool:
    if key in state.fired:
        return False
    if now < when:
        return False
    state.fired.add(key)
    return now < when + LATE_WINDOW


async def due_reminders(
    now: datetime, store: KnowledgeStore, state: RuntimeState, maps: MapsClient | None
) -> list[Outbound]:
    profile = store.profile()
    out: list[Outbound] = []

    for ev in store.events():
        if ev.status != "upcoming":
            continue
        times = ev.times(profile)

        get_ready_key = f"get_ready:{ev.path}"
        if is_due(get_ready_key, times.get_ready_at, now, state):
            if maps is not None and profile.home_latlng and ev.location_latlng:
                minutes = await maps.travel_minutes(
                    profile.home_latlng, ev.location_latlng, depart_at=times.leave_by,
                    mode=ev.travel_mode or profile.travel_mode,
                )
                if minutes is not None and minutes != ev.travel_minutes:
                    ev.travel_minutes = minutes
                    store.save(ev)
                    store.commit("maps: refresh travel")
                    times = ev.times(profile)
            out.append(
                Outbound(
                    text=f"Get ready for {esc(ev.title)}. Leave by {fmt_time(times.leave_by)}.",
                    kind="reminder",
                )
            )

        leave_key = f"leave:{ev.path}"
        if is_due(leave_key, times.leave_at, now, state):
            if ev.importance == "critical":
                state.critical = CriticalLeaveState(ev.path, "lead", now, None, 0)
            else:
                out.append(
                    Outbound(
                        text=f"Leave in the next {profile.leave_lead_minutes} minutes for {esc(ev.title)}."
                        + _directions_suffix(ev),
                        kind="reminder",
                    )
                )

        missed_key = f"missed:{ev.path}"
        missed_at = ev.start + MISSED_AFTER
        if is_due(missed_key, missed_at, now, state):
            ev.status = "missed"
            store.save(ev)
            store.commit("event missed")

    for todo in store.todos():
        if todo.status != "open" or todo.due is None or not todo.due_time:
            continue
        due_at = datetime.combine(todo.due, hm_to_time(todo.due_time), tzinfo=profile.tz)
        for minutes, left in DEADLINE_LEADS:
            # The due time is in the key, so a moved deadline gets its own reminders.
            key = f"due{minutes}:{todo.path}@{due_at:%Y-%m-%dT%H:%M}"
            if is_due(key, due_at - timedelta(minutes=minutes), now, state):
                out.append(
                    Outbound(
                        text=f"{esc(todo.title)} is due at {fmt_time(due_at)}, {left} from now.",
                        buttons=[("✅ Done", f"done:{todo.path}")],
                        kind="reminder",
                    )
                )

    return out


def _directions_suffix(ev) -> str:
    url = directions_url(ev.location, ev.location_latlng, mode=ev.travel_mode)
    return f' <a href="{url}">Directions</a>' if url else ""
