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
    def __init__(self, mapping, travel=None):
        self.mapping = mapping
        self.travel = travel
        self.calls = []
        self.travel_calls = []

    async def geocode(self, address):
        self.calls.append(address)
        return self.mapping.get(address)

    async def travel_minutes(self, origin, dest, depart_at):
        self.travel_calls.append((origin, dest, depart_at))
        return self.travel


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
        "Stored: Chapter 4 (chapter, 3 pages, topics: pointers, stack) under Intro to CS. "
        'Wrong course? Say "move that to &lt;course&gt;".'
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


async def test_document_in_the_dm_is_filed_under_an_inferred_course(rig, monkeypatch):
    router, store, client, state, _ = rig
    monkeypatch.setattr("bot.telegram.router._extract", lambda data, kind, name: [(1, "Cells and organelles")])
    client.responses.append(ModelResponse(
        '{"course": "Bio 201", "title": "Chapter 3", "kind": "chapter", "topics": ["cells"], "summary": "Cells."}', []
    ))
    outs = await router.on_document(b"%PDF", "ch3.pdf", "application/pdf", None)
    assert "Stored: Chapter 3" in outs[0].text and "under Bio 201" in outs[0].text
    assert [c.title for c in store.courses()] == ["Bio 201"]
    assert store.sources("bio-201")[0].title == "Chapter 3"
    assert "Existing courses: none yet" in client.calls[-1]["messages"][0]["content"]


async def test_study_tool_routes_a_dm_question_to_the_tutor(rig):
    router, store, client, state, _ = rig
    store.add_course(Course(path="courses/cs101.md", title="Intro to CS"))
    store.add_source(Source(path="", title="Lecture 1", course="cs101", kind="slides", topics=["stacks"],
                            summary="Stacks.", body="## slide 1\nA stack is LIFO.")); store.commit("s")
    client.responses.append(R(("study", {"question": "what is a stack?", "course": "cs101"}), ("reply", {"text": "..."})))
    client.responses.append(ModelResponse("A stack is last-in, first-out [Lecture 1, slide 1].", []))
    outs = await router.on_text("what is a stack?")
    assert "last-in, first-out" in outs[0].text
    assert "A stack is LIFO" in client.calls[-1]["messages"][1]["content"]


async def test_move_source_in_plain_words(rig):
    router, store, client, state, _ = rig
    store.add_course(Course(path="courses/cs101.md", title="Intro to CS"))
    store.add_course(Course(path="courses/bio-201.md", title="Bio 201"))
    store.add_source(Source(path="", title="Ch 3", course="cs101", kind="chapter", topics=[], summary="", body="x")); store.commit("s")
    client.responses.append(R(("move_source", {"course": "Bio 201"}), ("reply", {"text": "Moved."})))
    outs = await router.on_text("move that to bio 201")
    assert "Moved Ch 3 to Bio 201" in outs[0].text and store.sources("bio-201")[0].title == "Ch 3"


async def test_photo_in_a_course_topic_without_vision_keeps_the_caption(rig):
    router, store, client, state, _ = rig
    store.add_course(Course(path="courses/cs101.md", title="Intro to CS"))
    client.responses.append(ModelResponse(
        '{"title": "Whiteboard", "kind": "notes", "topics": ["stack"], "summary": "A stack diagram."}', []))

    outs = await router.on_photo(b"img", "stack diagram from class", channel=COURSE)
    assert "Stored: Whiteboard (photo, topics: stack) under Intro to CS. OCR unavailable." in outs[0].text
    source = store.sources("cs101")[0]
    assert source.kind == "photo" and source.ocr == "unavailable"
    assert source.body == "stack diagram from class"


async def test_photo_with_no_vision_and_no_caption_needs_no_model(rig):
    router, store, client, state, _ = rig
    store.add_course(Course(path="courses/cs101.md", title="Intro to CS"))
    outs = await router.on_photo(b"img", None, channel=COURSE)
    assert "Stored: Photo (photo) under Intro to CS. OCR unavailable." in outs[0].text
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
    assert _subject(store) == "ingest: Chapter 4"


async def test_ingest_caps_the_course_topics(rig, monkeypatch):
    router, store, client, state, _ = rig
    existing = [f"t{i}" for i in range(50)]
    store.add_course(Course(path="courses/cs101.md", title="Intro to CS", topics=existing))
    _pdf(monkeypatch, "Pointers hold addresses")
    client.responses.append(ModelResponse(DESCRIBED, []))

    await router.on_document(b"%PDF", "ch4.pdf", "application/pdf", None, channel=COURSE)
    assert store.get_course("cs101").topics == existing


async def test_a_markdown_upload_is_stored_as_notes(rig):
    router, store, client, state, _ = rig
    store.add_course(Course(path="courses/cs101.md", title="Intro to CS"))
    client.responses.append(ModelResponse(
        '{"title": "Lecture 4", "kind": "notes", "topics": ["pointers"], "summary": "Notes."}', []))

    outs = await router.on_document(
        "# Pointers\nThey hold addresses".encode(), "lecture4.md", "text/markdown", None,
        channel=COURSE,
    )
    assert outs[0].channel == "course:cs101"
    source = store.sources("cs101")[0]
    assert source.kind == "notes" and source.pages is None
    assert source.body == "# Pointers\nThey hold addresses"


async def test_a_text_upload_with_no_mime_type_is_stored_as_notes(rig):
    router, store, client, state, _ = rig
    store.add_course(Course(path="courses/cs101.md", title="Intro to CS"))
    client.responses.append(ModelResponse(
        '{"title": "Notes", "kind": "notes", "topics": [], "summary": ""}', []))

    await router.on_document("plain notes".encode(), "notes.txt", "", None, channel=COURSE)
    assert store.sources("cs101")[0].body == "plain notes"


async def test_a_missing_course_file_asks_for_a_rebind(rig):
    router, store, client, state, _ = rig
    outs = await router.on_document(b"x", "notes.txt", "text/plain", None, channel=COURSE)
    assert outs[0].text == "This topic's course file is gone; /bind again."
    assert store.sources("cs101") == [] and client.calls == []

    outs = await router.on_text("what is a pointer?", channel=COURSE)
    assert outs[0].text == "This topic's course file is gone; /bind again."


# -- auto-bind topics from their names -----------------------------------


def test_topic_named_binds_each_kind(rig):
    router, store, *_ = rig
    out = router.on_topic_named(-100, 10, "Homework")
    assert out[0].text == "Got it — this is your assignments topic."
    assert out[0].target == (-100, 10)
    assert store.channels().by_key("-100:10") == Channel(-100, 10, "assignments")

    out = router.on_topic_named(-100, 11, "Quizzes and Exams")
    assert out[0].text == "Got it — this is your exams topic."
    assert store.channels().by_key("-100:11").kind == "exams"

    out = router.on_topic_named(-100, 12, "Recall")
    assert out[0].text == "Got it — this is your review topic."
    assert store.channels().by_key("-100:12").kind == "review"


def test_topic_named_binds_a_course_from_its_name(rig):
    router, store, *_ = rig
    out = router.on_topic_named(-100, 20, "Bio 201 🧬")
    assert out[0].text == "Got it — this topic is Bio 201 🧬."
    assert store.channels().by_key("-100:20") == Channel(-100, 20, "course", "bio-201")
    assert store.get_course("bio-201").title == "Bio 201 🧬"


def test_topic_named_is_idempotent(rig):
    router, store, *_ = rig
    router.on_topic_named(-100, 30, "Assignments")
    assert router.on_topic_named(-100, 30, "Assignments") == []

    router.on_topic_named(-100, 31, "CS101")
    assert router.on_topic_named(-100, 31, "CS101") == []
    assert len(store.courses()) == 1


def test_topic_named_life_hint_never_binds(rig):
    router, store, *_ = rig
    out = router.on_topic_named(-100, 40, "General")
    assert out[0].text == (
        "Use your DM for life stuff; name this topic after a course to use it here."
    )
    assert out[0].target == (-100, 40)
    assert store.channels().by_key("-100:40") is None


def test_topic_named_renames_an_empty_course_in_place(rig):
    router, store, *_ = rig
    router.on_topic_named(-100, 50, "CS101")
    out = router.on_topic_named(-100, 50, "Intro to Computer Science")
    assert out[0].text == "Renamed: this topic is now Intro to Computer Science."
    channel = store.channels().by_key("-100:50")
    assert channel.course == "cs101"
    assert store.get_course("cs101").title == "Intro to Computer Science"


def test_topic_named_rename_leaves_a_course_with_sources_alone(rig):
    router, store, *_ = rig
    router.on_topic_named(-100, 60, "CS101")
    store.add_source(Source(path="", title="Ch1", course="cs101"))
    store.commit("ingest")

    out = router.on_topic_named(-100, 60, "Physics 101")
    assert out[0].text == "Renamed: this topic is now Physics 101."
    assert store.get_course("cs101").title == "CS101"
    channel = store.channels().by_key("-100:60")
    assert channel.course == "physics-101"
    assert store.get_course("physics-101").title == "Physics 101"


def test_topic_named_renames_between_kinds(rig):
    router, store, *_ = rig
    router.on_topic_named(-100, 70, "Assignments")
    out = router.on_topic_named(-100, 70, "Exams")
    assert out[0].text == "Renamed: this topic is now Exams."
    assert store.channels().by_key("-100:70").kind == "exams"


async def test_im_back_clears_the_pause(rig):
    router, store, client, state, _ = rig
    state.pause_until = NOW.replace(hour=20)
    client.responses.append(R(("snooze", {"minutes": 0}), ("reply", {"text": "Welcome back."})))
    await router.on_text("I'm back")
    assert state.pause_until is None


async def test_resume_button_clears_the_pause(rig):
    router, store, client, state, _ = rig
    state.pause_until = NOW.replace(hour=20)
    outs = await router.on_callback("resume")
    assert state.pause_until is None and outs[0].text == "Back on."


async def test_search_tool_answers_from_web_results(rig):
    router, store, client, state, _ = rig
    class FakeSearch:
        def __init__(self): self.queries = []
        async def search(self, q):
            self.queries.append(q); return "- Klaus building\n  https://gatech.edu/klaus\n  Open 7am-11pm"
    router.search = FakeSearch()
    client.responses.append(R(("search", {"query": "Klaus building hours"}), ("reply", {"text": "..."})))
    client.responses.append(ModelResponse("Open 7am to 11pm (https://gatech.edu/klaus).", []))
    outs = await router.on_text("when does klaus close?")
    assert router.search.queries == ["Klaus building hours"] and "7am to 11pm" in outs[0].text
    assert "gatech.edu/klaus" in client.calls[-1]["messages"][1]["content"]


async def test_named_places_switch_home(rig):
    router, store, client, state, _ = rig
    router.maps = FakeMaps({"123 Peachtree St": (33.77, -84.39), "45 Oak Lane": (33.90, -84.30)})
    client.responses.append(R(("save_place", {"name": "apartment", "address": "123 Peachtree St"}),
                              ("save_place", {"name": "parents", "address": "45 Oak Lane"}),
                              ("reply", {"text": "Saved."})))
    await router.on_text("my apartment is 123 Peachtree St and my parents' place is 45 Oak Lane")
    p = store.profile()
    assert p.base == "apartment" and p.home_address == "123 Peachtree St" and p.home_latlng is not None
    assert set(p.places) == {"apartment", "parents"}
    client.responses.append(R(("set_base", {"name": "parents"}), ("reply", {"text": "Ok."})))
    outs = await router.on_text("I'm at my parents' this weekend")
    p = store.profile()
    assert p.base == "parents" and p.home_address == "45 Oak Lane" and "Home is now: parents" in outs[0].text
    client.responses.append(R(("set_base", {"name": "the beach house"}), ("reply", {"text": "Ok."})))
    outs = await router.on_text("I'm at the beach house")
    assert "Couldn't apply set_base" in outs[0].text and store.profile().base == "parents"


async def test_undo_in_plain_words(rig):
    router, store, client, state, _ = rig
    client.responses.append(R(("add_todo", {"title": "Oops", "priority": 3}), ("reply", {"text": "Added."})))
    await router.on_text("add oops")
    client.responses.append(R(("undo", {}), ("reply", {"text": "Undone."})))
    outs = await router.on_text("undo that")
    assert outs[0].text.startswith("Reverted:") and not [t for t in store.todos() if t.title == "Oops"]


async def test_new_exam_gets_a_plan_and_daily_study_todos(rig):
    router, store, client, state, _ = rig
    store.add_course(Course(path="courses/cs101.md", title="Intro to CS"))
    store.add_source(Source(path="", title="Lecture 3", course="cs101", kind="slides", topics=["stacks"],
                            summary="Stacks and queues.", pages=40, body="x")); store.commit("s")
    exam_start = NOW.replace(day=6, hour=10, minute=0)  # three days out
    client.responses.append(R(("add_event", {"title": "CS101 Midterm", "start": exam_start.isoformat(),
                                             "kind": "exam", "course": "cs101", "topics": ["stacks"]}),
                              ("reply", {"text": "Got it."})))
    client.responses.append(ModelResponse(
        '{"days": [{"date": "2026-09-04", "minutes": 60, "task": "Read Lecture 3 slides 1-20"},'
        ' {"date": "2026-09-05", "minutes": 45, "task": "Practice stack problems"}],'
        ' "advice": "Doable at an hour a day."}', []))
    outs = await router.on_text("big exam on the 6th for cs101 on stacks, haven't learned any of it")
    text = outs[0].text
    assert "Plan for CS101 Midterm" in text and "3 days" in text and "Read Lecture 3" in text and "Doable" in text
    study = [t for t in store.todos() if t.kind == "study"]
    assert sorted(t.due for t in study) == [date(2026, 9, 4), date(2026, 9, 5)] and all(t.course == "cs101" for t in study)
    assert any("Days left: 3" in c["messages"][1]["content"] for c in client.calls)


async def test_recent_exchanges_are_passed_and_capped(rig):
    router, store, client, state, _ = rig
    for i in range(10):
        client.responses.append(R(("reply", {"text": f"ok {i}"})))
        await router.on_text(f"msg {i}")
    msgs = client.calls[-1]["messages"]
    assert msgs[2]["content"] == "msg 1" and msgs[2]["role"] == "user"  # window starts 8 exchanges back
    assert msgs[-2]["content"] == "ok 8" and msgs[-1]["content"] == "msg 9"
    assert len(state.recent) == 16


async def test_remember_then_recall(rig):
    router, store, client, state, _ = rig
    client.responses.append(R(("remember", {"fact": "Sam is his lab partner in CS101."}), ("reply", {"text": "Noted."})))
    outs = await router.on_text("remember that sam is my lab partner in cs101")
    assert "Remembered: Sam is his lab partner" in outs[0].text and len(store.memories()) == 1
    client.responses.append(R(("recall", {"query": "lab partner"}), ("reply", {"text": "..."})))
    client.responses.append(ModelResponse("Sam is your lab partner.", []))
    outs = await router.on_text("who did I say my lab partner was?")
    assert "Sam is your lab partner" in outs[0].text
    capture_ctx = client.calls[-2]["messages"][1]["content"]   # the capture that chose `recall`
    assert "Things I know about them:" in capture_ctx and "Sam is his lab partner" in capture_ctx
    assert "Sam is his lab partner" in client.calls[-1]["messages"][1]["content"]  # the recall answer context


async def test_recall_merges_semantic_hits(rig, tmp_path):
    from bot.memory.index import VectorIndex
    from tests.test_index import FakeEmbedder
    router, store, client, state, _ = rig
    store.add_memory("His dentist is Dr. Patel on Peachtree."); store.commit("m")
    router.index = VectorIndex(tmp_path / "idx.json", FakeEmbedder())
    hits = await router._recall("His dentist is Dr. Patel")
    assert any("Dr. Patel" in h for h in hits)


async def test_state_memory_expires_and_echoes_softly(rig):
    router, store, client, state, _ = rig
    client.responses.append(R(("remember", {"fact": "A girl who isn't replying is taking up your headspace.", "kind": "state"}),
                              ("reply", {"text": "Park it. What's due this week?"})))
    outs = await router.on_text("this girl is taking up all my bandwidth")
    assert "I'll keep that in mind." in outs[0].text and "headspace" not in outs[0].text
    m = store.memories()[0]
    assert m.kind == "state" and m.expires == NOW.date() + __import__("datetime").timedelta(days=30)
    client.responses.append(R(("reply", {"text": "Ok."})))
    await router.on_text("ok")
    ctx = client.calls[-1]["messages"][1]["content"]
    assert "On their mind lately:" in ctx and "headspace" in ctx and "Things I know about them:" not in ctx


async def test_pasted_conversation_goes_to_the_coach(rig):
    router, store, client, state, _ = rig
    thread = "her: haha maybe, busy week tho\nme: no worries"
    client.responses.append(R(("coach", {"thread": thread, "ask": "what now"}), ("reply", {"text": "..."})))
    client.responses.append(ModelResponse("Soft yes w/ an exit. Send: \"Thursday 4, the place on Bond. If not, next week.\" Then nothing.", []))
    outs = await router.on_text(f"she said: haha maybe, busy week tho. i said no worries. what now")
    assert "Thursday 4" in outs[0].text
    assert "Thread:\nher: haha maybe" in client.calls[-1]["messages"][-1]["content"]
    assert state.recent[-1][0] == "assistant" and "Thursday 4" in state.recent[-1][1]


async def test_chat_screenshot_in_dm_is_coached_not_filed(rig):
    router, store, client, state, _ = rig
    class Vision:
        def __init__(self): self.n = 0
        async def chat(self, messages, tools=None, temperature=0.2):
            self.n += 1
            if self.n == 1:  # look(): what is this
                return ModelResponse('{"kind": "chat", "description": "a text conversation"}', [])
            return ModelResponse("Her: busy week tho\nMe: cool", [])  # ocr()
    router.agent.vision = Vision()
    client.responses.append(ModelResponse("She's lukewarm. Send: \"Tuesday 4.\"", []))
    outs = await router.on_photo(b"img")
    assert "Tuesday 4" in outs[0].text and store.sources() == []


async def test_photo_of_friends_is_talked_about_not_filed(rig):
    router, store, client, state, _ = rig
    class Vision:
        async def chat(self, messages, tools=None, temperature=0.2):
            return ModelResponse('{"kind": "photo", "description": "two guys grinning at a bar, one holding a beer"}', [])
    router.agent.vision = Vision()
    client.responses.append(R(("reply", {"text": "Hi Jack. Hi Chris. The one w/ the beer is the problem."})))
    outs = await router.on_photo(b"img", "say hi to my friends jack and chris")
    assert "Hi Jack" in outs[0].text and store.sources() == []
    sent = client.calls[-1]["messages"][-1]["content"]
    assert sent.startswith("[sent a photo: two guys grinning") and "jack and chris" in sent
    assert state.recent[-2][0] == "user" and "two guys" in state.recent[-2][1]


async def test_weekly_class_gets_coordinates_and_travel_time_on_every_occurrence(rig):
    router, store, client, state, _ = rig
    p = store.profile(); p.home_latlng = (33.77, -84.39); store.save_profile(p)
    router.maps = FakeMaps({"Klaus Building": (33.78, -84.40)}, travel=25)
    client.responses.append(R(
        ("add_event", {"title": "CS 1332", "start": "2026-09-08T10:00:00-04:00", "location": "Klaus Building",
                       "repeat_days": ["TU", "TH"]}),
        ("reply", {"text": "Ok."}),
    ))
    out = await router.on_text("CS 1332 every Tue/Thu 10am at Klaus Building")
    tpl = store.series()[0]
    occurrences = [e for e in store.events() if e.series == tpl.path]
    assert tpl.location_latlng == (33.78, -84.40) and tpl.travel_minutes == 25
    assert occurrences and all(e.location_latlng == (33.78, -84.40) and e.travel_minutes == 25 for e in occurrences)
    assert router.maps.calls == ["Klaus Building"] and len(router.maps.travel_calls) == 1
    assert "Traffic from home 25 min, so leave by 9:35 AM." in out[0].text


async def test_single_event_travel_time_is_estimated_when_home_is_known(rig):
    router, store, client, state, _ = rig
    p = store.profile(); p.home_latlng = (33.77, -84.39); store.save_profile(p)
    router.maps = FakeMaps({"Equinox": (40.75, -73.99)}, travel=12)
    client.responses.append(R(
        ("add_event", {"title": "Gym", "start": "2026-09-04T18:00:00-04:00", "location": "Equinox"}),
        ("reply", {"text": "Ok."}),
    ))
    out = await router.on_text("gym tomorrow 6pm at Equinox")
    assert store.events()[0].travel_minutes == 12
    assert "leave by 5:48 PM" in out[0].text


async def test_event_with_location_but_no_home_asks_for_the_address(rig):
    router, store, client, state, _ = rig
    router.maps = FakeMaps({"Equinox": (40.75, -73.99)}, travel=12)
    client.responses.append(R(
        ("add_event", {"title": "Gym", "start": "2026-09-04T18:00:00-04:00", "location": "Equinox"}),
        ("reply", {"text": "Ok."}),
    ))
    out = await router.on_text("gym tomorrow 6pm at Equinox")
    assert "Tell me your home address" in out[0].text
    assert router.maps.travel_calls == []


async def test_asking_for_the_briefing_sends_it_with_voice_without_marking_it_fired(rig):
    router, store, client, state, _ = rig
    client.responses.append(R(("briefing", {"which": "morning"}), ("reply", {"text": "Here."})))
    client.responses.append(ModelResponse("Quiet day. Start with the reading.", []))
    outs = await router.on_text("what's my day look like")
    assert outs[0].kind == "briefing" and outs[0].voice is True
    assert "Quiet day" in outs[0].text
    assert not any(k.startswith("morning:") for k in state.fired)
    assert state.chain is None

    client.responses.append(R(("briefing", {"which": "evening"}), ("reply", {"text": "Here."})))
    client.responses.append(ModelResponse("Nothing closed today.", []))
    outs = await router.on_text("evening briefing")
    assert outs[0].kind == "briefing" and outs[0].voice is True


async def test_backup_model_declines_coaching_recall_study_and_photos(rig):
    router, store, client, state, _ = rig
    client.breaker_open = True
    client.responses.append(R(("coach", {"thread": "x", "ask": "y"}), ("reply", {"text": "…"})))
    outs = await router.on_text("she said x what now")
    assert outs[0].text.startswith("Spark's down.")
    outs = await router.on_photo(b"img", "look")
    assert outs[0].text.startswith("Spark's down.") and store.sources() == []


async def test_fallback_lines_are_not_remembered_as_his_own_words(rig):
    router, store, client, state, _ = rig
    class Boom:
        async def chat(self, *a, **k): raise ConnectionError("down")
    router.agent.client = Boom()
    outs = await router.on_text("hey")
    assert outs[0].text.startswith("Model's down")
    assert [r for r in state.recent if r[0] == "assistant"] == []
    assert state.recent[-1] == ["user", "hey"]
