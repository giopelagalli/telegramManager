from __future__ import annotations

import logging
from datetime import datetime, timedelta

from bot.agent.agent import apply_actions
from bot.agent.client import ToolCall
from bot.agent.prompts import build_context
from bot.knowledge.views import esc
from bot.maps.client import distance_m
from bot.scheduler.chains import close_chain
from bot.scheduler.critical import leave_on_location, leave_on_text, wake_on_message, wake_on_photo
from bot.scheduler.outbound import Outbound
from bot.telegram import callbacks, commands

logger = logging.getLogger(__name__)

VERIFY_RADIUS_M = 200


class Router:
    """Turns a user action into a list of outbound messages. No telegram types here."""

    def __init__(self, store, agent, state, clock, maps):
        self.store = store
        self.agent = agent
        self.state = state
        self.clock = clock
        self.maps = maps
        self.last_outcome = "handled"

    # -- entry points ----------------------------------------------------

    async def on_text(self, text: str, via_voice: bool = False) -> list[Outbound]:
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
            return [Outbound(esc(question), kind="wake")]

        if self.state.critical is not None:
            out = leave_on_text(self.state, self.store)
            return [out] if out is not None else []

        pending = self.state.pending_verify
        if pending is not None and pending.kind == "question":
            return [await self._verify_answer(pending, text)]

        return await self._capture(text, awaiting, via_voice, now)

    async def on_voice_unavailable(self) -> list[Outbound]:
        return [Outbound("Voice input isn't set up here. Send it as text.", kind="reply")]

    async def on_voice_failed(self, reason: str) -> list[Outbound]:
        self._touch()
        self.store.add_inbox(f"[voice note could not be transcribed: {reason}]")
        self.store.commit("inbox: voice note")
        return [Outbound("Couldn't transcribe that. Saved a note in your inbox.", kind="reply")]

    async def on_location(self, lat: float, lng: float) -> list[Outbound]:
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

    async def on_photo(self, image: bytes) -> list[Outbound]:
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

    async def on_callback(
        self,
        data: str,
        message_id: int | None = None,
        message_html: str | None = None,
        buttons: list[tuple[str, str]] | None = None,
    ) -> list[Outbound]:
        now = self._touch()
        close_chain(self.state)
        return await callbacks.handle(
            data, self.store, self.agent, self.state, now, message_id, message_html, buttons
        )

    async def command(self, name: str, arg: str) -> list[Outbound]:
        now = self._touch()
        close_chain(self.state)
        return await commands.handle(name, arg, self.store, self.agent, self.state, now)

    # -- helpers ---------------------------------------------------------

    def _touch(self):
        now = self.clock.now()
        self.state.last_user_message_at = now
        self.last_outcome = "handled"
        return now

    def _wake_active(self) -> bool:
        return self.state.wake is not None and self.state.wake.phase != "done"

    async def _capture(self, text: str, awaiting: str | None, via_voice: bool, now) -> list[Outbound]:
        result = await self.agent.capture(text, awaiting=awaiting)
        self.last_outcome = "captured" if result.parsed else "inbox"
        applied = apply_actions(self.store, result.actions, now)

        if applied.snooze_minutes is not None:
            self.state.pause_until = now + timedelta(minutes=applied.snooze_minutes)

        await self._geocode_home(result.actions)
        await self._geocode_events(result.actions, applied)

        profile = self.store.profile()
        if profile.voice_reply_mode == "on_voice":
            voice = via_voice
        else:
            voice = profile.voice_reply_mode == "always"

        lines = [esc(result.reply)] + [esc(s) for s in applied.summary]
        return [Outbound("\n".join(line for line in lines if line), voice=voice, kind="reply")]

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
