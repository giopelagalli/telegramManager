from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.knowledge.models import Goal, Todo
from bot.knowledge.store import KnowledgeStore
from bot.scheduler.reflect import due_reflection, reflect_text, render_week, week_of, week_stats
from bot.scheduler.state import RuntimeState

NY = ZoneInfo("America/New_York")
SUN = datetime(2026, 9, 20, 20, 0, tzinfo=NY)  # Sunday of ISO week 38


def _store(tmp_path):
    s = KnowledgeStore(tmp_path / "k", clock=lambda: SUN); s.init()
    prev_path = s.add(Goal(path="", title="Gym 3x", period="2026-W37"))
    this_path = s.add(Goal(path="", title="Gym 3x", period="2026-W38"))
    for i, d in enumerate((14, 16, 18)):  # this week, all done
        s.add(Todo(path="", title=f"Gym {i}", priority=2, goal=this_path, status="done", done_at=datetime(2026, 9, d, 18, 0, tzinfo=NY)))
    for i, d in enumerate((8, 10)):  # last week, all done → streak of 2
        s.add(Todo(path="", title=f"Gym prev {i}", priority=2, goal=prev_path, status="done", done_at=datetime(2026, 9, d, 18, 0, tzinfo=NY)))
    s.add(Todo(path="", title="CSCI 2670 hw", priority=1, due=date(2026, 9, 15), due_time="23:59", course="csci-2670",
               status="done", done_at=datetime(2026, 9, 15, 22, 0, tzinfo=NY)))
    s.add(Todo(path="", title="Spanish hw", priority=2, due=date(2026, 9, 16), status="done", done_at=datetime(2026, 9, 18, 12, 0, tzinfo=NY)))
    s.add(Todo(path="", title="Lab report", priority=2, due=date(2026, 9, 19)))  # still open, past due
    s.add(Todo(path="", title="Study: chapters 1-3", priority=2, status="done", done_at=datetime(2026, 9, 17, 12, 0, tzinfo=NY)))
    s.commit("seed")
    return s


def test_week_in_numbers(tmp_path):
    s = _store(tmp_path)
    text = render_week(week_stats(s, week_of(SUN.date()), SUN), SUN)
    assert text.splitlines() == [
        "<b>Week of Mon Sep 14</b>",
        "• Assignments: <b>1 of 3</b> on time, 1 late (Spanish hw, 2d), 1 not done.",
        "• Done: <b>6</b>, 1 study sessions.",
        "• Gym 3x: <b>hit</b>, 2 weeks running.",
    ]


def test_a_bad_week_reads_flat(tmp_path):
    s = _store(tmp_path)
    for t in s.todos():
        if t.title.startswith("Gym 2"):
            t.status = "open"; t.done_at = None; s.save(t)
    s.commit("undo one gym")
    text = render_week(week_stats(s, week_of(SUN.date()), SUN), SUN)
    assert "• Gym 3x: <b>2 of 3</b>. Streak's gone." in text


def test_reflect_has_history_and_sunday_sends_once(tmp_path):
    s = _store(tmp_path)
    text = reflect_text(s, SUN)
    assert "<b>Before that</b>" in text and "• Mon Sep 7: 2 done, Gym 3x hit" in text
    st = RuntimeState.load(Path("/nonexistent"))
    assert due_reflection(SUN.replace(hour=19), s, st, None) is None
    out = due_reflection(SUN, s, st, None)
    assert out and out.kind == "briefing" and out.text.startswith("<b>Week of Mon Sep 14</b>")
    assert due_reflection(SUN.replace(minute=5), s, st, None) is None
    assert due_reflection(datetime(2026, 9, 21, 20, 0, tzinfo=NY), s, st, None) is None  # Monday: nothing
