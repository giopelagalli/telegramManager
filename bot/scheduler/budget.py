from __future__ import annotations

from datetime import datetime, timedelta

from bot.knowledge.models import Profile
from bot.scheduler.state import RuntimeState


def budget_ok(now: datetime, state: RuntimeState, profile: Profile) -> bool:
    state.proactive_sends = [t for t in state.proactive_sends if now - t <= timedelta(hours=1)]
    return len(state.proactive_sends) < profile.proactive_budget_per_hour


def record_send(now: datetime, state: RuntimeState) -> None:
    state.proactive_sends.append(now)
