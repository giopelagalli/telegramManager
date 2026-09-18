from datetime import date, datetime
from zoneinfo import ZoneInfo
import pytest
from bot.knowledge.models import (Todo, Event, Goal, Profile, EventTimes, Channel, Channels, Course,
                                  Source, slugify, parse_frontmatter, dump_frontmatter)

NY = ZoneInfo("America/New_York")

TODO_MD = """---
type: todo
title: Call dentist to reschedule
priority: 1
due: 2026-09-05
status: open
verify: none
goal: ../goals/2026-W36-health.md
confirmed: true
tags: [health]
timestamp: 2026-09-03T14:10:00-04:00
---
Ask about moving the cleaning.
"""

def test_todo_round_trip():
    t = Todo.from_markdown("todos/2026-09-03-call-dentist.md", TODO_MD)
    assert t.title == "Call dentist to reschedule"
    assert t.priority == 1 and t.due == date(2026, 9, 5)
    assert t.goal == "../goals/2026-W36-health.md"
    assert t.created == date(2026, 9, 3)
    assert not t.in_backlog
    again = Todo.from_markdown(t.path, t.to_markdown())
    assert again == t
    assert t.to_markdown().startswith("---\ntype: todo\n")

def test_todo_defaults_when_keys_missing():
    t = Todo.from_markdown("backlog/2026-01-01-x.md", "---\ntype: todo\ntitle: X\n---\n")
    assert t.priority == 2 and t.status == "open" and t.due is None and t.in_backlog

def test_event_times():
    p = Profile()
    e = Event(path="schedule/2026-09-04-gym.md", title="Gym",
              start=datetime(2026, 9, 4, 18, 0, tzinfo=NY), travel_minutes=20, prep_minutes=15)
    t = e.times(p)
    assert t.leave_by == datetime(2026, 9, 4, 17, 40, tzinfo=NY)
    assert t.get_ready_at == datetime(2026, 9, 4, 17, 25, tzinfo=NY)
    assert t.leave_at == datetime(2026, 9, 4, 17, 35, tzinfo=NY)

def test_event_times_use_profile_prep_default_when_unset():
    p = Profile(default_prep_minutes=30, leave_lead_minutes=10)
    e = Event(path="schedule/x.md", title="X", start=datetime(2026, 9, 4, 9, 0, tzinfo=NY))
    t = e.times(p)
    assert t.leave_by == e.start
    assert t.get_ready_at == datetime(2026, 9, 4, 8, 30, tzinfo=NY)
    assert t.leave_at == datetime(2026, 9, 4, 8, 50, tzinfo=NY)

def test_event_round_trip_with_latlng():
    e = Event(path="schedule/x.md", title="X", start=datetime(2026, 9, 4, 9, 0, tzinfo=NY),
              location="Equinox", location_latlng=(40.72, -73.99), importance="critical")
    again = Event.from_markdown(e.path, e.to_markdown())
    assert again == e

def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        Event.from_markdown("schedule/x.md", "---\ntype: event\ntitle: X\nstart: 2026-09-04T09:00:00\n---\n")

def test_profile_defaults_and_window():
    p = Profile.from_markdown("---\ntype: profile\nname: Giovanni\n---\nLikes gym.\n")
    assert p.name == "Giovanni" and p.timezone == "America/New_York"
    assert p.followup_gaps_minutes == [] and p.checkin_times == ["11:00", "15:00", "19:00"]
    start, end = p.waking_window(date(2026, 9, 3))
    assert start == datetime(2026, 9, 3, 8, 0, tzinfo=NY) and end == datetime(2026, 9, 3, 22, 0, tzinfo=NY)
    assert p.body.strip() == "Likes gym."
    assert Profile.from_markdown(p.to_markdown()) == p

def test_goal_round_trip():
    g = Goal(path="goals/2026-ship.md", title="Ship", period="2026-W36")
    assert Goal.from_markdown(g.path, g.to_markdown()) == g

def test_slugify():
    assert slugify("Call dentist to reschedule!") == "call-dentist-to-reschedule"
    assert len(slugify("x" * 100)) == 40

def test_frontmatter_helpers():
    meta, body = parse_frontmatter("---\na: 1\n---\nhi\n")
    assert meta == {"a": 1} and body.strip() == "hi"
    assert dump_frontmatter({"type": "todo", "due": date(2026, 9, 5)}, "b") == "---\ntype: todo\ndue: 2026-09-05\n---\nb\n"

def test_todo_and_event_carry_course_fields():
    t = Todo(path="todos/x.md", title="Lab 3", course="cs101", kind="lab")
    assert Todo.from_markdown(t.path, t.to_markdown()) == t
    e = Event(path="schedule/x.md", title="Midterm", start=datetime(2026, 9, 4, 9, 0, tzinfo=NY),
              course="cs101", kind="exam", topics=["recursion", "sorting"])
    assert Event.from_markdown(e.path, e.to_markdown()) == e

def test_profile_study_defaults():
    p = Profile()
    assert p.tutor_context_chars == 150000 and p.exam_review_offsets_days == [7, 3, 1]
    assert p.review_time == "18:00" and p.digest_cadence == "daily"
    assert Profile.from_markdown(p.to_markdown()) == p

def test_profile_thinking_round_trip():
    p = Profile()
    assert p.thinking is False
    p.thinking = True
    assert Profile.from_markdown(p.to_markdown()) == p

def test_channel_key_and_name():
    assert Channel(-100, 45, "review").key == "-100:45"
    assert Channel(-100, None, "life").key == "-100:0"
    assert Channel(-100, 45, "course", "cs101").name == "course:cs101"
    assert Channel(-100, 45, "review").name == "review"

def test_channels_round_trip_and_lookup():
    c = Channels()
    c.bind(Channel(-100, 45, "course", "cs101"))
    c.bind(Channel(-100, 46, "review"))
    again = Channels.from_markdown(c.to_markdown())
    assert again == c
    assert again.by_key("-100:45").course == "cs101"
    assert again.for_kind("review").thread_id == 46
    assert again.for_kind("course", "cs101").thread_id == 45
    assert again.for_kind("course", "phys1") is None
    assert again.for_kind("exams") is None
    c.unbind("-100:46")
    assert c.for_kind("review") is None
    assert "course:cs101" in c.to_markdown()

def test_channels_empty_round_trip():
    assert Channels.from_markdown(Channels().to_markdown()) == Channels()

def test_course_round_trip_and_slug():
    c = Course(path="courses/cs101.md", title="Intro to CS", term="Fall 2026", topics=["recursion"])
    assert c.slug == "cs101"
    assert Course.from_markdown(c.path, c.to_markdown()) == c
    assert c.to_markdown().startswith("---\ntype: course\n")

def test_course_defaults_when_keys_missing():
    c = Course.from_markdown("courses/x.md", "---\ntype: course\ntitle: X\n---\n")
    assert c.term is None and c.topics == []

def test_source_round_trip():
    s = Source(path="sources/cs101/2026-09-03-lecture-7.md", title="Lecture 7", course="cs101",
               kind="slides", topics=["pointers"], summary="Pointers and the stack.", pages=30,
               body="## slide 1\nPointers")
    again = Source.from_markdown(s.path, s.to_markdown())
    assert again == s
    assert s.to_markdown().startswith("---\ntype: source\n")

def test_source_defaults_when_keys_missing():
    s = Source.from_markdown("sources/cs101/x.md", "---\ntype: source\ntitle: X\ncourse: cs101\n---\nbody\n")
    assert s.kind == "other" and s.topics == [] and s.summary == ""
    assert s.pages is None and s.group is None and s.ocr is None and s.timestamp is None
