"""Every route of the 0069 contract, against the fake router."""
import pytest

from bot.scheduler.outbound import Outbound
from bot.web.server import MAX_BODY

from .conftest import AUTH, LIFE, TOKEN

ROUTES = [
    ("GET", "/health"), ("GET", "/history"), ("POST", "/messages"), ("POST", "/callback"),
    ("POST", "/voice"), ("GET", "/audio/" + "0" * 32), ("GET", "/keys"), ("GET", "/stream"),
]


@pytest.mark.parametrize("method,path", ROUTES)
@pytest.mark.parametrize("header", [None, "Bearer wrong", f"Basic {TOKEN}", f"Bearer {TOKEN}x"])
async def test_every_route_wants_the_bearer(client, router, method, path, header):
    headers = {} if header is None else {"Authorization": header}
    response = await client.request(method, path, headers=headers, json={"text": "hi", "data": "x"})
    assert response.status == 401
    assert router.calls == []


async def test_health_names_jd(client):
    response = await client.get("/health", headers=AUTH)
    assert response.status == 200
    assert await response.json() == {"ok": True, "name": "JD"}


async def test_keys_are_the_quick_keys(client):
    assert await (await client.get("/keys", headers=AUTH)).json() == {"keys": ["Now", "Today"]}


async def test_a_message_is_echoed_then_answered_through_the_router(client, router):
    response = await client.post("/messages", headers=AUTH, json={"text": "buy milk & eggs"})
    assert response.status == 200
    owner, reply = (await response.json())["messages"]
    assert owner["from"] == "owner" and owner["text"] == "buy milk & eggs" and owner["format"] == "plain"
    assert reply["from"] == "jd" and reply["format"] == "html"
    assert reply["text"] == "<b>Got</b> buy milk &amp; eggs"
    assert reply["buttons"] == [[{"label": "Done", "data": "done:x"}], [{"label": "Later", "data": "defer:x"}]]
    assert isinstance(reply["at"], int) and int(reply["id"]) > int(owner["id"])
    assert "audio" not in reply and "edit" not in reply
    assert router.calls == [("text", "buy milk & eggs", False, LIFE)]


async def test_a_slash_command_goes_to_the_command_handler(client, router):
    replies = (await (await client.post("/messages", headers=AUTH, json={"text": "/schedule  add   gym"})).json())["messages"]
    assert router.calls == [("command", "schedule", "add gym", LIFE)]
    assert replies[1]["text"] == "/schedule add gym"
    await client.post("/messages", headers=AUTH, json={"text": "/notacommand hi"})
    assert router.calls[-1][0] == "text"


async def test_bad_bodies_are_400(client, router):
    assert (await client.post("/messages", headers=AUTH, data=b"not json")).status == 400
    assert (await client.post("/messages", headers=AUTH, json={"text": "  "})).status == 400
    assert (await client.post("/callback", headers=AUTH, json={})).status == 400
    assert router.calls == []


async def test_history_is_the_web_conversation_oldest_first(client):
    await client.post("/messages", headers=AUTH, json={"text": "one"})
    await client.post("/messages", headers=AUTH, json={"text": "two"})
    everything = (await (await client.get("/history", headers=AUTH)).json())["messages"]
    assert [m["text"] for m in everything] == ["one", "<b>Got</b> one", "two", "<b>Got</b> two"]
    last = (await (await client.get("/history?limit=1", headers=AUTH)).json())["messages"]
    assert last == everything[-1:]


async def test_a_button_tap_edits_its_message_in_place(client, router):
    reply = (await (await client.post("/messages", headers=AUTH, json={"text": "milk"})).json())["messages"][1]
    response = await client.post("/callback", headers=AUTH, json={"data": "done:x"})
    assert response.status == 200
    [edit] = (await response.json())["messages"]
    assert edit["edit"] is True and edit["id"] == reply["id"] and edit["at"] == reply["at"]
    assert edit["text"] == "<s><b>Got</b> milk</s>"
    assert edit["buttons"] == [[{"label": "Later", "data": "defer:x"}]]
    # The dispatcher saw what Telegram would have given it: the message id, its HTML, its buttons.
    assert router.calls[-1] == ("callback", "done:x", int(reply["id"]), "<b>Got</b> milk",
                                [("Done", "done:x"), ("Later", "defer:x")], LIFE)
    history = (await (await client.get("/history", headers=AUTH)).json())["messages"]
    assert history[-1] == {k: v for k, v in edit.items() if k != "edit"}


async def test_a_button_nobody_sent_reaches_the_dispatcher_without_a_message(client, router):
    [reply] = (await (await client.post("/callback", headers=AUTH, json={"data": "proj:list"})).json())["messages"]
    assert router.calls == [("callback", "proj:list", None, None, None, LIFE)]
    assert "edit" not in reply


async def test_voice_is_transcribed_answered_and_spoken_back(client, router, transcriber, synthesizer):
    response = await client.post("/voice", headers={**AUTH, "Content-Type": "audio/webm;codecs=opus"}, data=b"WEBM")
    assert response.status == 200
    body = await response.json()
    assert body["transcript"] == "remind me to call mom"
    assert transcriber.seen == [(".webm", b"WEBM")]
    owner, reply = body["messages"]
    assert owner == {**owner, "from": "owner", "text": "remind me to call mom"}
    assert router.calls == [("text", "remind me to call mom", True, LIFE)]
    assert synthesizer.texts == ["Got remind me to call mom"]
    assert reply["audio"]["mime"] == "audio/mp4"
    audio = await client.get(f"/audio/{reply['audio']['id']}", headers=AUTH)
    assert audio.status == 200
    assert audio.headers["Content-Type"] == "audio/mp4"
    assert await audio.read() == b"m4a:OggS-fake"


@pytest.mark.parametrize("mime,suffix", [("audio/mp4", ".mp4"), ("audio/ogg", ".ogg")])
async def test_voice_takes_safari_and_telegram_containers(client, transcriber, mime, suffix):
    assert (await client.post("/voice", headers={**AUTH, "Content-Type": mime}, data=b"A")).status == 200
    assert transcriber.seen == [(suffix, b"A")]


async def test_voice_refuses_other_types_and_reports_a_failed_transcription(client, router, transcriber):
    assert (await client.post("/voice", headers={**AUTH, "Content-Type": "video/mp4"}, data=b"A")).status == 415
    transcriber.fail = True
    body = await (await client.post("/voice", headers={**AUTH, "Content-Type": "audio/ogg"}, data=b"A")).json()
    assert body["transcript"] == ""
    assert router.calls == [("voice_failed", "whisper fell over")]
    assert body["messages"][0]["text"].startswith("Couldn't transcribe")


async def test_unknown_audio_is_404(client):
    assert (await client.get("/audio/" + "f" * 32, headers=AUTH)).status == 404
    assert (await client.get("/audio/..%2Fweb.json", headers=AUTH)).status == 404


@pytest.mark.parametrize("path,headers", [("/messages", {"Content-Type": "application/json"}),
                                          ("/voice", {"Content-Type": "audio/webm"})])
async def test_bodies_over_ten_megabytes_are_413(client, router, path, headers):
    big = b"x" * (MAX_BODY + 1)
    assert (await client.post(path, headers={**AUTH, **headers}, data=big)).status == 413

    async def chunked():  # no Content-Length: the cap still holds while reading
        yield big

    assert (await client.post(path, headers={**AUTH, **headers}, data=chunked())).status == 413
    assert router.calls == []


async def test_the_stream_gets_typing_and_proactive_messages_but_not_replies(client, door):
    ws = await client.ws_connect("/stream", headers=AUTH)
    await client.post("/messages", headers=AUTH, json={"text": "hi"})
    assert await ws.receive_json() == {"type": "typing", "on": True}
    assert await ws.receive_json() == {"type": "typing", "on": False}
    await door.conversation.send(Outbound("<b>Morning</b>", buttons=[("Ok", "ack:later")], kind="briefing"))
    event = await ws.receive_json()
    assert event["type"] == "message"
    assert event["message"]["text"] == "<b>Morning</b>" and event["message"]["from"] == "jd"
    assert event["message"]["buttons"] == [[{"label": "Ok", "data": "ack:later"}]]
    await ws.close()


async def test_the_stream_refuses_a_wrong_bearer(client):
    response = await client.get("/stream", headers={"Authorization": "Bearer nope"})
    assert response.status == 401


async def test_a_message_over_telegrams_4096_characters_is_400(client, router):
    response = await client.post("/messages", headers=AUTH, json={"text": "x" * 4097})
    assert response.status == 400
    assert "4096" in (await response.json())["error"]
    assert router.calls == [] and (await (await client.get("/history", headers=AUTH)).json())["messages"] == []
    assert (await client.post("/messages", headers=AUTH, json={"text": "x" * 4096})).status == 200
