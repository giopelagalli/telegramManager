from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

from bot.scheduler.checkins import due_sprint
from bot.scheduler import briefings, chains, checkins, critical
from bot.scheduler.budget import budget_ok, record_send
from bot.scheduler.outbound import Outbound
from bot.scheduler.reminders import LATE_WINDOW, MISSED_AFTER, due_reminders

logger = logging.getLogger(__name__)

PENDING_VERIFY_TTL = timedelta(minutes=10)
SENT_TIMES_CAP = 200


class Engine:
    """Runs one deterministic pass over every scheduled job per tick."""

    def __init__(self, store, agent, state, state_path: Path, clock, sender, maps):
        self.store = store
        self.agent = agent
        self.state = state
        self.state_path = Path(state_path)
        self.clock = clock
        self.sender = sender
        self.maps = maps
        self.sent_times: list[datetime] = []

    # -- hooks -----------------------------------------------------------

    def escalate_external(self, reason: str) -> None:
        """Out-of-band escalation (spec §20). No channel wired up yet."""
        logger.warning("external escalation: %s", reason)

    # -- loop ------------------------------------------------------------

    async def run(self, interval_seconds: int = 10) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("tick failed")
            await asyncio.sleep(interval_seconds)

    async def tick(self) -> list[Outbound]:
        now = self.clock.now()
        sent: list[Outbound] = []

        for step in (
            self._prune,
            self._reminders,
            self._sprint,
            self._critical_leave,
            self._wake,
            self._briefings,
            self._checkin,
            self._followup,
            self._expire_pending_verify,
        ):
            try:
                await step(now, sent)
            except Exception:
                logger.exception("scheduler step %s failed", step.__name__)

        try:
            self.state.save(self.state_path)
        except Exception:
            logger.exception("failed to save runtime state to %s", self.state_path)

        return sent

    def startup(self, now: datetime) -> None:
        """Drop jobs that are too late to fire and clear stale critical/wake state."""
        profile = self.store.profile()

        for ev in self.store.events():
            if ev.status != "upcoming":
                continue
            times = ev.times(profile)
            jobs = (
                (f"get_ready:{ev.path}", times.get_ready_at),
                (f"leave:{ev.path}", times.leave_at),
                (f"missed:{ev.path}", ev.start + MISSED_AFTER),
            )
            for key, when in jobs:
                if now > when + LATE_WINDOW:
                    self.state.fired.add(key)

        crit = self.state.critical
        if crit is not None:
            ev = next((e for e in self.store.events() if e.path == crit.event_path), None)
            cap = timedelta(minutes=profile.critical_leave_cap_minutes)
            if ev is None or ev.status != "upcoming" or now >= ev.times(profile).leave_by + cap:
                self.state.critical = None

        if self.state.wake is not None and self.state.wake.day != now.date().isoformat():
            self.state.wake = None

    # -- steps -----------------------------------------------------------

    async def _prune(self, now: datetime, sent: list[Outbound]) -> None:
        self.state.prune(now)
        key = f"materialize:{now.date()}"
        if key not in self.state.fired:
            self.state.fired.add(key)
            if self.store.materialize(now):
                self.store.commit("schedule: materialize weekly events")

    async def _reminders(self, now: datetime, sent: list[Outbound]) -> None:
        for out in await due_reminders(now, self.store, self.state, self.maps):
            await self._send(out, now, sent)

    async def _sprint(self, now: datetime, sent: list[Outbound]) -> None:
        out = due_sprint(now, self.state)
        if out is not None:
            await self._send(out, now, sent)

    async def _critical_leave(self, now: datetime, sent: list[Outbound]) -> None:
        if self.state.critical is None:
            return
        out = critical.leave_tick(now, self.state, self.store)
        if out is None:
            return
        await self._send(out, now, sent)
        if self.state.critical is None:
            self.escalate_external("critical leave hit its cap")

    async def _wake(self, now: datetime, sent: list[Outbound]) -> None:
        profile = self.store.profile()
        if critical.wake_due(now, self.state, self.store):
            await self._send(critical.wake_start(now, self.state, self.store), now, sent)
        elif self.state.wake is not None and self.state.wake.phase != "done":
            out = critical.wake_tick(now, self.state, self.store)
            if out is not None:
                await self._send(out, now, sent)
            if self.state.wake.phase == "done":
                self.escalate_external("wake-up hit its cap")
        elif self.state.wake is None and profile.wake_time:
            await self._missed_wake_morning(now, profile, sent)

        wake = self.state.wake
        if wake is not None and wake.phase == "done":
            note = None if wake.verified else "Wake-up not verified."
            out = await briefings.send_morning(now, self.store, self.state, self.agent, note)
            await self._send(out, now, sent)
            self.state.wake = None

    async def _missed_wake_morning(self, now: datetime, profile, sent: list[Outbound]) -> None:
        """Started after the wake window: the morning briefing would be lost otherwise."""
        day = now.date()
        guard_key = f"wake-missed:{day}"
        if guard_key in self.state.fired or f"morning:{day}" in self.state.fired:
            return
        hh, mm = (int(x) for x in profile.wake_time.split(":"))
        wake_at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if now <= wake_at + LATE_WINDOW:
            return
        self.state.fired.add(guard_key)
        out = await briefings.send_morning(
            now, self.store, self.state, self.agent, "Wake-up window missed."
        )
        await self._send(out, now, sent)

    async def _briefings(self, now: datetime, sent: list[Outbound]) -> None:
        for out in await briefings.due_briefings(now, self.store, self.state, self.agent):
            await self._send(out, now, sent)

    async def _checkin(self, now: datetime, sent: list[Outbound]) -> None:
        profile = self.store.profile()
        if not budget_ok(now, self.state, profile):
            # The slot is spent either way, but don't pay the model for it.
            checkins.consume_checkin_slot(now, self.state, profile)
            return
        out = await checkins.due_checkin(now, self.store, self.state, self.agent)
        if out is None:
            return
        await self._send(out, now, sent)
        record_send(now, self.state)

    async def _followup(self, now: datetime, sent: list[Outbound]) -> None:
        if self.state.pause_until is not None and now < self.state.pause_until:
            return
        if not budget_ok(now, self.state, self.store.profile()):
            return
        out = await chains.due_followup(now, self.store, self.state, self.agent)
        if out is None:
            return
        await self._send(out, now, sent)
        record_send(now, self.state)

    async def _expire_pending_verify(self, now: datetime, sent: list[Outbound]) -> None:
        pending = self.state.pending_verify
        if pending is not None and now - pending.asked_at > PENDING_VERIFY_TTL:
            self.state.pending_verify = None

    # -- helpers ---------------------------------------------------------

    async def _send(self, out: Outbound, now: datetime, sent: list[Outbound]) -> None:
        await self.sender.send(out)
        self.sent_times.append(now)
        del self.sent_times[:-SENT_TIMES_CAP]
        sent.append(out)
