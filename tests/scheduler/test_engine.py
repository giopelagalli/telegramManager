import asyncio
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
import pytest
from bot.agent.client import FakeModelClient, ModelResponse
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
    assert 1 <= kinds.count("checkin") <= 3
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

    store.add(Event(path="", title="Flight", start=T(12), travel_minutes=0, prep_minutes=15, importance="critical"))
    store.commit("critical event")
    clock.set(T(11, 55))  # leave_at
    await eng.tick()
    assert state.critical is not None
    clock.set(T(12, 20))  # leave_by + critical_leave_cap_minutes (20)
    await eng.tick()
    assert "critical leave hit its cap" in eng.reasons


async def test_old_event_file_fires_each_reminder_once(tmp_path):
    """An event created days before it happens must not be re-fired by prune()."""
    clock = FakeClock(datetime(2026, 9, 1, 9, 0, tzinfo=NY))
    store = KnowledgeStore(tmp_path / "k", clock=clock.now); store.init()
    path = store.add(Event(path="", title="Gym", start=datetime(2026, 9, 10, 18, 0, tzinfo=NY),
                           travel_minutes=20, prep_minutes=15))
    store.commit("e")
    assert path.startswith("schedule/2026-09-01-")
    state = RuntimeState.load(tmp_path / "s.json")
    sink = Sink()
    eng = Engine(store, Agent(FakeModelClient([]), None, store, clock.now), state,
                 tmp_path / "s.json", clock, sink, None)
    clock.set(datetime(2026, 9, 10, 17, 0, tzinfo=NY))
    eng.startup(clock.now())
    await run_until(eng, clock, datetime(2026, 9, 10, 17, 55, tzinfo=NY), step_s=10)
    reminders = [o.text for o in sink.sent if o.kind == "reminder"]
    assert sum("Get ready" in t for t in reminders) == 1
    assert sum("Leave in" in t for t in reminders) == 1
    assert len(reminders) == 2


async def test_state_is_saved_before_a_send_so_a_crash_cannot_repeat_it(rig):
    eng, clock, sink, state, store = rig
    class Boom(Exception): pass
    class DyingSink:
        async def send(self, out):
            raise Boom()
    eng.sender = DyingSink()
    clock.set(T(8, 0))
    await eng.tick()  # the morning briefing "sends" and the process would die here
    assert "morning:2026-09-03" in RuntimeState.load(eng.state_path).fired


async def test_engine_announces_the_spark_going_down_and_coming_back(rig):
    eng, clock, sink, state, store = rig
    class Probing:
        def __init__(self): self.up = True; self.breaker_open = False
        async def probe(self): self.breaker_open = not self.up; return self.up
        async def chat(self, *a, **k): return ModelResponse("x", [])
    eng.agent.client = Probing()
    state.fired |= {"morning:2026-09-03"}
    await eng.tick()
    assert not [o for o in sink.sent if "Spark" in o.text]
    eng.agent.client.up = False
    clock.advance(seconds=61); await eng.tick()
    down = [o.text for o in sink.sent if "Spark" in o.text]
    assert len(down) == 1 and down[0].startswith("Spark's down.") and "☁️" in down[0] and state.backend_down
    clock.advance(seconds=61); await eng.tick()  # still down: no repeat
    assert len([o for o in sink.sent if "Spark" in o.text]) == 1
    eng.agent.client.up = True
    clock.advance(seconds=61); await eng.tick()
    assert [o.text for o in sink.sent if "Spark" in o.text][-1] == "Spark's back." and not state.backend_down


async def test_an_exam_entering_the_horizon_is_planned_in_the_morning(rig):
    eng, clock, sink, state, store = rig
    store.add(Event(path="", title="CS101 Midterm", start=T(10) + timedelta(days=21), kind="exam", course="cs101"))
    store.commit("exam")
    for d in ("2026-09-03", "2026-09-04"):
        state.fired.add(f"morning:{d}")
    eng.agent.client.responses.append(ModelResponse(
        '{"days": [{"date": "2026-09-04", "minutes": 45, "task": "Read chapter 1"}], "advice": "Start now."}', []
    ))
    await eng.tick()  # 07:00: too early
    assert not [o for o in sink.sent if o.kind == "plan"]
    clock.set(T(8, 5))
    await eng.tick()
    plans = [o for o in sink.sent if o.kind == "plan"]
    assert len(plans) == 1 and "Plan for CS101 Midterm" in plans[0].text and "21 days" in plans[0].text
    assert [t.due for t in store.todos() if t.kind == "study"] == [date(2026, 9, 4)]
    clock.set(T(8, 5) + timedelta(days=1))
    await eng.tick()  # planned already: nothing more, and no model call
    assert len([o for o in sink.sent if o.kind == "plan"]) == 1 and eng.agent.client.responses == []
