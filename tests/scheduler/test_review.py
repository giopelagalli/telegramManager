from datetime import datetime, date
from zoneinfo import ZoneInfo
from pathlib import Path
import pytest
from bot.knowledge.models import Card, Course, Event, Source
from bot.knowledge.store import KnowledgeStore
from bot.scheduler.state import RuntimeState
from bot.scheduler import review

NY = ZoneInfo("America/New_York")
def T(h, m=0): return datetime(2026, 9, 3, h, m, tzinfo=NY)

@pytest.fixture
def store(tmp_path):
    s = KnowledgeStore(tmp_path / "k", clock=lambda: T(8)); s.init()
    s.add_course(Course(path="courses/cs101.md", title="Intro to CS"))
    for i in range(10):
        s.add_card(Card(path="", question=f"q{i}", answer="a", course="cs101", topic="stacks"))
    s.commit("c"); return s

def test_daily_session_at_review_time_with_cap(store):
    s = RuntimeState.load(Path("/nonexistent"))
    assert review.due_session(T(17, 59), store, s) is None
    out = review.due_session(T(18), store, s)
    assert out is not None and "8 questions" in out.text and s.review and len(s.review["queue"]) == 7
    assert review.due_session(T(18, 1), store, s) is None  # fired once

def test_exam_within_focus_raises_the_cap(store):
    store.add(Event(path="", title="Midterm", start=T(10).replace(day=6), kind="exam", course="cs101", topics=["stacks"])); store.commit("e")
    s = RuntimeState.load(Path("/nonexistent"))
    out = review.due_session(T(18), store, s)
    assert "10 questions" in out.text  # all ten due, exam cap is 15

async def test_digest_picks_stale_new_and_exam_sources_and_marks_them(store):
    store.add_source(Source(path="", title="Old", course="cs101", kind="notes", topics=["x"], summary="Old stuff.", last_surfaced=date(2026, 8, 1), body="o"))
    store.add_source(Source(path="", title="New", course="cs101", kind="notes", topics=["stacks"], summary="New stuff.", body="n")); store.commit("s")
    class A:
        async def digest(self, items):
            assert {t for _, t, _ in items} == {"Old", "New"}
            return "**Old stuff**\nStill true."
    s = RuntimeState.load(Path("/nonexistent"))
    assert await review.due_digest(T(8, 29), store, s, A()) is None
    out = await review.due_digest(T(8, 30), store, s, A())
    assert out is not None and "<b>Old stuff</b>" in out.text and out.buttons[0][1].startswith("quiz:cs101:")
    assert all(src.last_surfaced == date(2026, 9, 3) for src in store.sources())
    assert await review.due_digest(T(8, 31), store, s, A()) is None
