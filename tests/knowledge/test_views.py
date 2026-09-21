from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from bot.knowledge.models import Todo, Event, Goal, Profile
from bot.knowledge.views import render_todo, render_today, render_week, render_goals, render_backlog

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 3, 14, 0, tzinfo=NY)
GOLD = Path(__file__).parent / "golden"

def check(name, text):
    f = GOLD / f"{name}.txt"
    if not f.exists():
        f.write_text(text); raise AssertionError(f"wrote golden {name}; re-run")
    assert text == f.read_text()

def todos():
    return [
        Todo(path="todos/2026-09-01-dentist.md", title="Call dentist", priority=1, due=date(2026, 9, 1)),
        Todo(path="todos/2026-09-02-taxes.md", title="File <taxes>", priority=2, due=date(2026, 9, 3)),
        Todo(path="todos/2026-09-02-read.md", title="Read paper", priority=3),
        Todo(path="todos/2026-09-02-done.md", title="Old", status="done"),
    ]

def events():
    return [
        Event(path="schedule/2026-09-03-gym.md", title="Gym", start=datetime(2026, 9, 3, 18, 0, tzinfo=NY),
              end=datetime(2026, 9, 3, 19, 0, tzinfo=NY), location="Equinox", travel_minutes=20),
        Event(path="schedule/2026-09-04-dr.md", title="Doctor", start=datetime(2026, 9, 4, 9, 30, tzinfo=NY), importance="critical"),
        Event(path="schedule/2026-09-12-far.md", title="Far away", start=datetime(2026, 9, 12, 9, 30, tzinfo=NY)),
    ]

def test_render_todo_top5_escapes_html():
    check("todo", render_todo(todos(), NOW.date()))

def test_render_today_has_leave_by():
    check("today", render_today(events(), todos(), Profile(), NOW))

def test_render_week_groups_days_and_marks_free():
    check("week", render_week(events(), Profile(), NOW))

def test_render_goals_progress():
    goals = [Goal(path="goals/2026-W36-health.md", title="Health week", period="2026-W36"),
             Goal(path="goals/2026-ship.md", title="Ship app", period="2026")]
    ts = todos(); ts[0].goal = "../goals/2026-W36-health.md"; ts[3].goal = "../goals/2026-W36-health.md"
    check("goals", render_goals(goals, ts, NOW.date()))

def test_render_backlog_empty():
    assert "empty" in render_backlog([]).lower()


def test_week_has_bullets_bold_times_and_due_with_clock():
    from bot.knowledge.models import Todo
    ts = [Todo(path="todos/2026-09-01-a.md", title="Discrete math assignment", priority=1, due=date(2026, 9, 3), due_time="23:59"),
          Todo(path="todos/2026-09-01-b.md", title="Read chapter", priority=2, due=date(2026, 9, 4))]
    text = render_week(events(), Profile(), NOW, ts)
    thu = text.split("<b>Friday, September 4</b>")[0]
    assert "• <b>6:00pm–7:00pm</b> Gym — leave by 5:40pm" in thu
    assert "• <b>Due 11:59pm:</b> Discrete math assignment" in thu
    assert "• <b>Due:</b> Read chapter" in text and "• free" in text
    assert "due <b>Thu Sep 3, 11:59pm</b>" in render_todo(ts, date(2026, 9, 1))
