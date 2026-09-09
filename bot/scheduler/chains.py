from __future__ import annotations

from datetime import datetime, timedelta

from bot.agent.prompts import build_context
from bot.knowledge.models import Profile
from bot.knowledge.store import KnowledgeStore
from bot.scheduler.outbound import Outbound
from bot.scheduler.state import RuntimeState
from bot.telegram.markdown import md_to_html

_FALLBACKS = [
    "{name}, still there? {item} is the one thing right now.",
    "{name}, quick one: {item}. Yes or no?",
    "{name}, you said this week was about {goal}. {item} moves it.",
    "{name}, last nudge on {item}. I'll leave you alone until the next check-in.",
]


def close_chain(state: RuntimeState) -> None:
    state.chain = None


def chain_gaps(profile: Profile, kind: str) -> list[int]:
    gaps = profile.followup_gaps_minutes
    if kind == "checkin":
        return gaps[:2]
    return gaps[:4]


async def due_followup(now: datetime, store: KnowledgeStore, state: RuntimeState, agent) -> Outbound | None:
    chain = state.chain
    if chain is None:
        return None

    profile = store.profile()
    start, end = profile.waking_window(now.date())
    if now < start or now >= end:
        close_chain(state)
        return None

    gaps = chain_gaps(profile, chain.kind)
    if chain.step >= len(gaps):
        close_chain(state)
        return None

    if now < chain.last_sent_at + timedelta(minutes=gaps[chain.step]):
        return None

    goals = [g for g in store.goals() if g.status == "active"]
    if chain.step == 2 and not goals:
        fallback = "{name}, still on: {item}. Small step now?".format(name=profile.name, item=chain.item)
    else:
        goal = goals[0].title if goals else ""
        fallback = _FALLBACKS[chain.step].format(name=profile.name, item=chain.item, goal=goal)

    context = build_context(store, now)
    context += f"\nFollow-up step: {chain.step}\nItem: {chain.item}\nHistory: {chain.history}"

    text = md_to_html(await agent.compose("followup", context, fallback))

    chain.step += 1
    chain.last_sent_at = now
    chain.history.append(text)

    if chain.step >= len(gaps):
        close_chain(state)

    return Outbound(text, silent=True, kind="followup")
