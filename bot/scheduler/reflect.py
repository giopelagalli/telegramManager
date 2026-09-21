"""The record: a week in numbers. Assignments on time or late, things finished, study sessions,
weekly goals hit and how many weeks running. Flat and factual either way — that is the point."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from bot.knowledge.ranking import goal_progress
from bot.knowledge.views import esc, fmt_day
from bot.scheduler.outbound import Outbound


def week_of(d: date) -> date:
    return d - timedelta(days=d.weekday())  # Monday


def _period(monday: date) -> str:
    iso = monday.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _due_at(t, tz) -> datetime:
    hh, mm = (int(x) for x in (t.due_time or "23:59").split(":"))
    return datetime.combine(t.due, time(hh, mm), tzinfo=tz)


def week_stats(store, monday: date, now: datetime) -> dict:
    sunday = monday + timedelta(days=6)
    tz = now.tzinfo
    todos = store.todos()
    in_week = lambda d: d is not None and monday <= d <= sunday

    due = [t for t in todos if in_week(t.due)]
    on_time = [t for t in due if t.status == "done" and t.done_at and t.done_at <= _due_at(t, tz)]
    late = [t for t in due if t.status == "done" and t.done_at and t.done_at > _due_at(t, tz)]
    missed = [t for t in due if t.status == "open" and _due_at(t, tz) < now]
    done = [t for t in todos if t.status == "done" and t.done_at and in_week(t.done_at.date())]
    study = [t for t in done if t.title.lower().startswith("study")]

    goals = []
    for g in store.goals():
        if g.status not in ("active", "done") or g.period != _period(monday):
            continue
        d, total = goal_progress(g, todos)
        hit = total > 0 and d >= total
        streak = 0
        if hit:
            streak = 1
            m = monday - timedelta(days=7)
            while True:
                prev = next((x for x in store.goals() if x.title == g.title and x.period == _period(m)), None)
                if prev is None:
                    break
                pd, pt = goal_progress(prev, todos)
                if pt > 0 and pd >= pt:
                    streak += 1
                    m -= timedelta(days=7)
                else:
                    break
        goals.append({"title": g.title, "done": d, "total": total, "hit": hit, "streak": streak})
    return {"monday": monday, "due": due, "on_time": on_time, "late": late, "missed": missed,
            "done": done, "study": study, "goals": goals}


def render_week(stats: dict, now: datetime, tz=None) -> str:
    tz = tz or now.tzinfo
    monday = stats["monday"]
    lines = [f"<b>Week of {fmt_day(monday)}</b>"]
    if stats["due"]:
        n = len(stats["due"])
        part = f"• Assignments: <b>{len(stats['on_time'])} of {n}</b> on time"
        if stats["late"]:
            worst = max(stats["late"], key=lambda t: t.done_at - _due_at(t, tz))
            days = max(1, round((worst.done_at - _due_at(worst, tz)).total_seconds() / 86400))
            part += f", {len(stats['late'])} late ({esc(worst.title)}, {days}d)"
        if stats["missed"]:
            part += f", {len(stats['missed'])} not done"
        lines.append(part + ".")
    lines.append(f"• Done: <b>{len(stats['done'])}</b>" + (f", {len(stats['study'])} study sessions" if stats["study"] else "") + ".")
    for g in stats["goals"]:
        if g["hit"]:
            run = f", {g['streak']} weeks running" if g["streak"] > 1 else ""
            lines.append(f"• {esc(g['title'])}: <b>hit</b>{run}.")
        elif g["total"]:
            lines.append(f"• {esc(g['title'])}: <b>{g['done']} of {g['total']}</b>. Streak's gone." if g["done"] < g["total"] else "")
        else:
            lines.append(f"• {esc(g['title'])}: nothing toward it.")
    return "\n".join(l for l in lines if l)


def reflect_text(store, now: datetime, weeks_back: int = 4) -> str:
    this_monday = week_of(now.date())
    blocks = [render_week(week_stats(store, this_monday, now), now)]
    history = []
    for k in range(1, weeks_back + 1):
        m = this_monday - timedelta(days=7 * k)
        s = week_stats(store, m, now)
        if not s["due"] and not s["done"] and not s["goals"]:
            continue
        bits = []
        if s["due"]:
            bits.append(f"{len(s['on_time'])}/{len(s['due'])} on time")
        bits.append(f"{len(s['done'])} done")
        for g in s["goals"]:
            bits.append(f"{g['title']} {'hit' if g['hit'] else f'{g['done']}/{g['total']}'}")
        history.append(f"• {fmt_day(m)}: " + ", ".join(bits))
    if history:
        blocks.append("<b>Before that</b>\n" + "\n".join(history))
    return "\n\n".join(blocks)


REFLECT_TIME = "20:00"  # Sunday


def due_reflection(now: datetime, store, state, agent) -> Outbound | None:
    """Sunday evening: the week in numbers, once."""
    from bot.scheduler.reminders import is_due
    if now.weekday() != 6:
        return None
    hh, mm = (int(x) for x in REFLECT_TIME.split(":"))
    when = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    key = f"reflect:{now.date()}"
    if not is_due(key, when, now, state):
        return None
    stats = week_stats(store, week_of(now.date()), now)
    return Outbound(render_week(stats, now), kind="briefing")
