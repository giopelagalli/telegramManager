from datetime import datetime, date
from zoneinfo import ZoneInfo
import pytest
from bot.agent.client import FakeModelClient, ModelResponse, ToolCall
from bot.agent.agent import Agent
from bot.knowledge.store import KnowledgeStore
from bot.knowledge.models import Todo, Event
from bot.scheduler.state import RuntimeState, Chain, CriticalLeaveState
from bot.scheduler.clock import FakeClock
from bot.telegram.router import Router

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 3, 14, 0, tzinfo=NY)


def R(*calls):
    return ModelResponse(None, [ToolCall(n, a) for n, a in calls])


class FakeMaps:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    async def geocode(self, address):
        self.calls.append(address)
        return self.mapping.get(address)


@pytest.fixture
def rig(tmp_path):
    clock = FakeClock(NOW)
    store = KnowledgeStore(tmp_path / "k", clock=clock.now)
    store.init()
    client = FakeModelClient([])
    agent = Agent(client, None, store, clock.now)
    state = RuntimeState.load(tmp_path / "s.json")
    return Router(store, agent, state, clock, None), store, client, state, clock


async def test_text_capture_closes_chain_and_echoes(rig):
    router, store, client, state, _ = rig
    state.chain = Chain("checkin", NOW, NOW, 0, "X", [])
    client.responses.append(R(("add_todo", {"title": "Buy milk", "priority": 3}), ("reply", {"text": "Sure."})))
    outs = await router.on_text("buy milk")
    assert state.chain is None and state.last_user_message_at == NOW
    assert outs[0].text == "Sure.\nAdded todo: Buy milk (P3)" and outs[0].kind == "reply" and not outs[0].voice
    assert "Awaiting answer to: X" in client.calls[0]["messages"][1]["content"]


async def test_voice_in_voice_out_default(rig):
    router, store, client, state, _ = rig
    client.responses.append(R(("reply", {"text": "Ok."})))
    assert (await router.on_text("hi", via_voice=True))[0].voice


async def test_snooze_sets_pause(rig):
    router, store, client, state, _ = rig
    client.responses.append(R(("snooze", {"minutes": 120}), ("reply", {"text": "Ok, quiet for 2h."})))
    await router.on_text("not now")
    assert state.pause_until == NOW.replace(hour=16)


async def test_critical_text_and_location(rig):
    router, store, client, state, _ = rig
    p = store.add(Event(path="", title="Flight", start=NOW.replace(hour=18), travel_minutes=60, importance="critical"))
    store.commit("e")
    state.critical = CriticalLeaveState(p, "lead", NOW, None, 0)
    assert "Words don't count" in (await router.on_text("I left"))[0].text
    outs = await router.on_location(40.0, -74.0)
    assert state.critical is None and store.get_event(p).status == "left"


async def test_done_button_with_and_without_verify(rig):
    router, store, client, state, _ = rig
    a = store.add(Todo(path="", title="Easy"))
    b = store.add(Todo(path="", title="Car", verify="photo"))
    store.commit("t")
    assert (await router.on_callback(f"done:{a}"))[0].text == "Done: Easy" and store.get_todo(a).status == "done"
    outs = await router.on_callback(f"done:{b}")
    assert "photo" in outs[0].text.lower() and state.pending_verify.todo_path == b
    assert store.get_todo(b).status == "done" and store.get_todo(b).confirmed is False


async def test_photo_verifies_pending(rig):
    router, store, client, state, _ = rig
    b = store.add(Todo(path="", title="Car", verify="photo"))
    store.commit("t")
    await router.on_callback(f"done:{b}")
    outs = await router.on_photo(b"img")  # no vision client -> None -> accepted with note
    assert state.pending_verify is None and "verif" in outs[0].text.lower()


async def test_defer_button(rig):
    router, store, client, state, _ = rig
    a = store.add(Todo(path="", title="Slip", due=date(2026, 9, 1)))
    store.commit("t")
    assert "tomorrow" in (await router.on_callback(f"defer:{a}"))[0].text.lower()
    assert store.get_todo(a).due == date(2026, 9, 4)


async def test_set_home_address_geocodes(tmp_path):
    clock = FakeClock(NOW)
    store = KnowledgeStore(tmp_path / "k", clock=clock.now)
    store.init()
    maps = FakeMaps({"1 Main St": (40.7, -74.0)})
    client = FakeModelClient(
        [R(("set_profile", {"field": "home_address", "value": "1 Main St"}), ("reply", {"text": "Got it."}))]
    )
    agent = Agent(client, None, store, clock.now)
    state = RuntimeState.load(tmp_path / "s.json")
    router = Router(store, agent, state, clock, maps)

    await router.on_text("i live at 1 Main St")
    assert store.profile().home_latlng == (40.7, -74.0)
    assert maps.calls == ["1 Main St"]


async def test_location_verify_pending_todo(tmp_path):
    clock = FakeClock(NOW)
    store = KnowledgeStore(tmp_path / "k", clock=clock.now)
    store.init()
    maps = FakeMaps({"Equinox": (40.0, -74.0)})
    agent = Agent(FakeModelClient([]), None, store, clock.now)
    state = RuntimeState.load(tmp_path / "s.json")
    router = Router(store, agent, state, clock, maps)

    a = store.add(Todo(path="", title="Gym", verify="location", body="Location: Equinox"))
    store.commit("t")
    await router.on_callback(f"done:{a}")
    assert state.pending_verify.todo_path == a

    await router.on_location(40.0009, -74.0)  # ~100m away, within the 200m radius
    assert store.get_todo(a).confirmed is True
    assert state.pending_verify is None

    b = store.add(Todo(path="", title="Gym2", verify="location", body="Location: Equinox"))
    store.commit("t2")
    await router.on_callback(f"done:{b}")

    await router.on_location(40.05, -74.0)  # ~5.5km away, outside the radius
    assert store.get_todo(b).confirmed is False
    assert state.pending_verify is None


async def test_add_event_geocodes_its_location(rig):
    router, store, client, state, _ = rig
    router.maps = FakeMaps({"Equinox": (40.75, -73.99)})
    client.responses.append(R(
        ("add_event", {"title": "Gym", "start": "2026-09-04T18:00:00-04:00", "location": "Equinox"}),
        ("reply", {"text": "Ok."}),
    ))
    await router.on_text("gym tomorrow 6pm at Equinox")
    event = store.events()[0]
    assert event.location_latlng == (40.75, -73.99)
    assert router.maps.calls == ["Equinox"]
    subject = __import__("subprocess").run(["git", "log", "-1", "--format=%s"], cwd=store.root,
                                           capture_output=True, text=True).stdout
    assert subject.strip() == "geocode: Gym"


async def test_voice_failure_saves_an_inbox_note(rig):
    router, store, client, state, _ = rig
    outs = await router.on_voice_failed("whisper exploded")
    assert outs[0].text == "Couldn't transcribe that. Saved a note in your inbox."
    notes = list((store.root / "inbox").glob("*.md"))
    assert len(notes) == 1
    assert "[voice note could not be transcribed: whisper exploded]" in notes[0].read_text()


async def test_voice_unavailable_asks_for_text(rig):
    router = rig[0]
    assert (await router.on_voice_unavailable())[0].text == "Voice input isn't set up here. Send it as text."
