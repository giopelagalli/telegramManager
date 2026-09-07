from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from openai import NOT_GIVEN, AsyncOpenAI


@dataclass
class ToolCall:
    name: str
    arguments: dict


@dataclass
class ModelResponse:
    text: str | None
    tool_calls: list[ToolCall]


class ModelClient(Protocol):
    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.2,
    ) -> ModelResponse: ...


class OpenAIModelClient:
    def __init__(self, base_url: str, api_key: str, model: str):
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self._model = model

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.2,
    ) -> ModelResponse:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            tools=tools or NOT_GIVEN,
            tool_choice="auto" if tools else NOT_GIVEN,
            temperature=temperature,
        )
        message = response.choices[0].message
        tool_calls = []
        for tc in message.tool_calls or []:
            try:
                arguments = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError):
                arguments = {"__invalid_json__": tc.function.arguments}
            tool_calls.append(ToolCall(name=tc.function.name, arguments=arguments))
        return ModelResponse(text=message.content, tool_calls=tool_calls)


class FakeModelClient:
    def __init__(self, responses: list[ModelResponse]):
        self.responses: list[ModelResponse] = list(responses)
        self.calls: list[dict] = []

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.2,
    ) -> ModelResponse:
        self.calls.append({"messages": messages, "tools": tools, "temperature": temperature})
        return self.responses.pop(0)
