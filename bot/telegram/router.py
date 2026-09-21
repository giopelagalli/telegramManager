from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from bot.agent.agent import apply_actions
from bot.agent.client import ToolCall
from bot.agent.prompts import build_context
from bot.knowledge.models import Todo, UNBOUND, Channel, Course, Source, channel_key, slugify
from bot.knowledge.views import esc
from bot.maps.client import directions_url, distance_m
from bot.scheduler.chains import close_chain
from bot.scheduler.briefings import evening_outbound, morning_outbound
from bot.memory.thread import remember, thread
from bot.scheduler.critical import leave_on_location, leave_on_text
from bot.scheduler import review
from bot.scheduler.outbound import Outbound
from bot.study.extract import (
    PAGE_MARKER,
    PART_MARKER,
    SLIDE_MARKER,
    extract_docx,
    extract_pdf,
    extract_pptx,
    extract_text,
    guess_kind,
    is_plain_text,
    render_pages,
)
from bot.study.select import select_sources
from bot.telegram import callbacks, commands
from bot.telegram.markdown import md_to_html
from bot.telegram.sender import plain_text

logger = logging.getLogger(__name__)

VERIFY_RADIUS_M = 200
NOTE_TITLE_CHARS = 60
MAX_COURSE_TOPICS = 50
NOTE_SUMMARY_CHARS = 300
UNREADABLE_REPLY = "Stored the file but couldn't read it."
TUTOR_OFFLINE_REPLY = "Can't reach the model for that right now. Try again in a bit."
COURSE_GONE_REPLY = "This topic's course file is gone; /bind again."
TOO_LARGE_REPLY = (
    "That file is over Telegram's 20 MB bot limit. Split it or send a smaller export."
)
UNBOUND_REPLY = (
    "This topic isn't bound yet. Run /bind course <CODE> <title>, /bind assignments, "
    "/bind exams, or /bind review here."
)

# Topic-name auto-binding: what a topic's own name maps to, once cleaned up.
_ASSIGNMENT_TOPIC_NAMES = {"assignments", "assignment", "homework", "hw"}
_EXAM_TOPIC_NAMES = {"exams", "exam", "quizzes", "quiz", "tests", "quizzes and exams", "exams and quizzes"}
_REVIEW_TOPIC_NAMES = {"review", "reviews", "digest", "recall"}
_LIFE_TOPIC_NAMES = {"life", "general", "main"}
LIFE_TOPIC_HINT = "Use your DM for life stuff; name this topic after a course to use it here."


def _normalize_topic_name(name: str) -> str:
    """Case-insensitive, with emoji/punctuation and extra whitespace stripped."""
    cleaned = re.sub(r"[^\w\s]", "", name, flags=re.UNICODE)
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def _topic_kind(normalized: str) -> str | None:
    if normalized in _ASSIGNMENT_TOPIC_NAMES:
        return "assignments"
    if normalized in _EXAM_TOPIC_NAMES:
        return "exams"
    if normalized in _REVIEW_TOPIC_NAMES:
        return "review"
    return None


def _is_course(channel: Channel | None) -> bool:
    return channel is not None and channel.kind == "course"


def _extract(data: bytes, kind: str, filename: str) -> list[tuple[int, str]] | None:
    try:
        if kind == "slides":
            return extract_pptx(data)
        if kind == "chapter":
            return extract_pdf(data)
        if kind == "notes":
            return extract_docx(data)
    except Exception as exc:
        logger.warning("could not extract %s: %s", filename, exc)
    return None


def _stored_reply(source: Source, course: Course) -> str:
    parts = [source.kind]
    if source.pages:
        parts.append(f"{source.pages} pages")
    if source.topics:
        parts.append("topics: " + ", ".join(source.topics))
    note = " OCR unavailable." if source.ocr == "unavailable" else ""
    return (
        f"Stored: {source.title} ({', '.join(parts)}) under {course.title}.{note} "
        f"Wrong course? Say \"move that to <course>\"."
    )


SPARK_DOWN_WAIT = "Spark's down. That one waits till it's back — the backup only gets your schedule."

class Router:
    """Turns a user action into a list of outbound messages. No telegram types here."""

    def __init__(self, store, agent, state, clock, maps, search=None, index=None, cluster=None):
        self.store = store
        self.agent = agent
        self.state = state
        self.clock = clock
        self.maps = maps
        self.search = search
        self.index = index
        self.cluster = cluster
        self.last_outcome = "handled"
        self._warned_threads: set[str] = set()

    # -- entry points ----------------------------------------------------

    async def on_text(
        self, text: str, via_voice: bool = False, *, channel: Channel | None = None
    ) -> list[Outbound]:
        ignored = self._ignore_unbound(channel)
        if ignored is not None:
            return ignored
        if _is_course(channel):
            return self._tag(await self._tutor(text, channel, via_voice), channel)
        return self._tag(await self._text(text, via_voice), channel)

    async def _text(self, text: str, via_voice: bool) -> list[Outbound]:
        now = self._touch()
        awaiting = self.state.chain.item if self.state.chain is not None else None
        close_chain(self.state)

        if self.state.critical is not None:
            out = leave_on_text(self.state, self.store)
            return [out] if out is not None else []

        pending = self.state.pending_verify
        if pending is not None and pending.kind == "question":
            return [await self._verify_answer(pending, text)]

        if text.strip().lower() in ("now", "today"):  # the persistent keyboard keys
            return await commands.handle(text.strip().lower(), "", self.store, self.agent, self.state, now, None)

        return await self._capture(text, awaiting, via_voice, now)

    async def on_voice_unavailable(self, *, channel: Channel | None = None) -> list[Outbound]:
        ignored = self._ignore_unbound(channel)
        if ignored is not None:
            return ignored
        out = Outbound("Voice input isn't set up here. Send it as text.", kind="reply")
        return self._tag([out], channel)

    async def on_voice_failed(self, reason: str, *, channel: Channel | None = None) -> list[Outbound]:
        ignored = self._ignore_unbound(channel)
        if ignored is not None:
            return ignored
        self._touch()
        self.store.add_inbox(f"[voice note could not be transcribed: {reason}]")
        self.store.commit("inbox: voice note")
        out = Outbound("Couldn't transcribe that. Saved a note in your inbox.", kind="reply")
        return self._tag([out], channel)

    async def on_location(
        self, lat: float, lng: float, *, channel: Channel | None = None
    ) -> list[Outbound]:
        ignored = self._ignore_unbound(channel)
        if ignored is not None:
            return ignored
        return self._tag(await self._location(lat, lng), channel)

    async def _location(self, lat: float, lng: float) -> list[Outbound]:
        now = self._touch()
        close_chain(self.state)

        if self.state.critical is not None:
            out = leave_on_location(now, self.state, self.store, lat, lng)
            return [out] if out is not None else []

        pending = self.state.pending_verify
        if pending is not None and pending.kind == "location":
            return [await self._verify_location(pending, lat, lng)]

        out = leave_on_location(now, self.state, self.store, lat, lng)
        self.state.last_location = {"lat": lat, "lng": lng, "at": now.isoformat()}
        moved = await self._follow_timezone((lat, lng), now)
        if out is not None:
            return [out]
        text = "Got it. I'll go off this for \"how far\" until you move."
        if moved:
            text += f" You're on {moved} time now, clock switched."
        return [Outbound(text, kind="reply")]

    async def _follow_timezone(self, latlng, now) -> str | None:
        """The phone's timezone is wherever the phone is: a shared location moves JD's clock too."""
        if self.maps is None:
            return None
        zone = await self.maps.timezone(latlng, now)
        profile = self.store.profile()
        if not zone or zone == profile.timezone:
            return None
        try:
            tz = ZoneInfo(zone)
        except (KeyError, ValueError):
            return None
        profile.timezone = zone
        self.store.save_profile(profile)
        self.store.commit(f"profile: timezone {zone}")
        if hasattr(self.clock, "tz"):
            self.clock.tz = tz
        return zone.rsplit("/", 1)[-1].replace("_", " ")

    def on_topic_named(self, chat_id: int, thread_id: int, name: str) -> list[Outbound]:
        """A forum topic was created or renamed: bind it from its own name."""
        target = (chat_id, thread_id)
        normalized = _normalize_topic_name(name)
        if normalized in _LIFE_TOPIC_NAMES:
            return [Outbound(LIFE_TOPIC_HINT, kind="reply", target=target)]

        existing = self.store.channels().by_key(channel_key(chat_id, thread_id))
        renamed = existing is not None
        kind = _topic_kind(normalized)

        if kind is not None:
            if existing is not None and existing.kind == kind:
                return []
            bound = Channel(chat_id, thread_id, kind)
            self._save_binding(bound, f"bind: {name}")
            if renamed:
                return [Outbound(f"Renamed: this topic is now {kind.capitalize()}.", kind="reply", target=target)]
            return [Outbound(f"Got it — this is your {kind} topic.", kind="reply", target=target)]

        slug = slugify(name)
        title = name
        if existing is not None and existing.kind == "course" and existing.course == slug:
            return []
        if renamed and existing.kind == "course":
            try:
                old_course = self.store.get_course(existing.course)
            except KeyError:
                old_course = None
            if old_course is not None and not self.store.sources(existing.course):
                old_course.title = title
                self.store.save_course(old_course)
                self.store.commit(f"course: rename {existing.course} -> {title}")
                slug = existing.course

        try:
            self.store.get_course(slug)
        except KeyError:
            self.store.add_course(Course(path=f"courses/{slug}.md", title=title))
            self.store.commit(f"course: {title}")

        bound = Channel(chat_id, thread_id, "course", slug)
        self._save_binding(bound, f"bind: {name}")
        if renamed:
            return [Outbound(f"Renamed: this topic is now {title}.", kind="reply", target=target)]
        return [Outbound(f"Got it — this topic is {title}.", kind="reply", target=target)]

    async def on_photo(
        self, image: bytes, caption: str | None = None, *, channel: Channel | None = None
    ) -> list[Outbound]:
        ignored = self._ignore_unbound(channel)
        if ignored is not None:
            return ignored
        if _is_course(channel):
            return self._tag(await self._ingest_photo(image, caption, channel), channel)
        return self._tag(await self._photo(image, caption), channel)

    async def on_document(
        self,
        data: bytes,
        filename: str,
        mime: str,
        caption: str | None = None,
        *,
        channel: Channel | None = None,
    ) -> list[Outbound]:
        ignored = self._ignore_unbound(channel)
        if ignored is not None:
            return ignored
        return self._tag(await self._ingest_document(data, filename, mime, caption, channel), channel)

    async def on_file_too_large(self, *, channel: Channel | None = None) -> list[Outbound]:
        """The handler saw the size before downloading; nothing was fetched."""
        ignored = self._ignore_unbound(channel)
        if ignored is not None:
            return ignored
        self._touch()
        return self._tag([Outbound(esc(TOO_LARGE_REPLY), kind="reply")], channel)

    async def _photo(self, image: bytes, caption: str | None = None) -> list[Outbound]:
        now = self._touch()
        close_chain(self.state)

        pending = self.state.pending_verify
        if pending is not None and pending.kind == "photo":
            return [await self._verify_photo(pending, image)]

        if self.agent.degraded:
            return [Outbound(SPARK_DOWN_WAIT, kind="reply")]
        kind, description = await self.agent.look(image)
        if kind != "photo":
            return await self._ingest_photo(image, caption, None, kind=kind)
        # Just a photo: talk about it like a person would, and keep what it showed in memory.
        seen = f"[sent a photo: {description}]" if description else "[sent a photo]"
        text = f"{seen} {caption}".strip() if caption else seen
        awaiting = self.state.chain.item if self.state.chain is not None else None
        return await self._capture(text, awaiting, False, now)

    # -- study ingest and tutoring ---------------------------------------

    async def _ingest_document(
        self, data: bytes, filename: str, mime: str, caption: str | None, channel: Channel
    ) -> list[Outbound]:
        self._touch()
        kind = guess_kind(filename, mime)
        if kind == "photo":
            return await self._ingest_photo(data, caption, channel)

        if is_plain_text(filename, mime):
            pages, body = None, extract_text(data)
        else:
            pages = _extract(data, kind, filename)
            marker = SLIDE_MARKER if kind == "slides" else PART_MARKER if kind == "notes" else PAGE_MARKER
            body = render_pages(pages, marker) if pages else ""
        if not body.strip():
            self.store.keep_raw(channel.course if _is_course(channel) else "unsorted", filename, data)
            self.store.commit(f"ingest: {filename}")
            return [Outbound(esc(UNREADABLE_REPLY), kind="reply")]

        course, described = await self._describe_and_file(body, filename, channel)
        if course is None:
            return [Outbound(esc(COURSE_GONE_REPLY), kind="reply")]
        source = Source(
            path="",
            title=described["title"],
            course=course.slug,
            kind=described["kind"],
            topics=described["topics"],
            summary=described["summary"],
            pages=len(pages) if pages else None,
            body=body,
        )
        return [self._store_source(source, course)]

    async def _ingest_photo(
        self, image: bytes, caption: str | None, channel: Channel, kind: str | None = None
    ) -> list[Outbound]:
        self._touch()
        text = await self.agent.ocr(image, chat=(kind == "chat"))
        body = text if text else (caption or "")
        if kind is None and text and not _is_course(channel):
            kind = await self.agent.classify_photo(text)
        if text and kind == "chat":
            advice = await self.agent.coach(text, caption or "", recent=self._thread())
            if advice is None:
                return [Outbound(TUTOR_OFFLINE_REPLY, kind="reply")]
            self._remember("user", f"[screenshot of a conversation] {caption or ''}".strip())
            self._remember("assistant", advice)
            return [Outbound(md_to_html(advice), kind="reply")]
        if body.strip():
            course, described = await self._describe_and_file(body, "photo.jpg", channel)
        else:
            course = self._course(channel) if _is_course(channel) else self._course_named("General")
            described = {"title": "Photo", "topics": [], "summary": ""}
        if course is None:
            return [Outbound(esc(COURSE_GONE_REPLY), kind="reply")]
        source = Source(
            path="",
            title=described["title"],
            course=course.slug,
            kind="photo",
            topics=described["topics"],
            summary=described["summary"],
            ocr=None if text else "unavailable",
            body=body,
        )
        return [self._store_source(source, course)]

    async def _describe_and_file(
        self, body: str, filename: str, channel: Channel | None
    ) -> tuple[Course | None, dict]:
        """In a course topic the course is known; elsewhere the model picks it from the content."""
        if _is_course(channel):
            course = self._course(channel)
            if course is None:
                return None, {}
            return course, await self.agent.describe_source(body, filename, course.title)
        titles = [c.title for c in self.store.courses()]
        described = await self.agent.describe_source(body, filename, None, titles)
        course = self._course_named(described.get("course") or "General")
        described["filed"] = True
        return course, described

    def _save_binding(self, bound: Channel, message: str) -> None:
        channels = self.store.channels()
        channels.bind(bound)
        self.store.save_channels(channels)
        self.store.commit(message)

    def _course_named(self, name: str) -> Course:
        """Existing course by title or slug, else a new one."""
        wanted = name.strip()
        slug = slugify(wanted) or "general"
        for course in self.store.courses():
            if course.slug == slug or course.title.lower() == wanted.lower():
                return course
        course = Course(path=f"courses/{slug}.md", title=wanted or "General")
        self.store.add_course(course)
        return course

    def _store_source(self, source: Source, course: Course) -> Outbound:
        self.store.add_source(source)
        self._merge_topics(course, source.topics)
        self.store.commit(f"ingest: {source.title}")
        return Outbound(esc(_stored_reply(source, course)), kind="reply")

    def _merge_topics(self, course: Course, topics: list[str]) -> None:
        """The course keeps what its sources are about, in first-seen order."""
        merged = list(dict.fromkeys([*course.topics, *topics]))[:MAX_COURSE_TOPICS]
        if merged != course.topics:
            course.topics = merged
            self.store.save_course(course)

    async def _tutor(self, text: str, channel: Channel, via_voice: bool) -> list[Outbound]:
        self._touch()
        course = self._course(channel)
        if course is None:
            return [Outbound(esc(COURSE_GONE_REPLY), kind="reply")]
        sources = select_sources(text, self.store.sources(channel.course), self._tutor_budget())
        answer, note = await self.agent.tutor(text, course, sources)

        if note is not None:
            return [self._save_note(note, channel)]
        if answer is None:
            return [Outbound(TUTOR_OFFLINE_REPLY, kind="reply")]
        return [Outbound(md_to_html(answer), voice=self._voice_reply(via_voice), kind="reply")]

    async def _quiz(self, now, topic: str | None, course: str | None, n: int | None = None) -> list[Outbound]:
        """Start a session: on a topic (making cards if needed) or on whatever is due."""
        from bot.study.srs import due_cards
        profile = self.store.profile()
        if topic:
            slug = slugify(course) if course else None
            if slug is None:
                for c in self.store.courses():
                    if any(topic.lower() in t.lower() for t in c.topics):
                        slug = c.slug
                        break
            if slug is None:
                return [Outbound("Which course is that for?", kind="reply")]
            cards = await review.ensure_cards(self.store, self.agent, slug, topic, profile.cards_per_topic)
            if not cards:
                return [Outbound(f"Nothing stored to quiz you on for {esc(topic)}. Drop the material in first.", kind="reply")]
            cards = cards[: (n or profile.review_daily_cap)]
            label = f"Quiz: {topic}"
        else:
            cards = due_cards(self.store.cards(), now.date(), n or profile.review_daily_cap)
            if not cards:
                return [Outbound("Nothing due. Say \"quiz me on <topic>\" to drill something.", kind="reply")]
            label = "Review"
        out = review.start_session(now, self.store, self.state, cards, label)
        return [out] if out else []

    async def _plan_new_exams(self, actions: list[ToolCall], now) -> list[str]:
        """A new exam gets a day-by-day plan and one study todo per day."""
        lines: list[str] = []
        for action in actions:
            if action.name != "add_event" or action.arguments.get("kind") not in ("exam", "quiz"):
                continue
            exam = self._event_of(action)
            if exam is None:
                continue
            days_left = (exam.start.date() - now.date()).days
            if days_left < 1:
                continue
            profile = self.store.profile()
            sources = self.store.sources(exam.course) if exam.course else self.store.sources()
            lookup = None
            if not sources and self.search is not None:
                course_title = next((c.title for c in self.store.courses() if c.slug == exam.course), exam.course or "")
                lookup = await self.search.search(f"{course_title} {' '.join(exam.topics)} key concepts syllabus".strip())
            plan = await self.agent.plan_exam(exam, sources, days_left, profile.study_daily_minutes, lookup=lookup)
            if plan is None or not plan["days"]:
                lines.append(esc(f"{days_left} days until {exam.title}. Couldn't draft a plan right now; ask me again in a bit."))
                continue
            total = 0
            for d in plan["days"]:
                if d["date"] < now.date() or d["date"] >= exam.start.date():
                    continue
                self.store.add(Todo(path="", title=f"Study: {d['task'][:80]}", priority=1, due=d["date"],
                                    course=exam.course, kind="study"))
                total += d["minutes"]
            self.store.commit(f"plan: {exam.title}")
            per_day = round(total / max(1, len(plan["days"])))
            head = f"<b>Plan for {esc(exam.title)}</b> — {days_left} days, about {per_day} min/day"
            body = "\n".join(f"{d['date']:%a %b %d}: {esc(d['task'])} ({d['minutes']} min)" for d in plan["days"])
            advice = md_to_html(plan["advice"]) if plan["advice"] else ""
            lines.append("\n".join(x for x in (head, body, advice) if x))
        return lines

    async def _recall(self, query: str) -> list[str]:
        """Keyword hits plus, when an index exists, semantic hits over the same corpus."""
        hits = self.store.recall(query)
        if self.index is None:
            return hits
        try:
            await self.index.sync(self.store.recall_corpus())
            for score, line in await self.index.search(query, k=8):
                if line not in hits:
                    hits.append(line)
        except Exception:
            logger.exception("semantic recall failed; keyword hits only")
        return hits[:16]

    async def _search(self, query: str, question: str, via_voice: bool) -> list[Outbound]:
        results = await self.search.search(query)
        if results is None:
            return [Outbound("Search isn't answering right now.", kind="reply")]
        answer = await self.agent.answer(
            question,
            f"Live web search results fetched just now for \"{query}\" (you DO have current information — "
            f"never say you lack internet access or a knowledge cutoff; answer from these and cite the URL used):\n{results}",
        )
        if answer is None:
            return [Outbound(TUTOR_OFFLINE_REPLY, kind="reply")]
        return [Outbound(md_to_html(answer), voice=self._voice_reply(via_voice), kind="reply")]

    async def _study(self, question: str, course_slug: str | None, via_voice: bool) -> list[Outbound]:
        """Tutor from the DM: one course when the model named it, otherwise everything stored."""
        course: Course | None = None
        if course_slug:
            try:
                course = self.store.get_course(slugify(course_slug))
            except KeyError:
                course = None
        sources = self.store.sources(course.slug if course else None)
        if not sources:
            return [Outbound("Nothing stored to study from yet. Drop in slides, a PDF, or notes.", kind="reply")]
        scope = course or Course(path="", title="your notes")
        selected = select_sources(question, sources, self._tutor_budget())
        answer, _ = await self.agent.tutor(
            question, scope, selected, notes_tool=False, client=self.agent.hard or None
        )
        if answer is None:
            return [Outbound(TUTOR_OFFLINE_REPLY, kind="reply")]
        return [Outbound(md_to_html(answer), voice=self._voice_reply(via_voice), kind="reply")]

    def _course(self, channel: Channel) -> Course | None:
        try:
            return self.store.get_course(channel.course)
        except KeyError:
            logger.warning("course file %s is gone", channel.course)
            return None

    def _tutor_budget(self) -> int:
        """The fallback model is a rented context; don't ship it the local budget."""
        profile = self.store.profile()
        if getattr(self.agent.client, "breaker_open", False):
            return profile.tutor_context_chars_fallback
        return profile.tutor_context_chars

    def _save_note(self, note: dict, channel: Channel) -> Outbound:
        text = note["text"].strip()
        title = text.splitlines()[0][:NOTE_TITLE_CHARS]
        source = Source(
            path="",
            title=title,
            course=channel.course,
            kind="notes",
            topics=note["topics"],
            summary=text[:NOTE_SUMMARY_CHARS],
            body=text,
        )
        self.store.add_source(source)
        self.store.commit(f"note: {title}")
        return Outbound(f"Saved note: {esc(title)}", kind="reply")

    async def on_callback(
        self,
        data: str,
        message_id: int | None = None,
        message_html: str | None = None,
        buttons: list[tuple[str, str]] | None = None,
        *,
        channel: Channel | None = None,
    ) -> list[Outbound]:
        now = self._touch()
        close_chain(self.state)
        outs = await callbacks.handle(
            data, self.store, self.agent, self.state, now, message_id, message_html, buttons
        )
        return self._tag(outs, channel)

    async def command(
        self, name: str, arg: str, *, channel: Channel | None = None
    ) -> list[Outbound]:
        now = self._touch()
        close_chain(self.state)
        outs = await commands.handle(
            name, arg, self.store, self.agent, self.state, now, channel,
            recall=self._recall, recent=self._thread(), search=self.search, cluster=self.cluster,
        )
        return self._tag(outs, channel)

    # -- helpers ---------------------------------------------------------

    def _ignore_unbound(self, channel: Channel | None) -> list[Outbound] | None:
        """Warn once per unbound topic, then say nothing there. None means carry on."""
        if channel is None or channel.kind != UNBOUND:
            return None
        if channel.key in self._warned_threads:
            return []
        self._warned_threads.add(channel.key)
        return [
            Outbound(
                esc(UNBOUND_REPLY),
                kind="reply",
                target=(channel.chat_id, channel.thread_id),
            )
        ]

    def _tag(self, outs: list[Outbound], channel: Channel | None) -> list[Outbound]:
        """Send replies back to the topic they were asked in."""
        if channel is None or channel.kind in ("life", UNBOUND):
            return outs
        for out in outs:
            if out.channel == "life":
                out.channel = channel.name
        return outs

    def _touch(self):
        now = self.clock.now()
        self.state.last_user_message_at = now
        self.last_outcome = "handled"
        return now

    def _voice_reply(self, via_voice: bool) -> bool:
        mode = self.store.profile().voice_reply_mode
        return via_voice if mode == "on_voice" else mode == "always"

    def _remember(self, role: str, text: str) -> None:
        remember(self.state, role, text, self.clock.now())
        self.store.log_chat(role, text)

    def _thread(self) -> list:
        """Today's conversation, the model's working memory."""
        return thread(self.state, self.clock.now())

    async def _recalled(self, text: str) -> list[str]:
        """A few things from the vault that match this message, so old context resurfaces on its own."""
        if self.agent.degraded or len(text) < 12:
            return []
        try:
            return (await self._recall(text))[:5]
        except Exception:
            logger.exception("recall before capture failed")
            return []

    async def _capture(self, text: str, awaiting: str | None, via_voice: bool, now) -> list[Outbound]:
        result = await self.agent.capture(
            text, awaiting=awaiting, recent=self._thread(), recalled=await self._recalled(text)
        )
        self.last_outcome = "captured" if result.parsed else "inbox"
        if not result.parsed:
            # A failed exchange is not part of the conversation: remembered, the fallback line
            # reads as his own words and the next model copies it.
            return [Outbound(esc(result.reply), kind="reply")]
        outs = await self._captured(text, result, awaiting, via_voice, now)
        # Remember what was actually sent — the search answer, the directions line — not the
        # model's placeholder "Sending it.", or "what's the address" is forgotten a message later.
        self._remember("user", text)
        if outs and outs[0].text:
            self._remember("assistant", plain_text(outs[0].text))
        return outs

    async def _captured(self, text: str, result, awaiting, via_voice: bool, now) -> list[Outbound]:
        if any(a.name == "undo" for a in result.actions):
            subject = self.store.undo()
            return [Outbound("Nothing to undo." if subject is None else f"Reverted: {esc(subject)}", kind="reply")]
        applied = apply_actions(self.store, result.actions, now)

        if applied.snooze_minutes is not None:
            self.state.pause_until = (
                None if applied.snooze_minutes <= 0 else now + timedelta(minutes=applied.snooze_minutes)
            )

        await self._geocode_home(result.actions)
        travel_lines = await self._geocode_events(result.actions, applied)
        plan_lines = await self._plan_new_exams(result.actions, now)

        if self.agent.degraded and any(a.name in ("coach", "recall", "study") for a in result.actions):
            return [Outbound(SPARK_DOWN_WAIT, kind="reply")]

        briefing = next((a for a in result.actions if a.name == "briefing"), None)
        if briefing is not None:
            build = evening_outbound if briefing.arguments.get("which") == "evening" else morning_outbound
            return [await build(self.store, now, self.agent)]

        coach = next((a for a in result.actions if a.name == "coach"), None)
        if coach is not None:
            advice = await self.agent.coach(str(coach.arguments.get("thread", text)),
                                            str(coach.arguments.get("ask", "")), recent=self._thread())
            if advice is None:
                return [Outbound(TUTOR_OFFLINE_REPLY, kind="reply")]
            return [Outbound(md_to_html(advice), voice=self._voice_reply(via_voice), kind="reply")]

        recall = next((a for a in result.actions if a.name == "recall"), None)
        if recall is not None:
            hits = await self._recall(str(recall.arguments.get("query", text)))
            context = "What I have on that:\n" + ("\n".join(f"- {h}" for h in hits) if hits else "- nothing")
            answer = await self.agent.answer(text, context)
            return [Outbound(md_to_html(answer) if answer else esc(context), voice=self._voice_reply(via_voice), kind="reply")]

        search = next((a for a in result.actions if a.name == "search"), None)
        if search is not None and self.search is not None:
            return await self._search(str(search.arguments.get("query", text)), text, via_voice)

        study = next((a for a in result.actions if a.name == "study"), None)
        if study is not None:
            return await self._study(
                str(study.arguments.get("question", text)), study.arguments.get("course"), via_voice
            )

        reply_html = md_to_html(result.reply)
        if self.agent.degraded and reply_html:
            reply_html = "☁️ " + reply_html  # so it's always clear which model you're talking to
        lines = [reply_html] + [esc(s) for s in applied.summary] + travel_lines + plan_lines
        ask_location = False
        for a in result.actions:
            if a.name == "directions":
                line, need_location = await self._directions(
                    str(a.arguments.get("destination", "")), str(a.arguments.get("mode") or "drive")
                )
                lines.append(line)
                ask_location = ask_location or need_location
        asked_voice = any(a.name == "reply" and a.arguments.get("voice") is True for a in result.actions)
        return [
            Outbound(
                "\n".join(line for line in lines if line),
                voice=self._voice_reply(via_voice) or asked_voice,
                location_button=ask_location,
                kind="reply",
            )
        ]

    LOCATION_FRESH_HOURS = 3

    def _origin(self, now) -> tuple[tuple[float, float] | None, str]:
        """Where he is: the location he shared in the last few hours, else home. (latlng, label)"""
        loc = self.state.last_location
        if loc and loc.get("at"):
            try:
                if now - datetime.fromisoformat(loc["at"]) <= timedelta(hours=self.LOCATION_FRESH_HOURS):
                    return (float(loc["lat"]), float(loc["lng"])), "from you"
            except (ValueError, TypeError, KeyError):
                pass
        return self.store.profile().home_latlng, "from home"

    async def _directions(self, destination: str, mode: str) -> tuple[str, bool]:
        """'Mags: 12 min walk (0.9 km) from you. Directions.' Returns (line, needs_location)."""
        now = self.clock.now()
        origin, where = self._origin(now)
        place = None
        if self.maps is not None and destination.strip():
            place = await self.maps.find_place(destination, near=origin)
        name = place["name"] if place else destination
        url = directions_url(place["address"] or name if place else destination, place["latlng"] if place else None)
        link = f'<a href="{url}">Directions</a>' if url else ""
        if self.maps is None or origin is None or place is None:
            line = f"{esc(name)}. {link}".strip()
            if self.maps is not None and origin is None:
                line += "\nShare your location and I'll tell you how far."
            return line, self.maps is not None and origin is None
        route = await self.maps.route(origin, place["latlng"], mode)
        if route is None:
            return f"{esc(name)}. {link}", False
        minutes, meters = route
        how = "walk" if mode == "walk" else "drive"
        dist = f"{meters / 1609.34:.1f} mi" if meters >= 400 else f"{int(meters * 3.281)} ft"
        return f"{esc(name)}: {minutes} min {how} ({dist}) {where}. {link}", False

    async def _geocode_home(self, actions: list[ToolCall]) -> None:
        """Coordinates for a new home address or saved place; home follows the current base."""
        if self.maps is None:
            return
        profile = self.store.profile()
        changed = False
        for a in actions:
            if a.name == "set_profile" and a.arguments.get("field") == "home_address":
                latlng = await self.maps.geocode(str(a.arguments.get("value") or ""))
                if latlng is not None:
                    profile.home_latlng = latlng
                    changed = True
            elif a.name == "save_place":
                name = " ".join(str(a.arguments["name"]).lower().replace("'", "").split())
                latlng = await self.maps.geocode(str(a.arguments["address"]))
                if latlng is not None:
                    profile.places_latlng[name] = [latlng[0], latlng[1]]
                    if profile.base == name:
                        profile.home_latlng = latlng
                    changed = True
        if changed:
            self.store.save_profile(profile)
            self.store.commit("profile: geocoded places")

    async def _geocode_events(self, actions: list[ToolCall], applied) -> list[str]:
        """Events need coordinates for the traffic refresh and arrival detection; a weekly series
        gets them on the template and every occurrence. Travel time from home is estimated right
        away so the first leave-by reminder is not computed from zero. Returns lines for the reply."""
        if self.maps is None or not applied.changed_schedule:
            return []
        profile = self.store.profile()
        geocoded: list[str] = []
        notes: list[str] = []
        for action in actions:
            location = action.arguments.get("location")
            if not location:
                continue
            targets = self._events_of(action)
            if not targets:
                continue
            latlng = await self.maps.geocode(location)
            if latlng is None:
                logger.warning("could not geocode event location: %s", location)
                continue
            minutes = None
            if profile.home_latlng and not action.arguments.get("travel_minutes"):
                minutes = await self.maps.travel_minutes(profile.home_latlng, latlng, depart_at=targets[0].start)
            elif not profile.home_latlng and "Tell me your home address" not in " ".join(notes):
                notes.append("Tell me your home address and I'll do traffic for this.")
            for event in targets:
                event.location_latlng = latlng
                if minutes is not None:
                    event.travel_minutes = minutes
                self.store.save(event)
            geocoded.append(targets[0].title)
            if minutes is not None:
                leave_by = targets[0].times(profile).leave_by
                notes.append(f"Traffic from home {minutes} min, so leave by {leave_by:%-I:%M %p}.")
        if geocoded:
            self.store.commit(f"geocode: {geocoded[0]}")
        return notes

    def _events_of(self, action: ToolCall) -> list:
        """The event(s) an add/update touched: one event, or a series template plus its occurrences."""
        args = action.arguments
        try:
            if action.name == "add_event" and args.get("repeat_days"):
                start = datetime.fromisoformat(args["start"])
                tpl = next((t for t in self.store.series() if t.title == args["title"] and t.start == start), None)
                if tpl is None:
                    return []
                return [tpl] + [e for e in self.store.events() if e.series == tpl.path]
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("could not resolve series for %s: %s", action.name, exc)
            return []
        event = self._event_of(action)
        return [event] if event is not None else []

    def _event_of(self, action: ToolCall):
        args = action.arguments
        try:
            if action.name == "add_event":
                start = datetime.fromisoformat(args["start"])
                return next(
                    (e for e in self.store.events() if e.title == args["title"] and e.start == start),
                    None,
                )
            if action.name == "update_event":
                return self.store.get_event(args["file"])
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("could not resolve event for %s: %s", action.name, exc)
        return None

    async def _verify_answer(self, pending, text: str) -> Outbound:
        specific = await self.agent.rate_answer(pending.question or "", text)
        self._settle(pending, specific)
        return Outbound("Logged." if specific else "Logged as unconfirmed.", kind="reply")

    async def _verify_photo(self, pending, image: bytes) -> Outbound:
        todo = self.store.get_todo(pending.todo_path)
        ok, _reason = await self.agent.check_photo(image, f"evidence that this is done: {todo.title}")
        if ok is None:
            self._settle(pending, True)
            return Outbound(f"Can't verify photos right now, taking your word for it: {esc(todo.title)}", kind="reply")
        self._settle(pending, ok)
        if ok:
            return Outbound(f"Verified: {esc(todo.title)}", kind="reply")
        return Outbound(f"That doesn't show it done. Logged as unconfirmed: {esc(todo.title)}", kind="reply")

    async def _verify_location(self, pending, lat: float, lng: float) -> Outbound:
        todo = self.store.get_todo(pending.todo_path)
        target = await self._todo_latlng(todo)
        if target is None:
            self._settle(pending, True)
            return Outbound(f"Can't check that place, taking your word for it: {esc(todo.title)}", kind="reply")
        ok = distance_m((lat, lng), target) < VERIFY_RADIUS_M
        self._settle(pending, ok)
        if ok:
            return Outbound(f"Verified: {esc(todo.title)}", kind="reply")
        return Outbound(f"That's not the place. Logged as unconfirmed: {esc(todo.title)}", kind="reply")

    async def _todo_latlng(self, todo) -> tuple[float, float] | None:
        if self.maps is None:
            return None
        for line in todo.body.splitlines():
            if line.lower().startswith("location:"):
                return await self.maps.geocode(line.split(":", 1)[1].strip())
        return None

    def _settle(self, pending, confirmed: bool) -> None:
        try:
            todo = self.store.get_todo(pending.todo_path)
        except KeyError:
            self.state.pending_verify = None
            return
        todo.confirmed = confirmed
        self.store.save(todo)
        self.store.commit(f"verify: {todo.title}")
        self.state.pending_verify = None
