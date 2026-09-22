"""Study plans for exams: a day-by-day plan and one study todo per day.

An exam gets planned when it is added inside the horizon, or on the morning it enters it, so a
syllabus with three exams doesn't drop a semester of study todos on the list at once."""

from __future__ import annotations

import logging
from datetime import datetime

from bot.knowledge.models import Event, Todo
from bot.knowledge.views import esc
from bot.telegram.markdown import md_to_html

logger = logging.getLogger(__name__)

PLAN_HORIZON_DAYS = 21


def days_left(exam: Event, now: datetime) -> int:
    return (exam.start.date() - now.date()).days


def is_planned(store, exam: Event, now: datetime) -> bool:
    """A plan shows as study todos for the course due between today and the exam."""
    return any(
        t.kind == "study" and t.course == exam.course and t.due is not None
        and now.date() <= t.due < exam.start.date()
        for t in store.todos()
    )


def entering_horizon(store, now: datetime) -> list[Event]:
    """Upcoming exams inside the horizon that have no plan yet."""
    return [
        e for e in store.events()
        if e.kind in ("exam", "quiz") and e.status == "upcoming"
        and 1 <= days_left(e, now) <= PLAN_HORIZON_DAYS and not is_planned(store, e, now)
    ]


async def plan_exam(store, agent, exam: Event, now: datetime, search=None) -> str:
    """Draft the plan, add the study todos, and return the lines to show."""
    left = days_left(exam, now)
    profile = store.profile()
    sources = store.sources(exam.course) if exam.course else store.sources()
    lookup = None
    if not sources and search is not None:
        course_title = next((c.title for c in store.courses() if c.slug == exam.course), exam.course or "")
        lookup = await search.search(f"{course_title} {' '.join(exam.topics)} key concepts syllabus".strip())
    plan = await agent.plan_exam(exam, sources, left, profile.study_daily_minutes, lookup=lookup)
    if plan is None or not plan["days"]:
        return esc(f"{left} days until {exam.title}. Couldn't draft a plan right now; ask me again in a bit.")
    total = 0
    for d in plan["days"]:
        if d["date"] < now.date() or d["date"] >= exam.start.date():
            continue
        store.add(Todo(path="", title=f"Study: {d['task'][:80]}", priority=1, due=d["date"],
                       course=exam.course, kind="study"))
        total += d["minutes"]
    store.commit(f"plan: {exam.title}")
    per_day = round(total / max(1, len(plan["days"])))
    head = f"<b>Plan for {esc(exam.title)}</b> — {left} days, about {per_day} min/day"
    body = "\n".join(f"{d['date']:%a %b %d}: {esc(d['task'])} ({d['minutes']} min)" for d in plan["days"])
    advice = md_to_html(plan["advice"]) if plan["advice"] else ""
    return "\n".join(x for x in (head, body, advice) if x)


def later_line(exam: Event, now: datetime) -> str:
    left = days_left(exam, now)
    return esc(f"{exam.title} is {left} days out; I'll plan it when it's {PLAN_HORIZON_DAYS} days away.")
