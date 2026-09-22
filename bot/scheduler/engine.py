from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

from bot.scheduler.checkins import due_sprint
from bot.scheduler import briefings, chains, checkins, critical, reflect, review, trackers
from bot.study import plans
from bot.scheduler.budget import budget_ok, record_send
from bot.scheduler.outbound import Outbound
from bot.scheduler.reminders import LATE_WINDOW, MISSED_AFTER, due_reminders

logger = logging.getLogger(__name__)

PENDING_VERIFY_TTL = timedelta(minutes=10)
SENT_TIMES_CAP = 200
PLANS_AFTER_HOUR = 8  # exam plans are drafted in the morning, not at midnight
BACKEND_PROBE_EVERY = timedelta(seconds=60)
SPARK_DOWN_TEXT = (
    "Spark's down. Running on the backup until it's back — same as /hard, so everything works "
    "except voice. Replies start with ☁️ meanwhile."
)
SPARK_UP_TEXT = "Spark's back."


class Engine:
    """Runs one deterministic pass over every scheduled job per tick."""

    def __init__(self, store, agent, state, state_path: Path, clock, sender, maps, search=None):
        self.store = store
        self.agent = agent
        self.state = state
        self.state_path = Path(state_path)
        self.clock = clock
        self.sender = sender
        self.maps = maps
        self.search = search
        self.sent_times: list[datetime] = []
        self._last_probe: datetime | None = None

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
            self._backend,
            self._reminders,
            self._tracker_reminders,
            self._sprint,
            self._critical_leave,
            self._briefings,
            self._plans,
            self._reflect,
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
        """Drop jobs that are too late to fire and clear stale critical state."""
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

    # -- steps -----------------------------------------------------------

    async def _prune(self, now: datetime, sent: list[Outbound]) -> None:
        self.state.prune(now)
        key = f"materialize:{now.date()}"
        if key not in self.state.fired:
            self.state.fired.add(key)
            if self.store.materialize(now):
                self.store.commit("schedule: materialize weekly events")

    async def _backend(self, now: datetime, sent: list[Outbound]) -> None:
        """Notice the primary model going down or coming back, and say so once each way."""
        probe = getattr(self.agent.client, "probe", None)
        if probe is None:
            return
        if self._last_probe is not None and now < self._last_probe + BACKEND_PROBE_EVERY:
            return
        self._last_probe = now
        down = not await probe()
        if down == self.state.backend_down:
            return
        self.state.backend_down = down
        await self._send(Outbound(SPARK_DOWN_TEXT if down else SPARK_UP_TEXT, kind="reply"), now, sent)

    async def _reminders(self, now: datetime, sent: list[Outbound]) -> None:
        for out in await due_reminders(now, self.store, self.state, self.maps):
            await self._send(out, now, sent)

    async def _tracker_reminders(self, now: datetime, sent: list[Outbound]) -> None:
        for out in trackers.due_tracker_reminders(now, self.store, self.state):
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

    async def _briefings(self, now: datetime, sent: list[Outbound]) -> None:
        for out in await briefings.due_briefings(now, self.store, self.state, self.agent):
            await self._send(out, now, sent)

    async def _plans(self, now: datetime, sent: list[Outbound]) -> None:
        """An exam entering the study horizon gets its plan, once a day, in the morning."""
        key = f"plans:{now.date()}"
        if key in self.state.fired or now.hour < PLANS_AFTER_HOUR:
            return
        if self.state.pause_until is not None and now < self.state.pause_until:
            return
        self.state.fired.add(key)
        for exam in plans.entering_horizon(self.store, now):
            text = await plans.plan_exam(self.store, self.agent, exam, now, search=self.search)
            await self._send(Outbound(text, kind="plan"), now, sent)

    async def _reflect(self, now: datetime, sent: list[Outbound]) -> None:
        out = reflect.due_reflection(now, self.store, self.state, self.agent)
        if out is not None:
            await self._send(out, now, sent)

    async def _review(self, now: datetime, sent: list[Outbound]) -> None:
        if self.state.pause_until and now < self.state.pause_until:
            return
        out = review.due_session(now, self.store, self.state)
        if out is not None:
            await self._send(out, now, sent)

    async def _digest(self, now: datetime, sent: list[Outbound]) -> None:
        if self.state.pause_until and now < self.state.pause_until:
            return
        out = await review.due_digest(now, self.store, self.state, self.agent)
        if out is not None:
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
        # Persist first: the fired key is already set, so a crash mid-send (voice synthesis
        # running out of memory, say) loses one message instead of repeating it on restart.
        try:
            self.state.save(self.state_path)
        except Exception:
            logger.exception("failed to save runtime state to %s", self.state_path)
        await self.sender.send(out)
        self.sent_times.append(now)
        del self.sent_times[:-SENT_TIMES_CAP]
        sent.append(out)
