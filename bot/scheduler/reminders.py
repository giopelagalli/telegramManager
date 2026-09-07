from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from bot.knowledge.store import KnowledgeStore
from bot.knowledge.views import fmt_time
from bot.scheduler.outbound import Outbound
from bot.scheduler.state import CriticalLeaveState, RuntimeState

LATE_WINDOW = timedelta(minutes=15)


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
                    profile.home_latlng, ev.location_latlng, depart_at=times.leave_by
                )
                if minutes is not None and minutes != ev.travel_minutes:
                    ev.travel_minutes = minutes
                    store.save(ev)
                    store.commit("maps: refresh travel")
                    times = ev.times(profile)
            out.append(
                Outbound(
                    text=f"Get ready for {ev.title}. Leave by {fmt_time(times.leave_by)}.",
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
                        text=f"Leave in the next {profile.leave_lead_minutes} minutes for {ev.title}.",
                        kind="reminder",
                    )
                )

        missed_key = f"missed:{ev.path}"
        missed_at = ev.start + timedelta(minutes=15)
        if is_due(missed_key, missed_at, now, state):
            ev.status = "missed"
            store.save(ev)
            store.commit("event missed")

    return out
