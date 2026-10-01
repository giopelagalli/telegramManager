"""The web conversation survives a restart, stays bounded, and maps JD's HTML honestly."""
import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.scheduler.outbound import Outbound
from bot.web.audio import AudioStore
from bot.web.conversation import WebConversation
from bot.web.door import WebDoor
from bot.web.format import to_wire
from bot.web.server import build_app

from .conftest import AUTH, LIFE, TOKEN, FakeRouter, FakeSynthesizer, fake_transcode, make_conversation


async def test_history_and_ids_survive_a_restart(tmp_path):
    before = make_conversation(tmp_path)
    before.owner("milk")
    reply = await before.render(Outbound("<b>Added</b> milk", buttons=[("Done", "done:x")]))

    after = make_conversation(tmp_path)  # a new process reading the same DATA_DIR
    assert after.history(50) == before.history(50)
    assert after.owner("next")["id"] == str(int(reply["id"]) + 1)  # ids never repeat
    edit = await after.render(Outbound("<s>milk</s>", edit_message_id=int(reply["id"])))
    assert edit["edit"] is True and edit["id"] == reply["id"]


async def test_a_restarted_server_serves_the_same_history(tmp_path):
    def server():
        return TestClient(TestServer(build_app(WebDoor(FakeRouter(), make_conversation(tmp_path), LIFE), TOKEN)))

    async with server() as first:
        await first.post("/messages", headers=AUTH, json={"text": "hello"})
        sent = (await (await first.get("/history", headers=AUTH)).json())["messages"]
    async with server() as second:
        assert (await (await second.get("/history", headers=AUTH)).json())["messages"] == sent
    assert len(sent) == 2


async def test_history_is_capped(tmp_path):
    conversation = WebConversation(tmp_path / "web.json", cap=3)
    for n in range(5):
        conversation.owner(str(n))
    assert [m["text"] for m in WebConversation(tmp_path / "web.json", cap=3).history(10)] == ["2", "3", "4"]


async def test_a_corrupt_file_starts_empty(tmp_path):
    (tmp_path / "web.json").write_text("{nope")
    assert WebConversation(tmp_path / "web.json").history(10) == []


async def test_reply_audio_is_bounded(tmp_path):
    store = AudioStore(tmp_path / "audio", fake_transcode, keep_count=2)
    conversation = WebConversation(tmp_path / "web.json", store, FakeSynthesizer(), tmp_path / "tmp")
    ids = [(await conversation.render(Outbound(f"n{i}", voice=True)))["audio"]["id"] for i in range(3)]
    assert store.path(ids[0]) is None and store.path(ids[2]) is not None
    assert list((tmp_path / "tmp").iterdir()) == []  # the OGG voice notes are cleaned up


async def test_a_failed_synthesis_still_answers(tmp_path):
    class Broken:
        async def synthesize(self, text, out_dir):
            raise RuntimeError("kokoro out of memory")

    conversation = WebConversation(tmp_path / "web.json", AudioStore(tmp_path / "a", fake_transcode), Broken())
    message = await conversation.render(Outbound("hi", voice=True))
    assert message["text"] == "hi" and "audio" not in message


@pytest.mark.parametrize("telegram,wire", [
    ("<b>Today</b>\n<i>3 things</i>", ("<b>Today</b>\n<i>3 things</i>", "html")),
    ('<a href="https://maps.google.com/?q=a&amp;b">Go</a>', ('<a href="https://maps.google.com/?q=a&amp;b">Go</a>', "html")),
    ("<strong>x</strong> <em>y</em> <del>z</del> <ins>w</ins>", ("<b>x</b> <i>y</i> <s>z</s> <u>w</u>", "html")),
    ("<pre>a &lt; b</pre> <code>c</code>", ("<pre>a &lt; b</pre> <code>c</code>", "html")),
    ("<blockquote>quoted <b>bit</b></blockquote>", ("quoted <b>bit</b>", "html")),
    ("milk &amp; eggs", ("milk & eggs", "plain")),
    ("use <course> here", ("use  here", "plain")),  # Telegram refuses an unknown tag; so do we
    ("<b>open", ("open", "plain")),
])
def test_telegram_html_maps_to_the_wire_subset(telegram, wire):
    assert to_wire(telegram) == wire
