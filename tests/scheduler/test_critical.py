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
