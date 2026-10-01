"""A busy port turns the web door off; it never takes Telegram down with it."""
import logging
import socket
from types import SimpleNamespace

from bot.__main__ import _open_web_door
from bot.web.door import WebDoor

from .conftest import LIFE, FakeRouter, make_conversation


async def test_a_busy_port_is_logged_and_skipped(tmp_path, caplog):
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        settings = SimpleNamespace(jd_web_token="t", jd_web_host="127.0.0.1", jd_web_port=port)
        with caplog.at_level(logging.ERROR):
            runner = await _open_web_door(WebDoor(FakeRouter(), make_conversation(tmp_path), LIFE), settings)
    assert runner is None
    assert "web door off" in caplog.text


async def test_a_free_port_opens_the_door(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    settings = SimpleNamespace(jd_web_token="t", jd_web_host="127.0.0.1", jd_web_port=port)
    runner = await _open_web_door(WebDoor(FakeRouter(), make_conversation(tmp_path), LIFE), settings)
    assert runner is not None
    await runner.cleanup()
