"""/courses: each class with its weekly slots, its tests and its assignments, and buttons to add
either. A test is an event of kind exam/quiz, which also builds the study plan."""
from __future__ import annotations

from datetime import datetime

from bot.knowledge.store import _WEEKDAYS
from datetime import date, timedelta

from bot.knowledge.views import esc, fmt_clock, fmt_day, fmt_time
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


def _relative(d: date, today: date) -> str:
    if d == today:
        return "today"
    if d == today + timedelta(days=1):
        return "tomorrow"
    if d < today:
        return "overdue"
    if d - today <= timedelta(days=6):
        return d.strftime("%a")
    return fmt_day(d)


def next_due(store, course, now: datetime) -> str:
    """One line: the soonest assignment or test for a course, plus how many more are behind it."""
    today = now.date()
    items = []
    for t in todo_ui.items(store, f"course:{course.slug}", now):
        if t.due is not None:
            when = _relative(t.due, today) + (f" {fmt_clock(t.due_time)}" if t.due_time else "")
            items.append((t.due, t.due_time or "24:00", f"{esc(t.title)} due <b>{when}</b>"))
    for e in _tests(store, course, now):
        items.append((e.start.date(), e.start.strftime("%H:%M"), f"{esc(e.title)} <b>{_relative(e.start.date(), today)}</b>"))
    if not items:
        return "nothing due"
    items.sort(key=lambda x: (x[0], x[1]))
    more = f" (+{len(items) - 1} more)" if len(items) > 1 else ""
    return items[0][2] + more


def list_view(store, message_id: int | None = None, toast: str | None = None, now: datetime | None = None) -> Outbound:
    courses = store.courses()
    now = now or datetime.now()
    lines = ["<b>Courses</b>"] + ([f"• <b>{esc(c.title)}</b> — {next_due(store, c, now)}" for c in courses] or ["None yet."])
    buttons = [(c.title[:40], f"course:view:{c.slug}") for c in courses] + [("➕ Course", "course:add")]
    return Outbound("\n".join(lines), buttons=buttons, kind="edit" if message_id else "reply", edit_message_id=message_id, toast=toast)


def course_view(store, now: datetime, slug: str, message_id: int | None = None, toast: str | None = None) -> Outbound:
    course = _course(store, slug)
    if course is None:
        return list_view(store, message_id, now=now)
    lines = [f"<b>{esc(course.title)}</b>"]
    slots = _slots(store, course)
    for e in slots:
        days = "/".join(DAY[d] for d in _WEEKDAYS if d in e.repeat_days)
        span = fmt_time(e.start) + (f"–{fmt_time(e.end)}" if e.end else "")
        lines.append(f"• <b>{days} {span}</b>" + (f" · {esc(e.location)}" if e.location else ""))
    if not slots:
        lines.append("• No weekly slot yet (/schedule).")
    tests = _tests(store, course, now)
    buttons = []
    if tests:
        lines.append("")
        lines.append("<b>Tests</b>")
        for i, e in enumerate(tests):
            lines.append(f"• <b>{fmt_day(e.start.date())}</b> — {esc(e.title)}")
            buttons.append((f"{fmt_day(e.start.date())} · {e.title}"[:40], f"course:test:{slug}:{i}"))
    todos = todo_ui.items(store, f"course:{slug}", now)
    if todos:
        lines.append("")
        lines.append("<b>Assignments</b>")
        for i, t in enumerate(todos):
            when = todo_ui._when(t, now.date())
            lines.append(f"• <b>{when}</b> — {esc(t.title)}" if when else f"• {esc(t.title)}")
            buttons.append((f"{when + ' · ' if when else ''}{t.title}"[:40], f"todo:course:{slug}:e:{i}"))
    buttons += [("➕ Assignment", f"todo:course:{slug}:add"), ("➕ Test", f"course:addtest:{slug}"),
                ("📋 Due list", f"todo:course:{slug}:list"), ("✔ Turned in", f"course:done:{slug}"), ("◀ Courses", "course:list")]
    return Outbound("\n".join(lines), buttons=buttons, kind="edit" if message_id else "reply", edit_message_id=message_id, toast=toast)


def done_view(store, now: datetime, slug: str, message_id: int | None = None) -> Outbound:
    course = _course(store, slug)
    if course is None:
        return list_view(store, message_id, now=now)
    done = sorted((t for t in store.todos() if t.course == slug and t.status == "done"),
                  key=lambda t: t.done_at or now, reverse=True)
    lines = [f"<b>{esc(course.title)} — turned in</b>"] + (
        [f"• <b>{fmt_day(t.done_at.date()) if t.done_at else '?'}</b> — {esc(t.title)}" for t in done[:20]] or ["• Nothing yet."])
    return Outbound("\n".join(lines), buttons=[("◀ Back", f"course:view:{slug}")], kind="edit" if message_id else "reply", edit_message_id=message_id)


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
        return [list_view(store, message_id, now=now)]
    if what == "add":
        state.pending_schedule = {"ui": "course", "mode": "add_course"}
        return [Outbound("Course name? Like \"CSCI 2670\" or \"Spanish\".", kind="reply")]
    if what == "view" and len(parts) > 1:
        return [course_view(store, now, parts[1], message_id)]
    if what == "done" and len(parts) > 1:
        return [done_view(store, now, parts[1], message_id)]
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
    return [list_view(store, message_id, now=now)]


def hint(pending: dict, text: str) -> str:
    slug = pending.get("course", "")
    return (f"[Adding a test for course \"{slug}\": one add_event with kind exam or quiz, course \"{slug}\", "
            f"`start`, and `topics` if given; the study plan is made automatically] {text}")
