"""/courses: each class with its weekly slots, its tests and its assignments, and buttons to add
either. A test is an event of kind exam/quiz, which also builds the study plan."""
from __future__ import annotations

from datetime import datetime

from bot.knowledge.store import _WEEKDAYS
from bot.knowledge.views import esc, fmt_day, fmt_time
from bot.scheduler.outbound import Outbound
from bot.telegram import todo_ui

DAY = {"MO": "Mon", "TU": "Tue", "WE": "Wed", "TH": "Thu", "FR": "Fri", "SA": "Sat", "SU": "Sun"}


def _course(store, slug: str):
    return next((c for c in store.courses() if c.slug == slug), None)


def _slots(store, course) -> list:
    title = course.title.lower()
    return sorted((e for e in store.series() if e.course == course.slug or e.title.lower() == title),
                  key=lambda e: (_WEEKDAYS.index(next((d for d in _WEEKDAYS if d in e.repeat_days), "MO")), e.start.hour, e.start.minute))


def _tests(store, course, now: datetime) -> list:
    return sorted((e for e in store.events() if e.kind in ("exam", "quiz") and e.course == course.slug
                   and e.status == "upcoming" and e.start >= now), key=lambda e: e.start)


def list_view(store, message_id: int | None = None, toast: str | None = None) -> Outbound:
    courses = store.courses()
    lines = ["<b>Courses</b>"] + ([esc(c.title) for c in courses] or ["None yet."])
    buttons = [(c.title[:40], f"course:view:{c.slug}") for c in courses] + [("➕ Course", "course:add")]
    return Outbound("\n".join(lines), buttons=buttons, kind="edit" if message_id else "reply", edit_message_id=message_id, toast=toast)


def course_view(store, now: datetime, slug: str, message_id: int | None = None, toast: str | None = None) -> Outbound:
    course = _course(store, slug)
    if course is None:
        return list_view(store, message_id)
    lines = [f"<b>{esc(course.title)}</b>"]
    slots = _slots(store, course)
    for e in slots:
        days = "/".join(DAY[d] for d in _WEEKDAYS if d in e.repeat_days)
        span = fmt_time(e.start) + (f"–{fmt_time(e.end)}" if e.end else "")
        lines.append(f"{days} {span}" + (f" · {esc(e.location)}" if e.location else ""))
    if not slots:
        lines.append("No weekly slot yet (/schedule).")
    tests = _tests(store, course, now)
    buttons = []
    if tests:
        lines.append("")
        lines.append("<b>Tests</b>")
        for i, e in enumerate(tests):
            lines.append(f"{fmt_day(e.start.date())} — {esc(e.title)}")
            buttons.append((f"{fmt_day(e.start.date())} · {e.title}"[:40], f"course:test:{slug}:{i}"))
    todos = todo_ui.items(store, f"course:{slug}", now)
    if todos:
        lines.append("")
        lines.append("<b>Assignments</b>")
        for i, t in enumerate(todos):
            when = todo_ui._when(t, now.date())
            lines.append(f"{when + ' — ' if when else ''}{esc(t.title)}")
            buttons.append((f"{when + ' · ' if when else ''}{t.title}"[:40], f"todo:course:{slug}:e:{i}"))
    buttons += [("➕ Assignment", f"todo:course:{slug}:add"), ("➕ Test", f"course:addtest:{slug}"), ("◀ Courses", "course:list")]
    return Outbound("\n".join(lines), buttons=buttons, kind="edit" if message_id else "reply", edit_message_id=message_id, toast=toast)


def test_view(store, now: datetime, slug: str, index: int, message_id: int | None = None) -> Outbound:
    course = _course(store, slug)
    tests = _tests(store, course, now) if course else []
    if index < 0 or index >= len(tests):
        return course_view(store, now, slug, message_id)
    e = tests[index]
    lines = [f"<b>{esc(e.title)}</b>", f"{fmt_day(e.start.date())} {fmt_time(e.start)}"]
    if e.topics:
        lines.append("Topics: " + esc(", ".join(e.topics)))
    buttons = [("🗑 Remove", f"course:rmtest:{slug}:{index}"), ("◀ Back", f"course:view:{slug}")]
    return Outbound("\n".join(lines), buttons=buttons, kind="edit" if message_id else "reply", edit_message_id=message_id)


def handle(arg: str, store, state, now: datetime, message_id: int | None) -> list[Outbound]:
    parts = arg.split(":")
    what = parts[0]
    if what == "list":
        return [list_view(store, message_id)]
    if what == "add":
        state.pending_schedule = {"ui": "course", "mode": "add_course"}
        return [Outbound("Course name? Like \"CSCI 2670\" or \"Spanish\".", kind="reply")]
    if what == "view" and len(parts) > 1:
        return [course_view(store, now, parts[1], message_id)]
    if what == "test" and len(parts) > 2 and parts[2].isdigit():
        return [test_view(store, now, parts[1], int(parts[2]), message_id)]
    if what == "rmtest" and len(parts) > 2 and parts[2].isdigit():
        course = _course(store, parts[1])
        tests = _tests(store, course, now) if course else []
        if int(parts[2]) < len(tests):
            e = tests[int(parts[2])]
            store.delete(e.path)
            store.commit(f"test removed: {e.title}")
            return [course_view(store, now, parts[1], message_id, toast=f"Removed {e.title}")]
        return [course_view(store, now, parts[1], message_id)]
    if what == "addtest" and len(parts) > 1:
        state.pending_schedule = {"ui": "course", "mode": "add_test", "course": parts[1]}
        return [Outbound("Test or quiz: what, when, and the topics if you know them. One line, like "
                         "\"midterm Oct 14 9am, chapters 1-5\".", kind="reply")]
    return [list_view(store, message_id)]


def hint(pending: dict, text: str) -> str:
    slug = pending.get("course", "")
    return (f"[Adding a test for course \"{slug}\": one add_event with kind exam or quiz, course \"{slug}\", "
            f"`start`, and `topics` if given; the study plan is made automatically] {text}")
