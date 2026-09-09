from __future__ import annotations

import json
import re
from base64 import b64encode
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from bot.agent.client import ModelClient, ToolCall
from bot.agent.prompts import (
    ANSWER_SYSTEM,
    CAPTURE_SYSTEM,
    COMPOSE_SYSTEM,
    DESCRIBE_SOURCE_SYSTEM,
    OCR_PROMPT,
    TUTOR_SYSTEM,
    build_context,
)
from bot.agent.tools import TOOL_SCHEMAS, TUTOR_TOOLS, validate_call
from bot.knowledge.models import SOURCE_KINDS, Course, Event, Goal, Source, Todo
from bot.knowledge.store import KnowledgeStore
from bot.study.extract import guess_kind

_RATE_SYSTEM = 'Rate whether the answer is specific or vague. Respond with exactly one word: SPECIFIC or VAGUE.'
DESCRIBE_HEAD_CHARS = 12_000
MAX_TOPICS = 10


@dataclass
class CaptureResult:
    actions: list[ToolCall]
    reply: str
    parsed: bool


@dataclass
class Applied:
    summary: list[str]
    snooze_minutes: int | None
    changed_schedule: bool


class Agent:
    def __init__(
        self,
        client: ModelClient,
        vision: ModelClient | None,
        store: KnowledgeStore,
        clock: Callable[[], datetime],
        hard: ModelClient | None = None,
    ):
        self.client = client
        self.vision = vision
        self.store = store
        self.clock = clock
        self.hard = hard

    async def capture(self, text: str, awaiting: str | None = None) -> CaptureResult:
        now = self.clock()
        profile = self.store.profile()
        system = CAPTURE_SYSTEM.format(name=profile.name, now=now.isoformat())
        context = build_context(self.store, now, awaiting)
        messages = [
            {"role": "system", "content": system},
            {"role": "system", "content": context},
            {"role": "user", "content": text},
        ]

        response = await self._chat_or_none(messages, TOOL_SCHEMAS, 0.1)
        if response is None:
            return self._to_inbox(text, "The model is offline; saved your message to the inbox.")

        errors = self._check(response.tool_calls)
        if errors:
            retry_messages = messages + [
                {"role": "assistant", "content": self._raw_calls_repr(response.tool_calls)},
                {
                    "role": "user",
                    "content": "Tool call errors: "
                    + "; ".join(errors)
                    + ". Resend ALL tool calls, fixed, and include exactly one reply call.",
                },
            ]
            response = await self._chat_or_none(retry_messages, TOOL_SCHEMAS, 0.1)
            if response is None:
                return self._to_inbox(text, "The model is offline; saved your message to the inbox.")
            errors = self._check(response.tool_calls)

        if errors:
            return self._to_inbox(text, "Saved that, but I couldn't parse it. It's in your inbox.")

        reply_text = self._reply_text(response.tool_calls)
        return CaptureResult(response.tool_calls, reply_text, parsed=True)

    async def _chat_or_none(self, messages, tools, temperature, client: ModelClient | None = None):
        try:
            return await (client or self.client).chat(messages, tools=tools, temperature=temperature)
        except Exception:
            return None

    def _to_inbox(self, text: str, reply: str) -> CaptureResult:
        self.store.add_inbox(text)
        self.store.commit("inbox: saved unparsed message")
        return CaptureResult([], reply, parsed=False)

    @staticmethod
    def _check(tool_calls: list[ToolCall]) -> list[str]:
        errors: list[str] = []
        reply_count = 0
        for call in tool_calls:
            errors.extend(f"{call.name}: {e}" for e in validate_call(call.name, call.arguments))
            if call.name == "reply":
                reply_count += 1
        if reply_count != 1:
            errors.append(f"expected exactly one reply call, got {reply_count}")
        return errors

    @staticmethod
    def _raw_calls_repr(tool_calls: list[ToolCall]) -> str:
        return "; ".join(f"{c.name}({c.arguments})" for c in tool_calls)

    @staticmethod
    def _reply_text(tool_calls: list[ToolCall]) -> str:
        for call in tool_calls:
            if call.name == "reply":
                return call.arguments.get("text", "")
        return ""

    async def compose(self, kind: str, context: str, fallback: str) -> str:
        try:
            response = await self.client.chat(
                [
                    {"role": "system", "content": COMPOSE_SYSTEM},
                    {"role": "user", "content": f"Kind: {kind}\n{context}"},
                ],
                tools=None,
                temperature=0.6,
            )
            text = (response.text or "").strip()
        except Exception:
            return fallback
        if not text:
            return fallback
        return _truncate(text, 600)

    async def check_photo(self, image_bytes: bytes, expectation: str) -> tuple[bool | None, str]:
        if self.vision is None:
            return None, "no vision model configured"
        data_url = f"data:image/jpeg;base64,{b64encode(image_bytes).decode()}"
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f'Does this photo show: {expectation}? '
                        'Respond with JSON: {"ok": true or false, "reason": "..."}.',
                    },
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ]
        try:
            response = await self.vision.chat(messages, tools=None, temperature=0.0)
        except Exception:
            return None, "vision unavailable"
        text = response.text or ""
        match = re.search(r'"ok"\s*:\s*(true|false)', text, re.IGNORECASE)
        if not match:
            return None, "could not parse vision response"
        return match.group(1).lower() == "true", text.strip()

    async def describe_source(self, text_head: str, filename: str, course_title: str) -> dict:
        """{title, kind, topics, summary} for an ingested file; falls back to the filename."""
        messages = [
            {
                "role": "system",
                "content": DESCRIBE_SOURCE_SYSTEM.format(
                    course=course_title, kinds=", ".join(SOURCE_KINDS)
                ),
            },
            {
                "role": "user",
                "content": f"Filename: {filename}\n\n{text_head[:DESCRIBE_HEAD_CHARS]}",
            },
        ]
        for _attempt in range(2):
            response = await self._chat_or_none(messages, None, 0.1)
            if response is None:
                break
            described = _parse_description(response.text or "")
            if described is not None:
                return described
        return {
            "title": Path(filename).stem or filename,
            "kind": guess_kind(filename, ""),
            "topics": [],
            "summary": "",
        }

    async def ocr(self, image: bytes) -> str | None:
        if self.vision is None:
            return None
        data_url = f"data:image/jpeg;base64,{b64encode(image).decode()}"
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": OCR_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ]
        try:
            response = await self.vision.chat(messages, tools=None, temperature=0.0)
        except Exception:
            return None
        return (response.text or "").strip() or None

    async def tutor(
        self,
        question: str,
        course: Course,
        sources: list[Source],
        notes_tool: bool = True,
        client: ModelClient | None = None,
    ) -> tuple[str | None, dict | None]:
        """(answer, save_note arguments). Both None when the model is unreachable."""
        profile = self.store.profile()
        messages = [
            {
                "role": "system",
                "content": TUTOR_SYSTEM.format(name=profile.name, course=course.title),
            },
            {"role": "system", "content": _render_sources(sources)},
            {"role": "user", "content": question},
        ]
        response = await self._chat_or_none(messages, TUTOR_TOOLS if notes_tool else None, 0.3, client=client)
        if response is None:
            return None, None
        note = next((_note_args(c) for c in response.tool_calls if c.name == "save_note"), None)
        return (response.text or "").strip() or None, note

    async def answer(self, question: str, context: str, client: ModelClient | None = None) -> str | None:
        """Plain grounded Q&A, no tools. None when the model is unreachable."""
        messages = [
            {"role": "system", "content": ANSWER_SYSTEM},
            {"role": "system", "content": context},
            {"role": "user", "content": question},
        ]
        response = await self._chat_or_none(messages, None, 0.3, client=client)
        if response is None:
            return None
        return (response.text or "").strip() or None

    async def rate_answer(self, question: str, answer: str) -> bool:
        try:
            response = await self.client.chat(
                [
                    {"role": "system", "content": _RATE_SYSTEM},
                    {"role": "user", "content": f"Question: {question}\nAnswer: {answer}"},
                ],
                tools=None,
                temperature=0.0,
            )
            text = (response.text or "").strip().upper()
        except Exception:
            return True
        return "VAGUE" not in text


def _parse_description(text: str) -> dict | None:
    """The first {...} block, if it carries a known kind and a list of short topics."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("kind") not in SOURCE_KINDS:
        return None
    topics = data.get("topics", [])
    if not isinstance(topics, list) or any(not isinstance(t, str) for t in topics):
        return None
    return {
        "title": str(data.get("title", "")).strip(),
        "kind": data["kind"],
        "topics": [t.strip() for t in topics if t.strip()][:MAX_TOPICS],
        "summary": str(data.get("summary", "")).strip(),
    }


def _note_args(call: ToolCall) -> dict | None:
    text = call.arguments.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    topics = call.arguments.get("topics") or []
    if not isinstance(topics, list):
        topics = []
    return {
        "text": text.strip(),
        "topics": [t.strip() for t in topics if isinstance(t, str) and t.strip()][:MAX_TOPICS],
    }


def _render_sources(sources: list[Source]) -> str:
    if not sources:
        return "Sources: none for this course yet."
    blocks = [f"### {s.title} ({s.kind})\n{s.body}" for s in sources]
    return "Sources:\n\n" + "\n\n".join(blocks)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    truncated = text[:limit]
    idx = max(truncated.rfind("."), truncated.rfind("!"), truncated.rfind("?"))
    if idx != -1:
        return truncated[: idx + 1]
    return truncated.rstrip()


def _fmt_due(d: date) -> str:
    return f"{d:%a %b} {d.day}"


def _fmt_clock(dt: datetime) -> str:
    hour12 = dt.hour % 12 or 12
    ampm = "am" if dt.hour < 12 else "pm"
    return f"{hour12}:{dt.minute:02d}{ampm}"


def _fmt_event_start(dt: datetime) -> str:
    return f"{dt:%a %b} {dt.day} {_fmt_clock(dt)}"


def _coerce_profile_value(current, raw):
    if isinstance(current, bool):
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if isinstance(current, int):
        return int(raw)
    if isinstance(current, list):
        return raw if isinstance(raw, list) else [raw]
    return str(raw)


def apply_actions(store: KnowledgeStore, actions: list[ToolCall], now: datetime) -> Applied:
    summary: list[str] = []
    snooze_minutes: int | None = None
    changed_schedule = False
    renamed: dict[str, str] = {}
    first_success: str | None = None

    for action in actions:
        args = dict(action.arguments)
        if "file" in args:
            args["file"] = renamed.get(args["file"], args["file"])
        before = len(summary)

        try:
            if action.name == "add_todo":
                todo = Todo(
                    path="",
                    title=args["title"],
                    priority=args["priority"],
                    due=date.fromisoformat(args["due"]) if args.get("due") else None,
                    goal=args.get("goal"),
                    verify=args.get("verify", "none"),
                    body=args.get("notes", ""),
                )
                store.add(todo, folder="backlog" if args.get("backlog") else None)
                due_part = f", due {_fmt_due(todo.due)}" if todo.due else ""
                summary.append(f"Added todo: {todo.title} (P{todo.priority}{due_part})")

            elif action.name == "update_todo":
                todo = store.get_todo(args["file"])
                if "priority" in args:
                    todo.priority = args["priority"]
                if "due" in args:
                    todo.due = date.fromisoformat(args["due"]) if args["due"] else None
                if "goal" in args:
                    todo.goal = args["goal"]
                if "verify" in args:
                    todo.verify = args["verify"]
                if "notes" in args:
                    todo.body = args["notes"]
                if "status" in args:
                    todo.status = args["status"]
                    if todo.status == "done":
                        todo.done_at = now
                        todo.confirmed = todo.verify == "none"
                store.save(todo)
                if args.get("status") == "done":
                    summary.append(f"Marked done: {todo.title}")
                elif args.get("status") == "dropped":
                    summary.append(f"Dropped todo: {todo.title}")
                else:
                    summary.append(f"Updated todo: {todo.title}")

            elif action.name == "move_todo":
                todo = store.get_todo(args["file"])
                new_path = store.move_todo(args["file"], args["to"])
                renamed[action.arguments["file"]] = new_path
                renamed[args["file"]] = new_path
                summary.append(f"Moved to {args['to']}: {todo.title}")

            elif action.name == "add_event":
                event = Event(
                    path="",
                    title=args["title"],
                    start=datetime.fromisoformat(args["start"]),
                    end=datetime.fromisoformat(args["end"]) if args.get("end") else None,
                    location=args.get("location"),
                    travel_minutes=args.get("travel_minutes", 0),
                    prep_minutes=args.get("prep_minutes"),
                    importance=args.get("importance", "normal"),
                )
                store.add(event)
                profile = store.profile()
                leave_by = event.times(profile).leave_by
                summary.append(
                    f"Added event: {event.title} {_fmt_event_start(event.start)}, "
                    f"leave by {_fmt_clock(leave_by)}"
                )
                changed_schedule = True

            elif action.name == "update_event":
                event = store.get_event(args["file"])
                if "title" in args:
                    event.title = args["title"]
                if "start" in args:
                    event.start = datetime.fromisoformat(args["start"])
                if "end" in args:
                    event.end = datetime.fromisoformat(args["end"]) if args["end"] else None
                if "location" in args:
                    event.location = args["location"]
                if "travel_minutes" in args:
                    event.travel_minutes = args["travel_minutes"]
                if "prep_minutes" in args:
                    event.prep_minutes = args["prep_minutes"]
                if "importance" in args:
                    event.importance = args["importance"]
                if "status" in args:
                    event.status = args["status"]
                store.save(event)
                summary.append(f"Updated event: {event.title}")
                changed_schedule = True

            elif action.name == "delete_event":
                event = store.get_event(args["file"])
                store.delete(args["file"])
                summary.append(f"Deleted event: {event.title}")
                changed_schedule = True

            elif action.name == "add_goal":
                goal = Goal(path="", title=args["title"], period=args["period"], body=args.get("notes", ""))
                store.add(goal)
                summary.append(f"Added goal: {goal.title}")

            elif action.name == "update_goal":
                goal = store.get_goal(args["file"])
                if "status" in args:
                    goal.status = args["status"]
                if "notes" in args:
                    goal.body = args["notes"]
                store.save(goal)
                summary.append(f"Updated goal: {goal.title}")

            elif action.name == "set_profile":
                profile = store.profile()
                field_name = args["field"]
                current = getattr(profile, field_name)
                coerced = _coerce_profile_value(current, args["value"])
                setattr(profile, field_name, coerced)
                store.save_profile(profile)
                summary.append(f"Set {field_name} = {coerced}")
                changed_schedule = True

            elif action.name == "snooze":
                snooze_minutes = args["minutes"]

            elif action.name == "reply":
                pass

        except (KeyError, ValueError, TypeError) as exc:
            summary.append(f"Couldn't apply {action.name}: {exc}")
            continue

        if first_success is None and len(summary) > before:
            first_success = summary[before]

    if first_success is not None:
        store.commit(f"capture: {first_success}")

    return Applied(summary=summary, snooze_minutes=snooze_minutes, changed_schedule=changed_schedule)
