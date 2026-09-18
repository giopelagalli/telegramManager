from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.memory.thread import remember, thread, user_turns, THREAD_MAX_CHARS
from bot.scheduler.state import RuntimeState

NY = ZoneInfo("America/New_York")
T0 = datetime(2026, 9, 18, 9, 0, tzinfo=NY)


def _state():
    return RuntimeState.load(Path("/nonexistent"))


def test_the_whole_day_is_kept_not_eight_lines():
    s = _state()
    for i in range(40):
        remember(s, "user", f"u{i}", T0 + timedelta(minutes=i))
        remember(s, "assistant", f"a{i}", T0 + timedelta(minutes=i))
    t = thread(s, T0 + timedelta(hours=1))
    assert len(t) == 80 and t[0] == ["user", "u0"] and t[-1] == ["assistant", "a39"]
    assert user_turns(s) == 40


def test_long_gaps_are_marked_and_a_new_day_starts_fresh():
    s = _state()
    remember(s, "user", "morning", T0)
    remember(s, "assistant", "yo", T0)
    remember(s, "user", "evening", T0 + timedelta(hours=8))
    t = thread(s, T0 + timedelta(hours=8))
    assert t[2] == ["user", "[8h later] evening"]
    # 1am the same night is still today; 9am the next day is not
    assert thread(s, T0 + timedelta(hours=16)) and len(s.recent) == 3
    assert thread(s, T0 + timedelta(days=1)) == [] and s.recent == []


def test_size_cap_drops_the_oldest_and_old_two_item_entries_still_work():
    s = _state()
    s.recent = [["user", "legacy"], ["assistant", "entry"]]
    assert thread(s, T0) == [["user", "legacy"], ["assistant", "entry"]]  # no crash on old entries
    for i in range(40):
        remember(s, "user", "x" * 5000, T0 + timedelta(minutes=i))  # clipped to 2000 each
    assert all(len(e[1]) <= 2000 for e in s.recent)
    assert sum(len(e[1]) for e in s.recent) <= THREAD_MAX_CHARS
    assert len(s.recent) == 30 and s.recent[0][1] != "legacy"
