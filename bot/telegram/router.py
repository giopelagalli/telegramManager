from __future__ import annotations

import logging
from datetime import datetime, timedelta

from bot.agent.agent import apply_actions
from bot.agent.client import ToolCall
from bot.agent.prompts import build_context
from bot.knowledge.models import UNBOUND, Channel, Course, Source
from bot.knowledge.views import esc
from bot.maps.client import distance_m
from bot.scheduler.chains import close_chain
from bot.scheduler.critical import leave_on_location, leave_on_text, wake_on_message, wake_on_photo
from bot.scheduler.outbound import Outbound
from bot.study.extract import (
    PAGE_MARKER,
    PART_MARKER,
    SLIDE_MARKER,
    extract_docx,
    extract_pdf,
    extract_pptx,
    guess_kind,
    render_pages,
)
from bot.study.select import select_sources
from bot.telegram import callbacks, commands
from bot.telegram.markdown import md_to_html

logger = logging.getLogger(__name__)

VERIFY_RADIUS_M = 200
NOTE_TITLE_CHARS = 60
MAX_COURSE_TOPICS = 50
NOTE_SUMMARY_CHARS = 300
UNREADABLE_REPLY = "Stored the file but couldn't read it."
TUTOR_OFFLINE_REPLY = "The model is offline; ask again in a bit."
TOO_LARGE_REPLY = (
    "That file is over Telegram's 20 MB bot limit. Split it or send a smaller export."
)
UNBOUND_REPLY = (
    "This topic isn't bound yet. Run /bind course <CODE> <title>, /bind assignments, "
    "/bind exams, or /bind review here."
)


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


def _stored_reply(source: Source) -> str:
    parts = [source.kind]
    if source.pages:
        parts.append(f"{source.pages} pages")
    if source.topics:
        parts.append("topics: " + ", ".join(source.topics))
    note = " OCR unavailable." if source.ocr == "unavailable" else ""
    return f"Stored: {source.title} ({', '.join(parts)}).{note} Wrong course? /move <slug>."


class Router:
    """Turns a user action into a list of outbound messages. No telegram types here."""

    def __init__(self, store, agent, state, clock, maps):
        self.store = store
        self.agent = agent
        self.state = state
        self.clock = clock
        self.maps = maps
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

        if self._wake_active():
            out = wake_on_message(now, self.state, self.store, text)
            if out is not None:
                return [out]
            question = await self.agent.compose(
                "wake", build_context(self.store, now), "What's next after that?"
            )
            return [Outbound(md_to_html(question), kind="wake")]

        if self.state.critical is not None:
            out = leave_on_text(self.state, self.store)
            return [out] if out is not None else []

        pending = self.state.pending_verify
        if pending is not None and pending.kind == "question":
            return [await self._verify_answer(pending, text)]

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
        if out is not None:
            return [out]
        return [Outbound("Got your location, nothing waiting for it.", kind="reply")]

    async def on_photo(
        self, image: bytes, caption: str | None = None, *, channel: Channel | None = None
    ) -> list[Outbound]:
        ignored = self._ignore_unbound(channel)
        if ignored is not None:
            return ignored
        if _is_course(channel):
            return self._tag(await self._ingest_photo(image, caption, channel), channel)
        return self._tag(await self._photo(image), channel)

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
        if not _is_course(channel):
            return []
        return self._tag(await self._ingest_document(data, filename, mime, caption, channel), channel)

    async def on_file_too_large(self, *, channel: Channel | None = None) -> list[Outbound]:
        """The handler saw the size before downloading; nothing was fetched."""
        ignored = self._ignore_unbound(channel)
        if ignored is not None:
            return ignored
        self._touch()
        return self._tag([Outbound(esc(TOO_LARGE_REPLY), kind="reply")], channel)

    async def _photo(self, image: bytes) -> list[Outbound]:
        now = self._touch()

        if self._wake_active() and self.state.wake.phase == "challenge":
            close_chain(self.state)
            spot = self.store.profile().wake_photo_spot
            ok, reason = await self.agent.check_photo(
                image, f"a fresh photo of a {spot}, not a screenshot"
            )
            return [wake_on_photo(now, self.state, self.store, ok, reason)]

        close_chain(self.state)

        pending = self.state.pending_verify
        if pending is not None and pending.kind == "photo":
            return [await self._verify_photo(pending, image)]

        return [Outbound("Got a photo, but nothing waiting for one.", kind="reply")]

    # -- study ingest and tutoring ---------------------------------------

    async def _ingest_document(
        self, data: bytes, filename: str, mime: str, caption: str | None, channel: Channel
    ) -> list[Outbound]:
        self._touch()
        kind = guess_kind(filename, mime)
        if kind == "photo":
            return await self._ingest_photo(data, caption, channel)

        pages = _extract(data, kind, filename)
        marker = SLIDE_MARKER if kind == "slides" else PART_MARKER if kind == "notes" else PAGE_MARKER
        body = render_pages(pages, marker) if pages else ""
        if not body.strip():
            self.store.keep_raw(channel.course, filename, data)
            self.store.commit(f"ingest: {filename}")
            return [Outbound(esc(UNREADABLE_REPLY), kind="reply")]

        course = self.store.get_course(channel.course)
        described = await self.agent.describe_source(body, filename, course.title)
        source = Source(
            path="",
            title=described["title"],
            course=channel.course,
            kind=described["kind"],
            topics=described["topics"],
            summary=described["summary"],
            pages=len(pages),
            body=body,
        )
        return [self._store_source(source, course)]

    async def _ingest_photo(
        self, image: bytes, caption: str | None, channel: Channel
    ) -> list[Outbound]:
        self._touch()
        course = self.store.get_course(channel.course)
        text = await self.agent.ocr(image)
        body = text if text else (caption or "")
        if body.strip():
            described = await self.agent.describe_source(body, "photo.jpg", course.title)
        else:
            described = {"title": "Photo", "topics": [], "summary": ""}
        source = Source(
            path="",
            title=described["title"],
            course=channel.course,
            kind="photo",
            topics=described["topics"],
            summary=described["summary"],
            ocr=None if text else "unavailable",
            body=body,
        )
        return [self._store_source(source, course)]

    def _store_source(self, source: Source, course: Course) -> Outbound:
        self.store.add_source(source)
        self._merge_topics(course, source.topics)
        self.store.commit(f"ingest: {source.title}")
        return Outbound(esc(_stored_reply(source)), kind="reply")

    def _merge_topics(self, course: Course, topics: list[str]) -> None:
        """The course keeps what its sources are about, in first-seen order."""
        merged = list(dict.fromkeys([*course.topics, *topics]))[:MAX_COURSE_TOPICS]
        if merged != course.topics:
            course.topics = merged
            self.store.save_course(course)

    async def _tutor(self, text: str, channel: Channel, via_voice: bool) -> list[Outbound]:
        self._touch()
        course = self.store.get_course(channel.course)
        sources = select_sources(text, self.store.sources(channel.course), self._tutor_budget())
        answer, note = await self.agent.tutor(text, course, sources)

        if note is not None:
            return [self._save_note(note, channel)]
        if answer is None:
            return [Outbound(TUTOR_OFFLINE_REPLY, kind="reply")]
        return [Outbound(md_to_html(answer), voice=self._voice_reply(via_voice), kind="reply")]

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
        outs = await commands.handle(name, arg, self.store, self.agent, self.state, now, channel)
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

    def _wake_active(self) -> bool:
        return self.state.wake is not None and self.state.wake.phase != "done"

    def _voice_reply(self, via_voice: bool) -> bool:
        mode = self.store.profile().voice_reply_mode
        return via_voice if mode == "on_voice" else mode == "always"

    async def _capture(self, text: str, awaiting: str | None, via_voice: bool, now) -> list[Outbound]:
        result = await self.agent.capture(text, awaiting=awaiting)
        self.last_outcome = "captured" if result.parsed else "inbox"
        applied = apply_actions(self.store, result.actions, now)

        if applied.snooze_minutes is not None:
            self.state.pause_until = now + timedelta(minutes=applied.snooze_minutes)

        await self._geocode_home(result.actions)
        await self._geocode_events(result.actions, applied)

        lines = [md_to_html(result.reply)] + [esc(s) for s in applied.summary]
        return [
            Outbound(
                "\n".join(line for line in lines if line),
                voice=self._voice_reply(via_voice),
                kind="reply",
            )
        ]

    async def _geocode_home(self, actions: list[ToolCall]) -> None:
        if self.maps is None:
            return
        addresses = [
            a.arguments.get("value")
            for a in actions
            if a.name == "set_profile" and a.arguments.get("field") == "home_address"
        ]
        addresses = [a for a in addresses if a is not None]
        if not addresses:
            return
        latlng = await self.maps.geocode(addresses[-1])
        if latlng is None:
            return
        profile = self.store.profile()
        profile.home_latlng = latlng
        self.store.save_profile(profile)
        self.store.commit("profile: geocoded home address")

    async def _geocode_events(self, actions: list[ToolCall], applied) -> None:
        """Events need coordinates for the traffic refresh and arrival detection."""
        if self.maps is None or not applied.changed_schedule:
            return
        geocoded: list[str] = []
        for action in actions:
            location = action.arguments.get("location")
            if not location:
                continue
            event = self._event_of(action)
            if event is None:
                continue
            latlng = await self.maps.geocode(location)
            if latlng is None:
                logger.warning("could not geocode event location: %s", location)
                continue
            event.location_latlng = latlng
            self.store.save(event)
            geocoded.append(event.title)
        if geocoded:
            self.store.commit(f"geocode: {geocoded[0]}")

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
