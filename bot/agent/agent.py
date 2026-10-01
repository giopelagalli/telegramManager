from __future__ import annotations

import json
import re
from base64 import b64encode
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable

import logging
import time

from bot.agent.client import ModelClient, ToolCall
from bot.agent.prompts import (
    BACKUP_NOTE,
    PROJECTS_NOTE,
    CHAT_OCR_PROMPT,
    CONSOLIDATE_SYSTEM,
    CLASSIFY_PHOTO_SYSTEM,
    LOOK_SYSTEM,
    COACH_SYSTEM,
    VOICE,
    CARDS_SYSTEM,
    DIGEST_SYSTEM,
    GRADE_SYSTEM,
    PLAN_SYSTEM,
    DESCRIBE_SOURCE_ANY_SYSTEM,
    ANSWER_SYSTEM,
    CAPTURE_SYSTEM,
    COMPOSE_SYSTEM,
    DESCRIBE_SOURCE_SYSTEM,
    OCR_PROMPT,
    TUTOR_SYSTEM,
    build_context,
)
from bot.agent.tools import PROJECT_TOOLS, TOOL_SCHEMAS, TUTOR_TOOLS, validate_call
from bot.knowledge.models import slugify, SOURCE_KINDS, Course, Event, Goal, Source, Todo
from bot.knowledge.trackers import status_line

logger = logging.getLogger(__name__)
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


_RAW_CALL_RE = re.compile(r"^\w+\(\{.*\}\)$", re.DOTALL)


def _fmt_amount(n: float) -> str:
    return str(int(n)) if float(n).is_integer() else f"{n:g}"


def food_totals(items: list[dict], profile) -> str:
    """'1950 / 3000 kcal, 85 / 180 g protein, 120 g carbs, 70 g fat'."""
    kcal = sum(f["kcal"] for f in items)
    prot = sum(f.get("protein_g") or 0 for f in items)
    carbs = sum(f.get("carbs_g") or 0 for f in items)
    fat = sum(f.get("fat_g") or 0 for f in items)
    parts = [f"{kcal}" + (f" / {profile.calorie_target}" if profile.calorie_target else "") + " kcal"]
    if prot or profile.protein_target:
        parts.append(f"{prot}" + (f" / {profile.protein_target}" if profile.protein_target else "") + " g protein")
    if carbs:
        parts.append(f"{carbs} g carbs")
    if fat:
        parts.append(f"{fat} g fat")
    sodium = sum(f.get("sodium_mg") or 0 for f in items)
    potassium = sum(f.get("potassium_mg") or 0 for f in items)
    if sodium:
        parts.append(f"{sodium} mg sodium")
    if potassium:
        parts.append(f"{potassium} mg potassium")
    fiber = sum(f.get("fiber_g") or 0 for f in items)
    sugar = sum(f.get("sugar_g") or 0 for f in items)
    if fiber:
        parts.append(f"{fiber} g fiber")
    if sugar:
        parts.append(f"{sugar} g sugar")
    return ", ".join(parts)


def _dedupe(calls: list[ToolCall]) -> list[ToolCall]:
    seen: set[str] = set()
    out: list[ToolCall] = []
    for c in calls:
        key = c.name + json.dumps(c.arguments, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


class Agent:
    def __init__(
        self,
        client: ModelClient,
        vision: ModelClient | None,
        store: KnowledgeStore,
        clock: Callable[[], datetime],
        hard: ModelClient | None = None,
        search: bool = False,
        projects: bool = False,
    ):
        self.tools = [t for t in TOOL_SCHEMAS
                      if (search or t["function"]["name"] != "search")
                      and (projects or t["function"]["name"] not in PROJECT_TOOLS)]
        self.projects = projects
        self.client = client
        self.vision = vision
        self.store = store
        self.hard_remote = False  # /hard goes to another provider than the primary: give it the minimal view
        self.cloud: ModelClient | None = None  # the explicit opt-in for /hard: the bigger cloud model, blind view
        self.clock = clock
        self.hard = hard

    def _heavy(self):
        """The model for coaching, plans and tutoring: the Spark thinking harder, or the cloud while it's down."""
        if self.degraded and self.cloud is not None:
            return self.cloud
        return self.hard or None

    @property
    def degraded(self) -> bool:
        """True while the primary model is unreachable and the backup is answering."""
        return bool(getattr(self.client, "breaker_open", False))

    def context(self, now: datetime, awaiting: str | None = None, *, remote: bool = False) -> str:
        """The context for this moment: the full view on the primary, the minimal one on a backup."""
        return build_context(self.store, now, awaiting, minimal=self.degraded or remote)

    async def capture(
        self, text: str, awaiting: str | None = None, recent: list | None = None, recalled: list[str] | None = None,
        client: ModelClient | None = None, web: str | None = None,
    ) -> CaptureResult:
        """The main loop. `client` runs it on another model (/hard → the cloud) with the remote
        view: schedule, todos, goals, the conversation and the memories that match — not the vault."""
        now = self.clock()
        profile = self.store.profile()
        system = CAPTURE_SYSTEM.format(assistant=profile.assistant_name, name=profile.name, now=now.isoformat(), voice=VOICE)
        if self.projects:
            system += "\n\n" + PROJECTS_NOTE
        if self.degraded:
            system += "\n\n" + BACKUP_NOTE  # same view as /hard: conversation + matching notes, not the vault
        context = self.context(now, awaiting, remote=client is not None and client is not self.client)
        if recalled:
            context += "\nFrom memory, possibly relevant:\n" + "\n".join(f"- {r}" for r in recalled)
        tools = self.tools
        if web:
            # Second pass after a search: the results are in hand, now act on them and answer.
            context += ("\n\nLive web search results fetched just now (you DO have current information; never say you "
                        "lack internet access or a knowledge cutoff; numbers found here beat estimates; cite the URL "
                        "you used in `reply`):\n" + web)
            tools = [t for t in self.tools if t["function"]["name"] != "search"]
        messages = [
            {"role": "system", "content": system},
            {"role": "system", "content": context},
            *[{"role": role, "content": body} for role, body in (recent or [])],
            {"role": "user", "content": text},
        ]

        response = await self._chat_or_none(messages, tools, 0.1, client=client)
        if response is None:
            return self._to_inbox(text, "Model's down. Saved it, say it again in a bit.")

        self._adopt_text_as_reply(response)
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
            response = await self._chat_or_none(retry_messages, tools, 0.1, client=client)
            if response is None:
                return self._to_inbox(text, "Model's down. Saved it, say it again in a bit.")
            self._adopt_text_as_reply(response)
            errors = self._check(response.tool_calls)

        if errors == ["no tool calls and no text"]:
            # The model went silent (Qwen does this now and then with tools attached). Ask again
            # with no tools: it then just answers in prose.
            plain = await self._chat_or_none(messages, None, 0.3, client=client)
            if plain is not None and (plain.text or "").strip():
                return CaptureResult([ToolCall("reply", {"text": plain.text.strip()})], plain.text.strip(), parsed=True)

        if errors:
            logger.warning("capture unparsed after retry: %s | calls: %s", errors, self._raw_calls_repr(response.tool_calls))
            return self._to_inbox(text, "Didn't catch that. Say it again, plainer.")

        calls = _dedupe(response.tool_calls)  # the model sometimes emits the same call twice
        reply_text = self._reply_text(calls)
        response.tool_calls[:] = calls
        return CaptureResult(response.tool_calls, reply_text, parsed=True)

    async def _chat_or_none(self, messages, tools, temperature, client: ModelClient | None = None):
        started = time.monotonic()
        try:
            response = await (client or self.client).chat(messages, tools=tools, temperature=temperature)
            logger.info("model call %.1fs (%d msgs, tools=%s)", time.monotonic() - started, len(messages), tools is not None)
            return response
        except Exception as exc:
            logger.error("model call failed: %s: %s", type(exc).__name__, str(exc)[:500])
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
        if reply_count > 1:
            errors.append(f"expected at most one reply call, got {reply_count}")
        if not tool_calls:
            errors.append("no tool calls and no text")
        return errors

    @staticmethod
    def _adopt_text_as_reply(response) -> None:
        """A model that answers in prose instead of calling `reply` still answered: keep it."""
        text = (response.text or "").strip()
        if not text or any(c.name == "reply" for c in response.tool_calls):
            return
        if _RAW_CALL_RE.match(text):
            return  # the model echoed our "name({...})" retry note; that is not an answer
        response.tool_calls.append(ToolCall("reply", {"text": text}))

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
                    {"role": "system", "content": COMPOSE_SYSTEM.format(assistant=self.store.profile().assistant_name, voice=VOICE)},
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

    async def describe_source(
        self,
        text_head: str,
        filename: str,
        course_title: str | None = None,
        courses: list[str] | None = None,
    ) -> dict:
        """{title, kind, topics, summary[, course]} for an ingested file; falls back to the filename.

        With `course_title` the course is known. Without it the model also picks `course`
        from `courses` (existing titles) or proposes a new short name."""
        if course_title is not None:
            system = DESCRIBE_SOURCE_SYSTEM.format(course=course_title, kinds=", ".join(SOURCE_KINDS))
        else:
            system = DESCRIBE_SOURCE_ANY_SYSTEM.format(
                courses=", ".join(courses or []) or "none yet", kinds=", ".join(SOURCE_KINDS)
            )
        messages = [
            {"role": "system", "content": system},
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

    async def plan_exam(self, exam, sources: list, days_left: int, minutes: int, lookup: str | None = None) -> dict | None:
        """Day-by-day plan from the hard model when there is one; None when unparseable/offline.

        `lookup` is web-search text about the subject, used when nothing is stored yet."""
        listing = "\n".join(
            f"- {s.title} ({s.kind}{', ' + str(s.pages) + ' pages' if s.pages else ''}): {s.summary[:200]}"
            for s in sources
        )
        if not listing:
            listing = "- nothing stored yet" + (f"\n\nWhat this subject usually covers (web search):\n{lookup}" if lookup else "")
        user = (
            f"Exam: {exam.title} on {exam.start:%A %b %d}. Topics: {', '.join(exam.topics) or 'unspecified'}.\n"
            f"Days left: {days_left}. Daily budget: {minutes} minutes. Today: {self.clock():%Y-%m-%d}.\n"
            f"Sources:\n{listing}"
        )
        messages = [{"role": "system", "content": PLAN_SYSTEM}, {"role": "user", "content": user}]
        response = await self._chat_or_none(messages, None, 0.3, client=self._heavy())
        if response is None:
            return None
        match = re.search(r"\{.*\}", response.text or "", re.DOTALL)
        if match is None:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        days = data.get("days")
        if not isinstance(days, list):
            return None
        clean = []
        for d in days:
            try:
                clean.append({"date": date.fromisoformat(str(d["date"])), "minutes": int(d.get("minutes", minutes)),
                              "task": str(d["task"]).strip()})
            except (KeyError, ValueError, TypeError):
                continue
        return {"days": clean, "advice": str(data.get("advice", "")).strip()}

    async def make_cards(self, topic: str, sources: list, n: int) -> list[tuple[str, str]]:
        """Question/answer pairs from the sources; empty on failure."""
        body = "\n\n".join(f"### {s.title}\n{s.body[:20000]}" for s in sources)
        messages = [
            {"role": "system", "content": CARDS_SYSTEM.format(n=n)},
            {"role": "user", "content": f"Topic: {topic}\n\n{body or '(no sources)'}"},
        ]
        response = await self._chat_or_none(messages, None, 0.4, client=self._heavy())
        data = _json_block(response.text if response else "")
        cards = data.get("cards") if isinstance(data, dict) else None
        out = []
        for c in cards or []:
            try:
                q, a = str(c["q"]).strip(), str(c["a"]).strip()
            except (KeyError, TypeError):
                continue
            if q and a:
                out.append((q, a))
        return out[:n]

    async def grade(self, question: str, reference: str, answer: str) -> tuple[int, str]:
        """(0-5, feedback). Unreachable model grades 3 with a neutral note, never punishes."""
        messages = [
            {"role": "system", "content": GRADE_SYSTEM},
            {"role": "user", "content": f"Question: {question}\nReference: {reference}\nStudent: {answer}"},
        ]
        response = await self._chat_or_none(messages, None, 0.1)
        data = _json_block(response.text if response else "")
        try:
            return max(0, min(5, int(data["grade"]))), str(data.get("feedback", "")).strip()
        except (KeyError, TypeError, ValueError):
            return 3, "Couldn't grade that properly; counting it as okay."

    async def digest(self, items: list[tuple[str, str, str]]) -> str | None:
        """items = (course title, source title, summary). Markdown text or None."""
        listing = "\n\n".join(f"[{c}] {t}\n{s}" for c, t, s in items)
        messages = [{"role": "system", "content": DIGEST_SYSTEM}, {"role": "user", "content": listing}]
        response = await self._chat_or_none(messages, None, 0.6)
        return (response.text or "").strip() or None if response else None

    async def coach(self, thread: str, ask: str, recent: list | None = None) -> str | None:
        """What to text next. Reasoning model when there is one; None when unreachable."""
        profile = self.store.profile()
        messages = [
            {"role": "system", "content": COACH_SYSTEM.format(assistant=profile.assistant_name, voice=VOICE)},
            *[{"role": r, "content": b} for r, b in (recent or [])],
            {"role": "user", "content": f"Thread:\n{thread}\n\nAsk: {ask or 'what do I send?'}"},
        ]
        response = await self._chat_or_none(messages, None, 0.5, client=self._heavy())
        return (response.text or "").strip() or None if response else None

    async def consolidate(self, thread: list, known: list[str]) -> list[dict]:
        """Nightly notes: what today's conversation is worth remembering. Never on the backup model."""
        if self.degraded or not thread:
            return []
        profile = self.store.profile()
        system = CONSOLIDATE_SYSTEM.format(assistant=profile.assistant_name, name=profile.name)
        known_text = "\n".join(f"- {k}" for k in known[-80:]) or "- nothing yet"
        convo = "\n".join(f"{role}: {body}" for role, body in thread)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Already known:\n{known_text}\n\nToday:\n{convo}"},
        ]
        response = await self._chat_or_none(messages, None, 0.2)
        data = _json_block(response.text or "") if response else None
        items = data.get("memories") if isinstance(data, dict) else None
        out: list[dict] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            kind = "state" if str(item.get("kind", "fact")).lower() == "state" else "fact"
            if text and text.lower() not in {k.lower() for k in known}:
                out.append({"kind": kind, "text": text})
        return out[:8]

    async def look(self, image: bytes) -> tuple[str, str]:
        """What a photo is: ('chat'|'material'|'photo', one-line description). 'photo' when unsure."""
        if self.vision is None:
            return "photo", ""
        data_url = f"data:image/jpeg;base64,{b64encode(image).decode()}"
        messages = [
            {"role": "system", "content": LOOK_SYSTEM},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": data_url}}]},
        ]
        response = await self._chat_or_none(messages, None, 0.0, client=self.vision)
        data = _json_block(response.text or "") if response else None
        if not isinstance(data, dict):
            return "photo", ""
        kind = str(data.get("kind", "photo")).strip().lower()
        if kind not in ("chat", "material", "photo"):
            kind = "photo"
        return kind, str(data.get("description", "")).strip()

    async def classify_photo(self, text: str) -> str:
        """'chat' or 'material' for OCR'd text; material when unsure."""
        messages = [{"role": "system", "content": CLASSIFY_PHOTO_SYSTEM}, {"role": "user", "content": text[:3000]}]
        response = await self._chat_or_none(messages, None, 0.0)
        word = (response.text or "").strip().lower() if response else ""
        return "chat" if word.startswith("chat") else "material"

    async def ocr(self, image: bytes, chat: bool = False) -> str | None:
        """Text in an image. `chat` keeps sides and timestamps, for screenshots of conversations."""
        if self.vision is None:
            return None
        data_url = f"data:image/jpeg;base64,{b64encode(image).decode()}"
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": CHAT_OCR_PROMPT if chat else OCR_PROMPT},
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
                "content": TUTOR_SYSTEM.format(assistant=profile.assistant_name, name=profile.name, course=course.title),
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
    described = {
        "title": str(data.get("title", "")).strip(),
        "kind": data["kind"],
        "topics": [t.strip() for t in topics if t.strip()][:MAX_TOPICS],
        "summary": str(data.get("summary", "")).strip(),
    }
    course = str(data.get("course", "")).strip()
    if course:
        described["course"] = course
    return described


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
                    due_time=args.get("due_time") or None,
                    goal=args.get("goal"),
                    verify=args.get("verify", "none"),
                    body=args.get("notes", ""),
                    course=args.get("course") or None,
                )
                store.add(todo, folder="backlog" if args.get("backlog") else None)
                due_part = f", due {_fmt_due(todo.due)}" if todo.due else ""
                summary.append(f"Added todo: {todo.title} (P{todo.priority}{due_part})")

            elif action.name == "update_todo":
                todo = store.get_todo(args["file"])
                if "title" in args and str(args["title"]).strip():
                    todo.title = str(args["title"]).strip()
                if "priority" in args:
                    todo.priority = args["priority"]
                if "due_time" in args:
                    todo.due_time = args["due_time"] or None
                if "due" in args:
                    todo.due = date.fromisoformat(args["due"]) if args["due"] else None
                if "goal" in args:
                    todo.goal = args["goal"]
                if "verify" in args:
                    todo.verify = args["verify"]
                if "course" in args:
                    todo.course = args["course"] or None
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
                    travel_mode=args.get("travel_mode") or "",
                    prep_minutes=args.get("prep_minutes"),
                    importance=args.get("importance", "normal"),
                    repeat_days=list(args.get("repeat_days") or []),
                    repeat_until=date.fromisoformat(args["repeat_until"]) if args.get("repeat_until") else None,
                    kind=args.get("kind"),
                    course=args.get("course"),
                    topics=list(args.get("topics") or []),
                )
                profile = store.profile()
                leave_by = event.times(profile).leave_by
                if event.repeat_days:
                    store.add_series(event)
                    made = store.materialize(now)
                    days = "/".join(d.title() for d in event.repeat_days)
                    until = f" until {_fmt_due(event.repeat_until)}" if event.repeat_until else ""
                    summary.append(
                        f"Added weekly: {event.title} {days} {_fmt_clock(event.start)}{until}, "
                        f"leave by {_fmt_clock(leave_by)} ({made} on the calendar)"
                    )
                else:
                    store.add(event)
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
                if "repeat_until" in args:
                    event.repeat_until = date.fromisoformat(args["repeat_until"]) if args["repeat_until"] else None
                if "location" in args:
                    event.location = args["location"]
                if "travel_minutes" in args:
                    event.travel_minutes = args["travel_minutes"]
                if "travel_mode" in args:
                    event.travel_mode = args["travel_mode"] or ""
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
                if args["file"].startswith("schedule/series/"):
                    n = store.delete_series(args["file"], now)
                    summary.append(f"Removed weekly: {event.title} ({n} upcoming cleared)")
                else:
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
                summary.append(f"{field_name.replace('_', ' ').capitalize()}: {coerced}.")
                changed_schedule = True

            elif action.name == "save_place":
                profile = store.profile()
                name = _place_key(args["name"])
                profile.places[name] = str(args["address"]).strip()
                if not profile.base:  # the first place you tell it about is home until you say otherwise
                    profile.base = name
                    profile.home_address = profile.places[name]
                    profile.home_latlng = None
                store.save_profile(profile)
                summary.append(f"Saved place: {name} = {profile.places[name]}")
                changed_schedule = True
            elif action.name == "set_base":
                profile = store.profile()
                name = _match_place(profile, str(args["name"]))
                if name is None:
                    raise KeyError(f"no saved place like {args['name']}; tell me its address first")
                profile.base = name
                profile.home_address = profile.places[name]
                ll = profile.places_latlng.get(name)
                profile.home_latlng = (ll[0], ll[1]) if ll else None
                store.save_profile(profile)
                summary.append(f"Home is now: {name}")
                changed_schedule = True
            elif action.name == "track":
                t = store.add_tracking(str(args["name"]), float(args["amount"]), args.get("unit"), args.get("note"))
                summary.append(f"{status_line(store, now.date(), t).capitalize()}.")
            elif action.name == "track_setup":
                if args.get("off"):
                    t = store.upsert_tracker(str(args["name"]), active=False, remind_at=[], remind_every_minutes=0)
                    summary.append(f"Stopped tracking {t.label}; reminders off.")
                else:
                    t = store.upsert_tracker(
                        str(args["name"]), unit=args.get("unit"),
                        target=float(args["target"]) if args.get("target") is not None else None,
                        remind_at=list(args["remind_at"]) if args.get("remind_at") is not None else None,
                        remind_every_minutes=int(args["remind_every_minutes"]) if args.get("remind_every_minutes") is not None else None,
                        active=True,
                    )
                    bits = [f"Tracking {t.label}" + (f" in {t.unit}" if t.unit else "")]
                    if t.target:
                        bits.append(f"target {_fmt_amount(t.target)} {t.unit}".rstrip())
                    if t.remind_at:
                        bits.append("reminders at " + ", ".join(t.remind_at))
                    if t.remind_every_minutes:
                        bits.append(f"a reminder every {t.remind_every_minutes} min")
                    summary.append(", ".join(bits) + ".")
            elif action.name == "log_food":
                store.add_food(str(args["item"]), int(args["kcal"]), args.get("protein_g"), bool(args.get("estimate")),
                               carbs_g=args.get("carbs_g"), fat_g=args.get("fat_g"),
                               sodium_mg=args.get("sodium_mg"), potassium_mg=args.get("potassium_mg"),
                               fiber_g=args.get("fiber_g"), sugar_g=args.get("sugar_g"))
                today = store.food(now.date())
                profile = store.profile()
                approx = "~" if args.get("estimate") else ""
                macros = [f"{int(args[k])} {unit} {label}" for k, unit, label in
                          (("protein_g", "g", "protein"), ("carbs_g", "g", "carbs"), ("fat_g", "g", "fat"),
                           ("sodium_mg", "mg", "sodium"), ("potassium_mg", "mg", "potassium"),
                           ("fiber_g", "g", "fiber"), ("sugar_g", "g", "sugar")) if args.get(k) is not None]
                line = f"Logged: {str(args['item']).strip()} {approx}{int(args['kcal'])} kcal" + (", " + ", ".join(macros) if macros else "")
                summary.append(line + ". Today: " + food_totals(today, profile) + ".")
            elif action.name == "remember":
                kind = args.get("kind", "fact")
                text = str(args["fact"]).strip()
                if any(m.text.strip().lower() == text.lower() for m in store.memories()):
                    summary.append("Already have that.")
                else:
                    store.add_memory(text, kind=kind)
                    summary.append("I'll keep that in mind." if kind == "state" else f"Remembered: {text}")
            elif action.name in ("study", "directions", "search", "undo", "recall", "coach") or action.name in PROJECT_TOOLS:
                pass  # the router answers it after applying the rest
            elif action.name == "move_source":
                target = _resolve_course(store, str(args.get("course", "")))
                if target is None:
                    raise KeyError(f"no course named {args.get('course')}")
                latest = _latest_source(store)
                if latest is None:
                    raise KeyError("nothing stored yet")
                store.move_source(latest.path, target.slug)
                summary.append(f"Moved {latest.title} to {target.title}")
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


def _resolve_course(store, name: str):
    """A course by slug or by title, case-insensitive; None when unknown."""
    wanted = name.strip().lower()
    if not wanted:
        return None
    for course in store.courses():
        if wanted in (course.slug, course.title.lower(), slugify(name)):
            return course
    return None


def _latest_source(store):
    sources = store.sources()
    if not sources:
        return None
    return max(sources, key=lambda s: (s.timestamp.timestamp() if s.timestamp else 0.0, s.path))


def _place_key(name: str) -> str:
    return " ".join(name.lower().replace("'", "").split())


def _match_place(profile, name: str) -> str | None:
    """Saved place whose name contains (or is contained by) what the user said."""
    wanted = _place_key(name)
    for key in profile.places:
        if key == wanted or wanted in key or key in wanted:
            return key
    return None


def _json_block(text: str):
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if match is None:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
