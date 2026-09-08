# tests/scheduler/test_critical.py
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
import pytest
from bot.knowledge.models import Event
from bot.knowledge.store import KnowledgeStore
from bot.scheduler.state import RuntimeState, CriticalLeaveState
from bot.scheduler import critical as C

NY = ZoneInfo("America/New_York")
def T(h, m=0, s=0): return datetime(2026, 9, 4, h, m, s, tzinfo=NY)

@pytest.fixture
def store(tmp_path):
    st = KnowledgeStore(tmp_path / "k", clock=lambda: T(8)); st.init()
    p = st.profile(); p.home_latlng = (40.7000, -74.0000); st.save_profile(p)
    st.add(Event(path="", title="Flight", start=T(18), travel_minutes=60, importance="critical",
                 location="JFK", location_latlng=(40.6413, -73.7781)))
    st.commit("e"); return st

def fresh(store):
    s = RuntimeState.load(Path("/nonexistent"))
    s.critical = CriticalLeaveState(store.events()[0].path, "lead", T(16, 55), None, 0)
    return s

def test_lead_message_then_storm_cadence(store):
    s = fresh(store)
    o = C.leave_tick(T(16, 55), s, store)
    assert o and o.location_button and o.critical and "leave now for Flight" in o.text and "5:00pm" in o.text
    assert C.leave_tick(T(16, 56), s, store) is None
    assert C.leave_tick(T(17, 0), s, store) and s.critical.phase == "storm"
    assert C.leave_tick(T(17, 0, 30), s, store) is None
    assert C.leave_tick(T(17, 1), s, store)
    assert C.leave_tick(T(17, 5, 30), s, store) is None or True
    assert C.leave_tick(T(17, 6), s, store) and C.leave_tick(T(17, 6, 30), s, store)   # 30s cadence after 5 min

def test_cap_marks_missed(store):
    s = fresh(store); C.leave_tick(T(16, 55), s, store); C.leave_tick(T(17, 0), s, store)
    o = C.leave_tick(T(17, 20), s, store)
    assert "stop now" in o.text and s.critical is None and store.events()[0].status == "missed"

def test_location_still_home_then_left_then_arrived(store):
    s = fresh(store); C.leave_tick(T(16, 55), s, store)
    o = C.leave_on_location(T(16, 57), s, store, 40.7001, -74.0001)
    assert "still at home" in o.text and s.critical
    o = C.leave_on_location(T(16, 59), s, store, 40.7100, -74.0000)
    assert "on your way" in o.text and s.critical is None and store.events()[0].status == "left"
    o = C.leave_on_location(T(17, 50), s, store, 40.6414, -73.7780)
    assert "made it" in o.text and store.events()[0].status == "arrived"

def test_text_does_not_verify(store):
    s = fresh(store)
    assert "Words don't count" in C.leave_on_text(s, store).text and s.critical

def test_no_home_accepts_any_fix(store):
    p = store.profile(); p.home_latlng = None; store.save_profile(p)
    s = fresh(store)
    o = C.leave_on_location(T(16, 57), s, store, 40.7001, -74.0001)
    assert s.critical is None and "taking your word" in o.text
    assert store.events()[0].status == "left"

def test_stale_left_event_ignored(store):
    ev = store.events()[0]
    ev.status = "left"
    ev.start = T(16) - timedelta(days=1)
    store.save(ev)
    store.commit("stale")
    s = RuntimeState.load(Path("/nonexistent"))
    s.critical = None
    o = C.leave_on_location(T(16, 57), s, store, 40.9, -74.9)
    assert o is None
    assert store.get_event(ev.path).status == "left"

def test_leave_on_text_no_critical_returns_none(store):
    s = RuntimeState.load(Path("/nonexistent"))
    s.critical = None
    assert C.leave_on_text(s, store) is None

def test_storm_message_alternates(store):
    s = fresh(store)
    C.leave_tick(T(16, 55), s, store)
    o1 = C.leave_tick(T(17, 0), s, store)
    o2 = C.leave_tick(T(17, 1), s, store)
    assert o1.text != o2.text

# wake-up
@pytest.fixture
def wstore(tmp_path):
    st = KnowledgeStore(tmp_path / "k", clock=lambda: T(6)); st.init()
    p = st.profile(); p.wake_time = "06:30"; st.save_profile(p); return st

def test_wake_full_flow(wstore):
    s = RuntimeState.load(Path("/nonexistent"))
    assert not C.wake_due(T(6, 29), s, wstore) and C.wake_due(T(6, 30), s, wstore)
    o = C.wake_start(T(6, 30), s, wstore); assert o.kind == "wake" and s.wake.phase == "alarm"
    assert C.wake_tick(T(6, 30, 30), s, wstore) is None and C.wake_tick(T(6, 31), s, wstore)
    o = C.wake_on_message(T(6, 31, 10), s, wstore, "ugh"); assert "photo" in o.text.lower() and s.wake.phase == "challenge"
    o = C.wake_on_photo(T(6, 31, 40), s, wstore, False, "too dark"); assert "Try again" in o.text and s.wake.attempts == 1
    o = C.wake_on_photo(T(6, 32), s, wstore, True, "kitchen"); assert s.wake.phase == "engage" and s.wake.verified
    assert C.wake_on_message(T(6, 32, 20), s, wstore, "ok").text.startswith("More than that")
    assert C.wake_on_message(T(6, 32, 50), s, wstore, "gym then emails") is None and s.wake.engaged_seconds == 50
    assert C.wake_on_message(T(6, 33, 50), s, wstore, "then the dentist call") is None and s.wake.engaged_seconds == 110
    o = C.wake_on_message(T(6, 34, 30), s, wstore, "and lunch with Sam"); assert o.text == "You're up." and s.wake.phase == "done"

def test_wake_silence_in_engage_returns_to_alarm_fast(wstore):
    s = RuntimeState.load(Path("/nonexistent")); C.wake_start(T(6, 30), s, wstore)
    C.wake_on_message(T(6, 31), s, wstore, "hi"); C.wake_on_photo(T(6, 31, 30), s, wstore, True, "")
    assert C.wake_tick(T(6, 32), s, wstore) is None
    o = C.wake_tick(T(6, 32, 31), s, wstore); assert "still with me" in o.text and s.wake.phase == "alarm" and s.wake.cadence_seconds == 30

def test_wake_cap(wstore):
    s = RuntimeState.load(Path("/nonexistent")); C.wake_start(T(6, 30), s, wstore)
    o = C.wake_tick(T(7, 0), s, wstore); assert "Couldn't verify" in o.text and s.wake.phase == "done" and s.wake.verified is False

def test_wake_photo_three_failures_moves_on(wstore):
    s = RuntimeState.load(Path("/nonexistent")); C.wake_start(T(6, 30), s, wstore)
    C.wake_on_message(T(6, 31), s, wstore, "hi")
    for _ in range(2): C.wake_on_photo(T(6, 31), s, wstore, False, "no")
    o = C.wake_on_photo(T(6, 31), s, wstore, False, "no")
    assert s.wake.phase == "engage" and s.wake.verified is False and "moving on" in o.text

def test_wake_photo_none_then_tick_stays_in_engage(wstore):
    s = RuntimeState.load(Path("/nonexistent")); C.wake_start(T(6, 30), s, wstore)
    C.wake_on_message(T(6, 31), s, wstore, "hi")
    C.wake_on_photo(T(6, 33), s, wstore, None, "")
    assert C.wake_tick(T(6, 33, 30), s, wstore) is None and s.wake.phase == "engage"

def test_wake_tick_after_done_is_noop(wstore):
    s = RuntimeState.load(Path("/nonexistent")); C.wake_start(T(6, 30), s, wstore)
    C.wake_on_message(T(6, 31), s, wstore, "hi")
    C.wake_on_photo(T(6, 31, 30), s, wstore, True, "")
    C.wake_on_message(T(6, 32), s, wstore, "gym then emails")
    C.wake_on_message(T(6, 33), s, wstore, "then the dentist call")
    o = C.wake_on_message(T(6, 34), s, wstore, "and lunch with Sam")
    assert o.text == "You're up." and s.wake.phase == "done" and s.wake.verified is True
    assert C.wake_tick(T(7, 5), s, wstore) is None
    assert s.wake.verified is True


def test_deleted_event_stands_down(store):
    path = store.events()[0].path
    (store.root / path).unlink()

    def gone():
        s = RuntimeState.load(Path("/nonexistent"))
        s.critical = CriticalLeaveState(path, "lead", T(16, 55), None, 0)
        return s

    s = gone()
    assert C.leave_tick(T(16, 55), s, store) is None and s.critical is None

    s = gone()
    o = C.leave_on_location(T(16, 57), s, store, 40.7001, -74.0001)
    assert "That event is gone" in o.text and s.critical is None

    s = gone()
    o = C.leave_on_text(s, store)
    assert "That event is gone" in o.text and s.critical is None
