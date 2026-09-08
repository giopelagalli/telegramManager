import asyncio
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
import pytest
from bot.agent.client import FakeModelClient
from bot.agent.agent import Agent
from bot.knowledge.store import KnowledgeStore
from bot.knowledge.models import Todo, Event
from bot.scheduler.checkins import checkin_slots
from bot.scheduler.state import Chain, RuntimeState
from bot.scheduler.clock import FakeClock
from bot.scheduler.engine import Engine

NY = ZoneInfo("America/New_York")
def T(h, m=0): return datetime(2026, 9, 3, h, m, tzinfo=NY)

class Sink:
    def __init__(self): self.sent = []
    async def send(self, out): self.sent.append(out)

@pytest.fixture
def rig(tmp_path):
    clock = FakeClock(T(7, 0))
    store = KnowledgeStore(tmp_path / "k", clock=clock.now); store.init()
    store.add(Todo(path="", title="Dentist", priority=1, due=date(2026, 9, 3)))
    store.add(Event(path="", title="Gym", start=T(18), travel_minutes=20, prep_minutes=15)); store.commit("s")
    state = RuntimeState.load(tmp_path / "s.json")
    sink = Sink()
    eng = Engine(store, Agent(FakeModelClient([]), None, store, clock.now), state, tmp_path / "s.json", clock, sink, None)
    eng.startup(clock.now())
    return eng, clock, sink, state, store

async def run_until(eng, clock, until, step_s=30):
    while clock.now() < until:
        await eng.tick(); clock.advance(seconds=step_s)

async def test_full_day_message_budget_and_order(rig):
    eng, clock, sink, state, store = rig
    await run_until(eng, clock, T(22, 30))
    kinds = [o.kind for o in sink.sent]
    assert kinds.count("briefing") == 2 and kinds.count("reminder") == 2
    assert 1 <= kinds.count("checkin") <= 14
    # never more than 3 non-critical proactive messages (checkin+followup) in any rolling hour
    times = [t for t, o in zip(eng.sent_times, sink.sent) if o.kind in ("checkin", "followup")]
    for i, t in enumerate(times):
        assert sum(1 for u in times if t - timedelta(hours=1) < u <= t) <= 3
    assert all(o.kind != "checkin" or T(8) <= eng.sent_times[i] < T(22) for i, o in enumerate(sink.sent))
    assert (eng.state_path).exists()

async def test_pause_blocks_checkins_not_briefings_or_reminders(rig):
    eng, clock, sink, state, store = rig
    state.pause_until = T(23)
    await run_until(eng, clock, T(22, 30))
    kinds = [o.kind for o in sink.sent]
    assert kinds.count("checkin") == 0 and kinds.count("followup") == 0
    assert kinds.count("briefing") == 2 and kinds.count("reminder") == 2

async def test_startup_drops_late_jobs(rig):
    eng, clock, sink, state, store = rig
    clock.set(T(18, 30)); eng.startup(clock.now())
    await eng.tick()
    assert all(o.kind != "reminder" for o in sink.sent)

async def test_wake_flow_sends_morning_after_done(rig):
    eng, clock, sink, state, store = rig
    p = store.profile(); p.wake_time = "07:05"; store.save_profile(p)
    await run_until(eng, clock, T(7, 6))
    assert sink.sent and sink.sent[0].kind == "wake"
    state.wake.phase = "done"; state.wake.verified = True
    await eng.tick()
    assert sink.sent[-1].kind == "briefing" and state.wake is None and "morning:2026-09-03" in state.fired


async def test_run_survives_tick_exception(rig):
    eng, clock, sink, state, store = rig
    calls = []

    async def flaky():
        calls.append(len(calls))
        if len(calls) == 1:
            raise RuntimeError("boom")

    eng.tick = flaky
    task = asyncio.create_task(eng.run(interval_seconds=0))
    for _ in range(100):
        if len(calls) >= 2:
            break
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(calls) >= 2


async def test_late_start_after_wake_time_still_sends_morning(rig):
    eng, clock, sink, state, store = rig
    p = store.profile(); p.wake_time = "06:30"; store.save_profile(p)
    clock.set(T(9)); eng.startup(clock.now())
    state.last_user_message_at = T(9)  # keep check-ins out of the way
    await eng.tick()
    briefings = [o for o in sink.sent if o.kind == "briefing"]
    assert len(briefings) == 1 and "Wake-up window missed." in briefings[0].text
    assert state.wake is None
    before = len(sink.sent)
    await eng.tick()
    assert len(sink.sent) == before


async def test_budget_exhausted_checkin_consumes_slot_and_keeps_chain(rig):
    eng, clock, sink, state, store = rig
    day = date(2026, 9, 3)
    key, due = checkin_slots(store.profile(), day)[0]
    clock.set(due)
    state.fired.add(f"morning:{day}")  # keep the briefing from touching the chain
    state.proactive_sends = [due - timedelta(minutes=m) for m in (30, 20, 10)]
    chain = Chain("briefing", due, due, 0, item="Dentist", history=["x"])
    state.chain = chain
    await eng.tick()
    assert all(o.kind != "checkin" for o in sink.sent)
    assert key in state.fired
    assert state.chain is chain


class RecordingEngine(Engine):
    def __init__(self, *args):
        super().__init__(*args)
        self.reasons: list[str] = []

    def escalate_external(self, reason: str) -> None:
        self.reasons.append(reason)


async def test_escalate_external_called_at_caps(rig):
    eng, clock, sink, state, store = rig
    eng = RecordingEngine(eng.store, eng.agent, eng.state, eng.state_path, eng.clock, eng.sender, eng.maps)

    p = store.profile(); p.wake_time = "07:00"; store.save_profile(p)
    await eng.tick()  # 07:00 — wake starts
    assert state.wake is not None
    clock.set(T(7, 31))  # past wakeup_cap_minutes (30)
    await eng.tick()
    assert "wake-up hit its cap" in eng.reasons

    store.add(Event(path="", title="Flight", start=T(12), travel_minutes=0, prep_minutes=15, importance="critical"))
    store.commit("critical event")
    clock.set(T(11, 55))  # leave_at
    await eng.tick()
    assert state.critical is not None
    clock.set(T(12, 20))  # leave_by + critical_leave_cap_minutes (20)
    await eng.tick()
    assert "critical leave hit its cap" in eng.reasons
