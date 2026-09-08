from __future__ import annotations

from datetime import datetime, timedelta

from bot.knowledge.store import KnowledgeStore
from bot.knowledge.views import fmt_time
from bot.maps.client import distance_m
from bot.scheduler.outbound import Outbound
from bot.scheduler.reminders import is_due
from bot.scheduler.state import RuntimeState, WakeState

HOME_RADIUS_M = 150
DEST_RADIUS_M = 200
STORM_FAST_AFTER = timedelta(minutes=5)

_NON_SUBSTANTIVE = {"ok", "okay", "yes", "yeah", "fine", "sure", "yep", "no", "nope"}


def _event(store: KnowledgeStore, path: str):
    try:
        return store.get_event(path)
    except KeyError:
        return None


def leave_tick(now: datetime, state: RuntimeState, store: KnowledgeStore) -> Outbound | None:
    crit = state.critical
    profile = store.profile()
    ev = _event(store, crit.event_path)
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
                    f"You will be late after {fmt_time(leave_by)}. Confirm you have left."
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
    return Outbound(text=f"Words don't count, {profile.name}. Tap the button and share your location.", kind="critical")


def wake_due(now: datetime, state: RuntimeState, store: KnowledgeStore) -> bool:
    profile = store.profile()
    if not profile.wake_time or state.wake is not None:
        return False
    hh, mm = (int(x) for x in profile.wake_time.split(":"))
    when = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    day = now.date().isoformat()
    return is_due(f"wake:{day}", when, now, state)


def wake_start(now: datetime, state: RuntimeState, store: KnowledgeStore) -> Outbound:
    profile = store.profile()
    day = now.date().isoformat()
    state.wake = WakeState(day, "alarm", now, now, 60, 0, 0, None, None)
    return Outbound(
        text=f"{profile.name}, time to get up. Reply with anything.",
        kind="wake",
        critical=True,
        voice=False,
    )


def wake_tick(now: datetime, state: RuntimeState, store: KnowledgeStore) -> Outbound | None:
    w = state.wake
    profile = store.profile()

    if w.phase == "done":
        return None

    if now >= w.started_at + timedelta(minutes=profile.wakeup_cap_minutes):
        w.phase = "done"
        w.verified = False
        return Outbound(text="Couldn't verify you're up. Here's your morning anyway.", kind="wake")

    if w.phase == "alarm":
        if w.last_sent_at is not None and now < w.last_sent_at + timedelta(seconds=w.cadence_seconds):
            return None
        n = int((now - w.started_at).total_seconds() // 60) + 1
        w.last_sent_at = now
        return Outbound(text=f"{profile.name}, get up. ({n})", kind="wake")

    if w.phase == "challenge":
        return None

    if w.phase == "engage":
        last = w.last_reply_at if w.last_reply_at is not None else w.started_at
        if now - last > timedelta(seconds=60):
            w.phase = "alarm"
            w.cadence_seconds = 30
            w.last_sent_at = now
            return Outbound(text=f"{profile.name}, still with me? Get up.", kind="wake")
        return None

    return None


def wake_on_message(now: datetime, state: RuntimeState, store: KnowledgeStore, text: str) -> Outbound | None:
    w = state.wake
    profile = store.profile()

    if w.phase == "alarm":
        w.phase = "challenge"
        w.last_reply_at = now
        return Outbound(text=f"Send me a photo of the {profile.wake_photo_spot}.", kind="wake")

    if w.phase == "challenge":
        return Outbound(text=f"Photo, not words. The {profile.wake_photo_spot}.", kind="wake")

    if w.phase == "engage":
        substantive = (
            len(text.split()) >= 3 and text.strip().lower() not in _NON_SUBSTANTIVE
        )
        if substantive:
            if w.last_reply_at is not None:
                credit = min(60, int((now - w.last_reply_at).total_seconds()))
            else:
                credit = 0
            w.engaged_seconds += credit
            w.last_reply_at = now
            if w.engaged_seconds >= profile.wakeup_engage_seconds:
                w.phase = "done"
                w.verified = w.verified if w.verified is not None else True
                return Outbound(text="You're up.", kind="wake")
            return None
        return Outbound(text=f"More than that, {profile.name}. What's the first thing you're doing today?", kind="wake")

    return None


def wake_on_photo(
    now: datetime, state: RuntimeState, store: KnowledgeStore, ok: bool | None, reason: str
) -> Outbound:
    w = state.wake
    profile = store.profile()

    if ok is None:
        w.verified = False
        w.phase = "engage"
        w.last_reply_at = now
        return Outbound(
            text="Can't check photos right now, I'll take it. First question: what's the first thing you're doing today?",
            kind="wake",
        )

    if ok:
        w.verified = True
        w.phase = "engage"
        w.last_reply_at = now
        return Outbound(text="Good. Two minutes with me. What's the first thing you're doing today?", kind="wake")

    w.attempts += 1
    if w.attempts >= 3:
        w.verified = False
        w.phase = "engage"
        w.last_reply_at = now
        return Outbound(text="Not convinced, but moving on. What's the first thing you're doing today?", kind="wake")

    return Outbound(text=f"Doesn't look like the {profile.wake_photo_spot}: {reason}. Try again.", kind="wake")
