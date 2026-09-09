from datetime import datetime, date
from zoneinfo import ZoneInfo
import pytest
from bot.agent.client import FakeModelClient, ModelResponse, ToolCall
from bot.agent.agent import Agent
from bot.knowledge.store import KnowledgeStore
from bot.knowledge.models import Todo, Event, Channel, Course, Source, UNBOUND
from bot.knowledge.views import esc
from bot.scheduler.state import RuntimeState, Chain, CriticalLeaveState
from bot.scheduler.clock import FakeClock
from bot.telegram.router import Router, UNBOUND_REPLY

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


async def test_last_outcome_tracks_capture_and_inbox(rig):
    router, store, client, state, _ = rig
    client.responses.append(R(("add_todo", {"title": "Buy milk", "priority": 3}), ("reply", {"text": "Sure."})))
    await router.on_text("buy milk")
    assert router.last_outcome == "captured"

    client.responses.append(ModelResponse(None, []))
    await router.on_text("something rambling")
    assert router.last_outcome == "inbox"


async def test_unbound_topic_is_warned_once_then_ignored(rig):
    router, store, client, state, _ = rig
    topic = Channel(-100, 45, UNBOUND)
    outs = await router.on_text("some notes", channel=topic)
    assert outs[0].text == esc(UNBOUND_REPLY)
    assert "/bind course" in outs[0].text
    assert await router.on_text("more notes", channel=topic) == []
    assert await router.on_photo(b"img", channel=topic) == []
    assert state.last_user_message_at is None and client.calls == []

    other = Channel(-100, 46, UNBOUND)
    assert len(await router.on_text("hi", channel=other)) == 1


async def test_bound_topic_reply_carries_the_channel(rig):
    router, store, client, state, _ = rig
    store.add_course(Course(path="courses/cs101.md", title="Intro to CS"))
    client.responses.append(ModelResponse("Chapter 4 covers pointers.", []))
    outs = await router.on_text("what is in chapter 4?", channel=Channel(-100, 45, "course", "cs101"))
    assert outs[0].text == "Chapter 4 covers pointers." and outs[0].channel == "course:cs101"

    outs = await router.command("todo", "", channel=Channel(-100, 46, "assignments"))
    assert outs[0].channel == "assignments"


async def test_dm_channel_behaves_like_today(rig):
    router, store, client, state, _ = rig
    client.responses.append(R(("reply", {"text": "Sure."})))
    outs = await router.on_text("hi", channel=Channel(42, None, "life"))
    assert outs[0].text == "Sure." and outs[0].channel == "life"
    assert state.last_user_message_at == NOW


COURSE = Channel(-100, 45, "course", "cs101")
DESCRIBED = '{"title": "Chapter 4", "kind": "chapter", "topics": ["pointers", "stack"], "summary": "All about pointers."}'


class FakePage:
    def __init__(self, text):
        self._text = text

    def extract_text(self):
        return self._text


class FakeVision:
    def __init__(self, text):
        self.text = text

    async def chat(self, messages, tools=None, temperature=0.2):
        return ModelResponse(text=self.text, tool_calls=[])


def _pdf(monkeypatch, *texts):
    import pypdf

    class FakeReader:
        def __init__(self, stream):
            self.pages = [FakePage(x) for x in texts]

    monkeypatch.setattr(pypdf, "PdfReader", FakeReader)


def _subject(store):
    import subprocess
    return subprocess.run(["git", "log", "-1", "--format=%s"], cwd=store.root,
                          capture_output=True, text=True).stdout.strip()


async def test_document_in_a_course_topic_is_ingested(rig, monkeypatch):
    router, store, client, state, _ = rig
    store.add_course(Course(path="courses/cs101.md", title="Intro to CS"))
    _pdf(monkeypatch, "Pointers hold addresses", "", "More pointers")
    client.responses.append(ModelResponse(DESCRIBED, []))

    outs = await router.on_document(b"%PDF", "ch4.pdf", "application/pdf", None, channel=COURSE)
    assert outs[0].text == (
        "Stored: Chapter 4 (chapter, 3 pages, topics: pointers, stack). "
        "Wrong course? /move &lt;slug&gt;."
    )
    assert outs[0].channel == "course:cs101"

    source = store.sources("cs101")[0]
    assert source.kind == "chapter" and source.pages == 3 and source.topics == ["pointers", "stack"]
    assert source.body == "## p.1\nPointers hold addresses\n\n## p.3\nMore pointers"
    assert _subject(store) == "ingest: Chapter 4"


async def test_docx_document_in_a_course_topic_is_ingested(rig):
    import io

    from docx import Document

    router, store, client, state, _ = rig
    store.add_course(Course(path="courses/cs101.md", title="Intro to CS"))
    document = Document()
    document.add_paragraph("Lecture notes on pointers")
    buffer = io.BytesIO()
    document.save(buffer)
    client.responses.append(ModelResponse(DESCRIBED, []))

    outs = await router.on_document(
        buffer.getvalue(),
        "notes.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        None,
        channel=COURSE,
    )
    assert outs[0].channel == "course:cs101"

    source = store.sources("cs101")[0]
    assert source.body == "## part 1\nLecture notes on pointers"


async def test_unreadable_document_is_kept_raw(rig):
    router, store, client, state, _ = rig
    store.add_course(Course(path="courses/cs101.md", title="Intro to CS"))
    outs = await router.on_document(b"zipped", "data.zip", "application/zip", None, channel=COURSE)
    assert outs[0].text == "Stored the file but couldn't read it."
    assert (store.root / "sources/cs101/raw/data.zip").read_bytes() == b"zipped"
    assert store.sources("cs101") == [] and client.calls == []
    assert _subject(store) == "ingest: data.zip"


async def test_documents_outside_a_course_topic_are_ignored(rig):
    router, store, client, state, _ = rig
    assert await router.on_document(b"%PDF", "ch4.pdf", "application/pdf", None) == []
    assert await router.on_document(b"%PDF", "ch4.pdf", "application/pdf", None,
                                    channel=Channel(-100, 46, "assignments")) == []


async def test_photo_in_a_course_topic_without_vision_keeps_the_caption(rig):
    router, store, client, state, _ = rig
    store.add_course(Course(path="courses/cs101.md", title="Intro to CS"))
    client.responses.append(ModelResponse(
        '{"title": "Whiteboard", "kind": "notes", "topics": ["stack"], "summary": "A stack diagram."}', []))

    outs = await router.on_photo(b"img", "stack diagram from class", channel=COURSE)
    assert "Stored: Whiteboard (photo, topics: stack). OCR unavailable." in outs[0].text
    source = store.sources("cs101")[0]
    assert source.kind == "photo" and source.ocr == "unavailable"
    assert source.body == "stack diagram from class"


async def test_photo_with_no_vision_and_no_caption_needs_no_model(rig):
    router, store, client, state, _ = rig
    store.add_course(Course(path="courses/cs101.md", title="Intro to CS"))
    outs = await router.on_photo(b"img", None, channel=COURSE)
    assert "Stored: Photo (photo). OCR unavailable." in outs[0].text
    assert client.calls == [] and store.sources("cs101")[0].body == ""


async def test_photo_in_a_course_topic_is_ocred(tmp_path):
    clock = FakeClock(NOW)
    store = KnowledgeStore(tmp_path / "k", clock=clock.now)
    store.init()
    store.add_course(Course(path="courses/cs101.md", title="Intro to CS"))
    client = FakeModelClient([ModelResponse(
        '{"title": "Board", "kind": "photo", "topics": [], "summary": "Notes."}', [])])
    agent = Agent(client, FakeVision("f(n) = O(n log n)"), store, clock.now)
    router = Router(store, agent, RuntimeState.load(tmp_path / "s.json"), clock, None)

    outs = await router.on_photo(b"img", None, channel=COURSE)
    assert "OCR unavailable" not in outs[0].text
    source = store.sources("cs101")[0]
    assert source.ocr is None and source.body == "f(n) = O(n log n)"


async def test_text_in_a_course_topic_is_tutored(rig):
    router, store, client, state, _ = rig
    store.add_course(Course(path="courses/cs101.md", title="Intro to CS"))
    store.add_source(Source(path="", title="Chapter 4", course="cs101", topics=["pointers"],
                            body="## p.3\nPointers hold addresses"))
    store.commit("ingest: Chapter 4")
    client.responses.append(ModelResponse("A pointer holds an address [Chapter 4, p.3].", []))

    outs = await router.on_text("what is a pointer?", channel=COURSE)
    assert outs[0].text == "A pointer holds an address [Chapter 4, p.3]."
    assert "### Chapter 4" in client.calls[0]["messages"][1]["content"]
    assert state.last_user_message_at == NOW


async def test_a_tutor_note_becomes_a_source(rig):
    router, store, client, state, _ = rig
    store.add_course(Course(path="courses/cs101.md", title="Intro to CS"))
    note = "Quicksort is n log n on average\nand n squared in the worst case"
    client.responses.append(R(("save_note", {"text": note, "topics": ["sorting"]})))

    outs = await router.on_text(note, channel=COURSE)
    assert outs[0].text == "Saved note: Quicksort is n log n on average"
    source = store.sources("cs101")[0]
    assert source.kind == "notes" and source.topics == ["sorting"] and source.body == note
    assert source.summary == note
    assert _subject(store) == "note: Quicksort is n log n on average"


async def test_tutor_says_so_when_the_model_is_down(rig):
    router, store, client, state, _ = rig
    store.add_course(Course(path="courses/cs101.md", title="Intro to CS"))
    outs = await router.on_text("what is a pointer?", channel=COURSE)  # no queued response -> IndexError
    assert outs[0].text == "The model is offline; ask again in a bit."
    assert store.sources("cs101") == []


async def test_a_file_over_the_download_cap_is_refused(rig):
    router, store, client, state, _ = rig
    outs = await router.on_file_too_large(channel=COURSE)
    assert outs[0].text == (
        "That file is over Telegram's 20 MB bot limit. Split it or send a smaller export."
    )
    assert outs[0].channel == "course:cs101" and client.calls == []


async def test_a_file_over_the_cap_in_an_unbound_topic_only_warns_once(rig):
    router, store, client, state, _ = rig
    unbound = Channel(-100, 46, UNBOUND)
    assert (await router.on_file_too_large(channel=unbound))[0].text == esc(UNBOUND_REPLY)
    assert await router.on_file_too_large(channel=unbound) == []


async def test_tutor_context_shrinks_while_the_fallback_model_is_in_use(tmp_path):
    import httpx
    import openai

    from bot.agent.client import FallbackModelClient

    class Down:
        async def chat(self, messages, tools=None, temperature=0.2):
            raise openai.APIConnectionError(request=httpx.Request("POST", "http://x"))

    clock = FakeClock(NOW)
    store = KnowledgeStore(tmp_path / "k", clock=clock.now)
    store.init()
    fallback = FakeModelClient([ModelResponse("ok", []), ModelResponse("an answer", [])])
    client = FallbackModelClient(Down(), fallback)
    await client.chat([])  # opens the breaker
    assert client.breaker_open

    state = RuntimeState.load(tmp_path / "s.json")
    router = Router(store, Agent(client, None, store, clock.now), state, clock, None)
    store.add_course(Course(path="courses/cs101.md", title="Intro to CS"))
    for i in range(3):
        store.add_source(Source(path="", title=f"Chapter {i}", course="cs101",
                                topics=["pointers"], body="x" * 30_000))
    store.commit("ingest")

    await router.on_text("what is a pointer?", channel=COURSE)
    sent = fallback.calls[-1]["messages"][1]["content"]
    # 90k of sources: all of it fits the 150k default, one chapter fits the 40k fallback.
    assert sent.count("### Chapter") == 1 and len(sent) < 45_000


async def test_the_unbound_warning_goes_to_the_topic_that_asked(rig):
    router, store, client, state, _ = rig
    outs = await router.on_text("hi", channel=Channel(-100, 46, UNBOUND))
    assert outs[0].target == (-100, 46)


async def test_ingest_merges_its_topics_into_the_course(rig, monkeypatch):
    router, store, client, state, _ = rig
    store.add_course(Course(path="courses/cs101.md", title="Intro to CS", topics=["pointers"]))
    _pdf(monkeypatch, "Pointers hold addresses")
    client.responses.append(ModelResponse(DESCRIBED, []))

    await router.on_document(b"%PDF", "ch4.pdf", "application/pdf", None, channel=COURSE)
    assert store.get_course("cs101").topics == ["pointers", "stack"]
    assert "2 topics" in (await router.command("courses", ""))[0].text
    assert _subject(store) == "ingest: Chapter 4"


async def test_ingest_caps_the_course_topics(rig, monkeypatch):
    router, store, client, state, _ = rig
    existing = [f"t{i}" for i in range(50)]
    store.add_course(Course(path="courses/cs101.md", title="Intro to CS", topics=existing))
    _pdf(monkeypatch, "Pointers hold addresses")
    client.responses.append(ModelResponse(DESCRIBED, []))

    await router.on_document(b"%PDF", "ch4.pdf", "application/pdf", None, channel=COURSE)
    assert store.get_course("cs101").topics == existing
