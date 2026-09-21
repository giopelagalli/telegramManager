from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.knowledge.store import KnowledgeStore
from bot.knowledge.trackers import status_line
from bot.scheduler.state import RuntimeState
from bot.scheduler.trackers import due_tracker_reminders

NY = ZoneInfo("America/New_York")


def T(h, m=0):
    return datetime(2026, 9, 21, h, m, tzinfo=NY)


def _store(tmp_path, now):
    clock = {"now": now}
    s = KnowledgeStore(tmp_path / "k", clock=lambda: clock["now"]); s.init()
    return s, clock


def test_trackers_are_set_up_logged_and_summed(tmp_path):
    s, clock = _store(tmp_path, T(9))
    t = s.upsert_tracker("Water", unit="oz", target=100, remind_every_minutes=120)
    assert t.name == "water" and s.tracker("water").target == 100
    s.add_tracking("water", 16)
    clock["now"] = T(11)
    s.add_tracking("water", 24.5, note="gym")
    assert s.tracking_total(T(11).date(), "water") == 40.5 and s.tracking_last_at(T(11).date(), "water") == "11:00"
    assert status_line(s, T(11).date(), s.tracker("water")) == "water: 40.5 / 100 oz"
    # a first log on an unknown name creates the tracker with the unit as said
    s.add_tracking("cholesterol", 180, unit="mg")
    assert s.tracker("cholesterol").unit == "mg" and status_line(s, T(11).date(), s.tracker("cholesterol")) == "cholesterol: 180 mg"
    s.upsert_tracker("water", active=False, remind_at=[], remind_every_minutes=0)
    assert not s.tracker("water").active and s.tracker("water").remind_every_minutes is None


def test_reminders_at_times_and_every_n_minutes_inside_waking_hours(tmp_path):
    s, clock = _store(tmp_path, T(8))
    s.upsert_tracker("meals", unit="", remind_at=["12:00", "18:00"])
    s.upsert_tracker("water", unit="oz", target=100, remind_every_minutes=120)
    st = RuntimeState.load(Path("/nonexistent"))
    assert due_tracker_reminders(T(8), s, st) == []  # first interval slot is not due yet
    clock["now"] = T(10)
    out = due_tracker_reminders(T(10), s, st)
    assert [o.text for o in out] == ["Water: 0 / 100 oz. Drink."]
    assert due_tracker_reminders(T(10, 1), s, st) == []  # once per slot
    clock["now"] = T(11)
    s.add_tracking("water", 16)  # logged at 11:00 → the 12:00 slot is skipped (less than 2h since)
    clock["now"] = T(12)
    out = due_tracker_reminders(T(12), s, st)
    assert [o.text for o in out] == ["Meals: 0. Eat."]
    clock["now"] = T(18)  # 7h since the last water: both fire
    assert [o.text for o in due_tracker_reminders(T(18), s, st)] == ["Meals: 0. Eat.", "Water: 16 / 100 oz. Drink."]
    st.pause_until = T(23)
    assert due_tracker_reminders(T(18, 1), s, st) == []
    st.pause_until = None
    assert due_tracker_reminders(T(23), s, st) == []  # outside waking hours
