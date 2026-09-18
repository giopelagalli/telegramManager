from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Callable, Protocol

import openai
from openai import NOT_GIVEN, AsyncOpenAI

logger = logging.getLogger(__name__)


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
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        enable_thinking: bool | None = None,
        extra_body: dict | None = None,
        timeout: float = 120.0,
    ):
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        self.model = model
        self.enable_thinking = enable_thinking
        self.extra_body = extra_body

    async def alive(self) -> bool:
        """Cheap reachability check (GET /models), for noticing the Spark going down or coming back."""
        try:
            await self._client.with_options(timeout=5.0).models.list()
            return True
        except Exception:
            return False

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.2,
    ) -> ModelResponse:
        extra_kwargs = {}
        merged_extra_body = dict(self.extra_body or {})
        if self.enable_thinking is not None:
            chat_template_kwargs = dict(merged_extra_body.get("chat_template_kwargs") or {})
            chat_template_kwargs["enable_thinking"] = self.enable_thinking
            merged_extra_body["chat_template_kwargs"] = chat_template_kwargs
        if merged_extra_body:
            extra_kwargs["extra_body"] = merged_extra_body
        response = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools or NOT_GIVEN,
            tool_choice="auto" if tools else NOT_GIVEN,
            temperature=temperature,
            **extra_kwargs,
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


_FALLBACK_ERRORS = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,
    asyncio.TimeoutError,
)


class FallbackModelClient:
    """Routes to a secondary client while the primary looks unreachable."""

    def __init__(
        self,
        primary: ModelClient,
        fallback: ModelClient,
        cooldown_seconds: float = 600.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._primary = primary
        self._fallback = fallback
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._open_until = 0.0

    @property
    def primary(self) -> ModelClient:
        return self._primary

    @property
    def breaker_open(self) -> bool:
        return self._clock() < self._open_until

    async def probe(self) -> bool:
        """Ask the primary directly whether it is up, and reset the breaker to match. True = primary."""
        alive = getattr(self._primary, "alive", None)
        if alive is None:
            return not self.breaker_open
        if await alive():
            self._open_until = 0.0
            return True
        self._open_until = self._clock() + self._cooldown_seconds
        return False

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.2,
    ) -> ModelResponse:
        if not self.breaker_open:
            try:
                return await self._primary.chat(messages, tools, temperature)
            except _FALLBACK_ERRORS as exc:
                self._open_until = self._clock() + self._cooldown_seconds
                logger.warning(
                    "primary model unreachable (%s); falling back for %.0fs",
                    type(exc).__name__,
                    self._cooldown_seconds,
                )
        return await self._fallback.chat(messages, tools, temperature)


class FakeModelClient:
    def __init__(self, responses: list[ModelResponse], model: str = "fake-model"):
        self.responses: list[ModelResponse] = list(responses)
        self.calls: list[dict] = []
        self.model = model
        self.enable_thinking: bool | None = None

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.2,
    ) -> ModelResponse:
        self.calls.append({"messages": messages, "tools": tools, "temperature": temperature})
        return self.responses.pop(0)
