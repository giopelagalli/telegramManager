from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
import pytest
from bot.agent.client import FakeModelClient
from bot.agent.agent import Agent
from bot.knowledge.store import KnowledgeStore
from bot.knowledge.models import Todo, Event
from bot.scheduler.state import RuntimeState
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
