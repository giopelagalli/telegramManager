from unittest.mock import AsyncMock

import httpx
import openai
import pytest

from bot.agent.client import FallbackModelClient, ModelResponse, OpenAIModelClient


def _make_response():
    message = AsyncMock()
    message.content = "hi"
    message.tool_calls = []
    response = AsyncMock()
    response.choices = [AsyncMock(message=message)]
    return response


async def test_enable_thinking_false_sets_extra_body():
    client = OpenAIModelClient("http://x", "k", "m", enable_thinking=False)
    client._client.chat.completions.create = AsyncMock(return_value=_make_response())

    await client.chat([{"role": "user", "content": "hi"}])

    _, kwargs = client._client.chat.completions.create.call_args
    assert kwargs["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


async def test_enable_thinking_none_omits_extra_body():
    client = OpenAIModelClient("http://x", "k", "m", enable_thinking=None)
    client._client.chat.completions.create = AsyncMock(return_value=_make_response())

    await client.chat([{"role": "user", "content": "hi"}])

    _, kwargs = client._client.chat.completions.create.call_args
    assert "extra_body" not in kwargs


async def test_extra_body_only_is_passed_through():
    client = OpenAIModelClient(
        "http://x", "k", "m", enable_thinking=None, extra_body={"reasoning_effort": "high"}
    )
    client._client.chat.completions.create = AsyncMock(return_value=_make_response())

    await client.chat([{"role": "user", "content": "hi"}])

    _, kwargs = client._client.chat.completions.create.call_args
    assert kwargs["extra_body"] == {"reasoning_effort": "high"}


async def test_enable_thinking_only_sets_chat_template_kwargs():
    client = OpenAIModelClient("http://x", "k", "m", enable_thinking=True, extra_body=None)
    client._client.chat.completions.create = AsyncMock(return_value=_make_response())

    await client.chat([{"role": "user", "content": "hi"}])

    _, kwargs = client._client.chat.completions.create.call_args
    assert kwargs["extra_body"] == {"chat_template_kwargs": {"enable_thinking": True}}


async def test_enable_thinking_and_extra_body_merge_with_thinking_winning():
    client = OpenAIModelClient(
        "http://x",
        "k",
        "m",
        enable_thinking=True,
        extra_body={
            "chat_template_kwargs": {"enable_thinking": False, "other_key": "keep"},
            "unrelated": "stays",
        },
    )
    client._client.chat.completions.create = AsyncMock(return_value=_make_response())

    await client.chat([{"role": "user", "content": "hi"}])

    _, kwargs = client._client.chat.completions.create.call_args
    assert kwargs["extra_body"] == {
        "chat_template_kwargs": {"enable_thinking": True, "other_key": "keep"},
        "unrelated": "stays",
    }


class _Recorder:
    def __init__(self, raises: Exception | None = None):
        self.raises = raises
        self.calls = 0

    async def chat(self, messages, tools=None, temperature=0.2):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return ModelResponse(text="ok", tool_calls=[])


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _connection_error() -> openai.APIConnectionError:
    return openai.APIConnectionError(request=httpx.Request("POST", "http://x"))


async def test_fallback_uses_primary_when_healthy():
    primary, fallback = _Recorder(), _Recorder()
    client = FallbackModelClient(primary, fallback)

    assert (await client.chat([])).text == "ok"
    assert (primary.calls, fallback.calls) == (1, 0)
    assert client.breaker_open is False


async def test_connection_error_opens_breaker_and_uses_fallback():
    primary, fallback = _Recorder(raises=_connection_error()), _Recorder()
    clock = _Clock()
    client = FallbackModelClient(primary, fallback, cooldown_seconds=600.0, clock=clock)

    assert (await client.chat([])).text == "ok"
    assert (primary.calls, fallback.calls) == (1, 1)
    assert client.breaker_open is True

    await client.chat([])
    assert (primary.calls, fallback.calls) == (1, 2)

    clock.now = 600.0
    await client.chat([])
    assert primary.calls == 2


async def test_fallback_client_exposes_primary():
    primary, fallback = _Recorder(), _Recorder()
    client = FallbackModelClient(primary, fallback)
    assert client.primary is primary


async def test_other_exception_propagates_without_opening_breaker():
    primary, fallback = _Recorder(raises=ValueError("boom")), _Recorder()
    client = FallbackModelClient(primary, fallback)

    with pytest.raises(ValueError):
        await client.chat([])
    assert fallback.calls == 0
    assert client.breaker_open is False


async def test_probe_resets_the_breaker_from_the_primary_answer():
    from bot.agent.client import FallbackModelClient
    class Primary:
        def __init__(self): self.up = False
        async def alive(self): return self.up
        async def chat(self, *a, **k): raise AssertionError("not used")
    primary = Primary()
    t = [0.0]
    fb = FallbackModelClient(primary, primary, cooldown_seconds=600, clock=lambda: t[0])
    assert await fb.probe() is False and fb.breaker_open
    primary.up = True
    assert await fb.probe() is True and not fb.breaker_open


async def test_leading_system_messages_are_folded_into_one():
    from unittest.mock import AsyncMock
    from bot.agent.client import OpenAIModelClient
    client = OpenAIModelClient("http://x/v1", "k", "m")
    client._client.chat.completions.create = AsyncMock(return_value=_make_response())
    await client.chat([
        {"role": "system", "content": "rules"},
        {"role": "system", "content": "context"},
        {"role": "user", "content": "hey"},
        {"role": "assistant", "content": "yo"},
        {"role": "system", "content": "late note stays where it is"},
    ])
    sent = client._client.chat.completions.create.call_args.kwargs["messages"]
    assert sent[0] == {"role": "system", "content": "rules\n\ncontext"}
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "system"]


async def test_think_blocks_never_reach_the_reply():
    from unittest.mock import AsyncMock
    from bot.agent.client import OpenAIModelClient
    from bot.agent.client import _strip_thinking
    assert _strip_thinking("<think>\nhmm\n</think>\n\nPong.") == "Pong."
    assert _strip_thinking("<think>") is None and _strip_thinking("") == "" and _strip_thinking(None) is None
    client = OpenAIModelClient("http://x/v1", "k", "m")
    resp = _make_response()
    resp.choices[0].message.content = "<think>reasoning</think>Hey."
    client._client.chat.completions.create = AsyncMock(return_value=resp)
    assert (await client.chat([{"role": "user", "content": "hi"}])).text == "Hey."
