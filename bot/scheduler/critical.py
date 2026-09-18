from __future__ import annotations

from datetime import datetime, timedelta

from bot.knowledge.store import KnowledgeStore
from bot.knowledge.views import fmt_time
from bot.maps.client import directions_url, distance_m
from bot.scheduler.outbound import Outbound
from bot.scheduler.reminders import is_due
from bot.scheduler.state import RuntimeState

HOME_RADIUS_M = 150
DEST_RADIUS_M = 200
STORM_FAST_AFTER = timedelta(minutes=5)



def _event(store: KnowledgeStore, path: str):
    try:
        return store.get_event(path)
    except KeyError:
        return None


def leave_tick(now: datetime, state: RuntimeState, store: KnowledgeStore) -> Outbound | None:
    crit = state.critical
    profile = store.profile()
    ev = _event(store, crit.event_path)
    if ev is None:
        state.critical = None
        return None
    times = ev.times(profile)
    leave_by = times.leave_by
    cap_at = leave_by + timedelta(minutes=profile.critical_leave_cap_minutes)

    if crit.phase == "lead":
        if crit.sent_count == 0:
            crit.sent_count += 1
            crit.last_sent_at = now
            if now >= leave_by:
                crit.phase = "storm"
            return Outbound(
                text=(
                    f"{profile.name}, you need to leave now for {ev.title}. "
                    f"You will be late after {fmt_time(leave_by)}. Confirm you have left." + _dirs(ev)
                ),
                location_button=True,
                critical=True,
                kind="critical",
            )
        if now < leave_by:
            return None
        crit.phase = "storm"

    if crit.phase == "storm":
        if now >= cap_at:
            ev.status = "missed"
            store.save(ev)
            store.commit("critical: leave missed")
            crit_text = f"I'll stop now. {ev.title} is marked missed."
            crit.phase = "done"
            state.critical = None
            return Outbound(text=crit_text, kind="critical")

        cadence = timedelta(seconds=60) if now < leave_by + STORM_FAST_AFTER else timedelta(seconds=30)
        if crit.last_sent_at is not None and now < crit.last_sent_at + cadence:
            return None

        if crit.sent_count % 2 == 0:
            text = f"{profile.name}, if you don't leave now you will be late for {ev.title}. Share your location to confirm."
        else:
            text = f"{profile.name}, you need to leave now. {ev.title} at {fmt_time(ev.start)}. Tap the button and share your location."
        crit.sent_count += 1
        crit.last_sent_at = now
        return Outbound(text=text, location_button=True, critical=True, kind="critical")

    return None


def leave_on_location(
    now: datetime, state: RuntimeState, store: KnowledgeStore, lat: float, lng: float
) -> Outbound | None:
    profile = store.profile()
    crit = state.critical
    if crit is not None:
        ev = _event(store, crit.event_path)
        if ev is None:
            state.critical = None
            return Outbound(text=f"That event is gone, {profile.name}. Standing down.", kind="critical")
    else:
        ev = next(
            (e for e in store.events() if e.status == "left" and e.start.date() == now.date()),
            None,
        )
        if ev is None:
            return None

    if ev is not None and ev.location_latlng and distance_m((lat, lng), ev.location_latlng) < DEST_RADIUS_M:
        ev.status = "arrived"
        store.save(ev)
        store.commit("critical: arrived")
        state.critical = None
        return Outbound(text=f"You made it to {ev.title}.", kind="critical")

    if crit is None:
        return Outbound(text=f"You are still at home, {profile.name}.", kind="critical")

    home = profile.home_latlng
    if home is None:
        ev.status = "left"
        store.save(ev)
        store.commit("critical: left home")
        state.critical = None
        return Outbound(
            text=f"Can't check home, taking your word for it, {profile.name}. On your way to {ev.title}.",
            kind="critical",
        )

    if distance_m((lat, lng), home) > HOME_RADIUS_M:
        ev.status = "left"
        store.save(ev)
        store.commit("critical: left home")
        state.critical = None
        return Outbound(text=f"Confirmed, you're on your way to {ev.title}.", kind="critical")

    return Outbound(text=f"You are still at home, {profile.name}.", kind="critical")


def leave_on_text(state: RuntimeState, store: KnowledgeStore) -> Outbound | None:
    if state.critical is None:
        return None
    profile = store.profile()
    if _event(store, state.critical.event_path) is None:
        state.critical = None
        return Outbound(text=f"That event is gone, {profile.name}. Standing down.", kind="critical")
    return Outbound(text=f"Words don't count, {profile.name}. Tap the button and share your location.", kind="critical")


def _dirs(ev) -> str:
    url = directions_url(ev.location, ev.location_latlng)
    return f' <a href="{url}">Directions</a>' if url else ""
