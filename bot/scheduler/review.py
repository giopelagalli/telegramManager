"""Spaced review: one daily recall session, and a morning digest that re-reads what you learned."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from bot.knowledge.models import Card, Course, hm_to_time
from bot.knowledge.store import KnowledgeStore
from bot.knowledge.views import esc
from bot.scheduler.outbound import Outbound
from bot.scheduler.reminders import is_due
from bot.scheduler.state import RuntimeState
from bot.telegram.markdown import md_to_html

logger = logging.getLogger(__name__)
MINI_SESSION = 3
DIGEST_ITEMS = 3


# -- cards -------------------------------------------------------------------

async def ensure_cards(store: KnowledgeStore, agent, course: str, topic: str, n: int) -> list[Card]:
    """Cards for a topic; generated from the course's sources when there are none yet."""
    have = [c for c in store.cards(course) if c.topic.lower() == topic.lower()]
    if have:
        return have
    from bot.study.select import select_sources
    sources = select_sources(topic, store.sources(course), 60_000)
    made: list[Card] = []
    for q, a in await agent.make_cards(topic, sources, n):
        card = Card(path="", question=q, answer=a, course=course, topic=topic,
                    source=sources[0].path if sources else None)
        store.add_card(card)
        made.append(card)
    if made:
        store.commit(f"cards: {topic} ({len(made)})")
    return made


# -- session -----------------------------------------------------------------

def session_active(state: RuntimeState) -> bool:
    return bool(state.review and state.review.get("current"))


def start_session(now: datetime, store: KnowledgeStore, state: RuntimeState, cards: list[Card],
                  label: str) -> Outbound | None:
    if not cards:
        return None
    state.review = {"queue": [c.path for c in cards[1:]], "current": cards[0].path,
                    "asked_at": now.isoformat(), "right": 0, "again": 0, "label": label}
    return Outbound(f"<b>{esc(label)}</b> — {len(cards)} questions.\n\n{_ask(cards[0])}", kind="review")


def _ask(card: Card) -> str:
    return f"❓ {esc(card.question)}"


async def answer(now: datetime, store: KnowledgeStore, state: RuntimeState, agent, text: str) -> list[Outbound]:
    """Grade the reply to the current card, move it, ask the next one or wrap up."""
    from bot.study.srs import review as sm2
    session = state.review
    try:
        card = store.get_card(session["current"])
    except KeyError:
        state.review = None
        return [Outbound("That card is gone; session over.", kind="review")]
    grade, feedback = await agent.grade(card.question, card.answer, text)
    sm2(card, grade, now.date())
    store.save(card)
    if grade >= 3:
        session["right"] += 1
        mark = "✅"
    else:
        session["again"] += 1
        mark = f"❌ Answer: {esc(card.answer)}"
    line = f"{mark} {esc(feedback)}".strip()

    queue = session["queue"]
    if queue:
        session["current"] = queue.pop(0)
        session["asked_at"] = now.isoformat()
        store.commit(f"review: {card.question[:40]}")
        return [Outbound(f"{line}\n\n{_ask(store.get_card(session['current']))}", kind="review")]
    state.review = None
    store.commit(f"review: {card.question[:40]}")
    return [Outbound(f"{line}\n\nDone: {session['right']} right, {session['again']} to see again.", kind="review")]


def end_session(state: RuntimeState) -> Outbound:
    session = state.review or {}
    state.review = None
    return Outbound(f"Stopped. {session.get('right', 0)} right, {session.get('again', 0)} to see again.", kind="review")


def due_session(now: datetime, store: KnowledgeStore, state: RuntimeState) -> Outbound | None:
    """The daily session at review_time, only when something is due."""
    from bot.study.srs import due_cards
    profile = store.profile()
    day = now.date()
    at = datetime.combine(day, hm_to_time(profile.review_time), tzinfo=profile.tz)
    if not is_due(f"review:{day}", at, now, state):
        return None
    if session_active(state):
        return None
    cap, focus = profile.review_daily_cap, None
    for ev in store.events():
        if ev.kind in ("exam", "quiz") and 0 <= (ev.start.date() - day).days <= profile.exam_focus_days:
            cap, focus = profile.review_exam_cap, ev.course
            break
    cards = due_cards(store.cards(), day, cap, prefer_course=focus)
    return start_session(now, store, state, cards, "Review")


# -- digest ------------------------------------------------------------------

def pick_digest_sources(store: KnowledgeStore, today: date, focus_days: int) -> list:
    """The one surfaced longest ago, the newest, and anything an exam within focus_days covers."""
    sources = [s for s in store.sources() if s.summary.strip()]
    if not sources:
        return []
    picks = []
    stale = sorted(sources, key=lambda s: (s.last_surfaced or date.min, s.path))
    picks.append(stale[0])
    rest = [s for s in sources if s not in picks]
    if rest:
        picks.append(max(rest, key=lambda s: (s.timestamp or datetime.min.replace(tzinfo=None)).isoformat()))
    exams = [e for e in store.events() if e.kind in ("exam", "quiz") and 0 <= (e.start.date() - today).days <= focus_days]
    for e in exams:
        for s in sources:
            if s.course == e.course and s not in picks and any(t.lower() in " ".join(s.topics).lower() for t in e.topics):
                picks.append(s)
                break
    return picks[:DIGEST_ITEMS]


async def due_digest(now: datetime, store: KnowledgeStore, state: RuntimeState, agent) -> Outbound | None:
    profile = store.profile()
    day = now.date()
    if profile.digest_cadence == "weekly" and day.weekday() != 0:
        return None
    at = datetime.combine(day, hm_to_time(profile.digest_time), tzinfo=profile.tz)
    if not is_due(f"digest:{day}", at, now, state):
        return None
    picks = pick_digest_sources(store, day, profile.exam_focus_days)
    if not picks:
        return None
    titles = {c.slug: c.title for c in store.courses()}
    text = await agent.digest([(titles.get(s.course, s.course), s.title, s.summary) for s in picks])
    if not text:
        return None
    for s in picks:
        s.last_surfaced = day
        store.save(s)
    store.commit("digest")
    buttons = [(f"🎯 Quiz me: {(s.topics[0] if s.topics else s.title)[:20]}", f"quiz:{s.course}:{(s.topics[0] if s.topics else s.title)[:40]}") for s in picks]
    return Outbound(md_to_html(text), buttons=buttons, kind="digest")
