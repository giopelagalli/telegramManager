from unittest.mock import AsyncMock

from bot.agent.client import OpenAIModelClient


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
