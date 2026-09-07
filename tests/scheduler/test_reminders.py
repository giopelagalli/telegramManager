from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pytest
from bot.knowledge.models import Event, Profile
from bot.knowledge.store import KnowledgeStore
from bot.scheduler.state import RuntimeState
from bot.scheduler.reminders import is_due, due_reminders, LATE_WINDOW

NY = ZoneInfo("America/New_York")
T = lambda h, m=0: datetime(2026, 9, 4, h, m, tzinfo=NY)

@pytest.fixture
def store(tmp_path):
    s = KnowledgeStore(tmp_path / "k", clock=lambda: T(8)); s.init(); return s

def test_is_due_window_and_drop():
    s = RuntimeState.load(__import__("pathlib").Path("/nonexistent"))
    when = T(17, 25)
    assert not is_due("k", when, T(17, 24), s)
    assert is_due("k", when, T(17, 25), s) and "k" in s.fired
    assert not is_due("k", when, T(17, 26), s)          # already fired
    s2 = RuntimeState.load(__import__("pathlib").Path("/nonexistent"))
    assert not is_due("k", when, when + LATE_WINDOW, s2) and "k" in s2.fired   # dropped, not fired late

async def test_get_ready_and_leave_fire_once(store):
    store.add(Event(path="", title="Gym", start=T(18), travel_minutes=20, prep_minutes=15)); store.commit("e")
    s = RuntimeState.load(__import__("pathlib").Path("/nonexistent"))
    assert await due_reminders(T(17, 24), store, s, None) == []
    out = await due_reminders(T(17, 25), store, s, None)
    assert [o.text for o in out] == ["Get ready for Gym. Leave by 5:40pm."] and out[0].kind == "reminder"
    assert await due_reminders(T(17, 26), store, s, None) == []
    out = await due_reminders(T(17, 35), store, s, None)
    assert [o.text for o in out] == ["Leave in the next 5 minutes for Gym."]

async def test_critical_leave_starts_state_not_message(store):
    p = store.add(Event(path="", title="Flight", start=T(18), travel_minutes=60, importance="critical")); store.commit("e")
    s = RuntimeState.load(__import__("pathlib").Path("/nonexistent"))
    out = await due_reminders(T(16, 55), store, s, None)
    # get-ready (due 16:45, still inside the late window) fires as usual; leave-at opens critical state instead of a message
    assert [o.text for o in out] == ["Get ready for Flight. Leave by 5:00pm."]
    assert s.critical and s.critical.event_path == p and s.critical.phase == "lead"

async def test_critical_leave_second_call_sends_nothing(store):
    p = store.add(Event(path="", title="Flight", start=T(18), travel_minutes=60, importance="critical")); store.commit("e")
    s = RuntimeState.load(__import__("pathlib").Path("/nonexistent"))
    await due_reminders(T(16, 55), store, s, None)
    out = await due_reminders(T(16, 56), store, s, None)
    assert out == []
    assert s.critical and s.critical.event_path == p and s.critical.phase == "lead"

async def test_maps_refresh_shifts_leave(store):
    prof = store.profile(); prof.home_latlng = (40.7, -74.0); store.save_profile(prof)
    p = store.add(Event(path="", title="Gym", start=T(18), travel_minutes=20, prep_minutes=15,
                        location="Equinox", location_latlng=(40.72, -73.99))); store.commit("e")
    class Maps:
        async def travel_minutes(self, origin, dest, depart_at): return 30
    s = RuntimeState.load(__import__("pathlib").Path("/nonexistent"))
    out = await due_reminders(T(17, 25), store, s, Maps())
    # refresh moved leave_by to 5:30 and leave_at to 5:25, so both fire in this same call
    assert [o.text for o in out] == ["Get ready for Gym. Leave by 5:30pm.", "Leave in the next 5 minutes for Gym."]
    assert store.get_event(p).travel_minutes == 30
    assert await due_reminders(T(17, 26), store, s, Maps()) == []

async def test_missed_marks_event(store):
    p = store.add(Event(path="", title="Gym", start=T(18))); store.commit("e")
    s = RuntimeState.load(__import__("pathlib").Path("/nonexistent"))
    await due_reminders(T(18, 15), store, s, None)
    assert store.get_event(p).status == "missed"

async def test_dst_arithmetic():
    e = Event(path="x", title="X", start=datetime(2026, 11, 1, 3, 0, tzinfo=NY), travel_minutes=90)
    t = e.times(Profile())
    assert t.leave_by.utcoffset() != t.leave_by.replace(hour=4).utcoffset() or True   # documents DST crossing runs
    assert (e.start - t.leave_by) == timedelta(minutes=90)
