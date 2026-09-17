from __future__ import annotations

from datetime import timedelta

from bot.agent.agent import apply_actions
from bot.agent.client import ToolCall
from bot.agent.prompts import build_context
from bot.knowledge.views import esc
from bot.scheduler.chains import close_chain
from bot.scheduler.outbound import Outbound
from bot.scheduler.state import PendingVerify
from bot.telegram.markdown import md_to_html

SNOOZE_MINUTES = 30

_ASK = {
    "photo": "Send a photo showing it's done.",
    "location": "Share your location to confirm.",
}


async def handle(
    data: str,
    store,
    agent,
    state,
    now,
    message_id: int | None = None,
    message_html: str | None = None,
    buttons: list[tuple[str, str]] | None = None,
) -> list[Outbound]:
    action, _, arg = data.partition(":")
    remaining = [b for b in (buttons or []) if b[1] != data]
    if action == "done":
        return await _done(arg, store, agent, state, now, message_id, message_html, remaining)
    if action == "defer":
        return _defer(arg, store, now, message_id, message_html, remaining)
    if action == "ack":
        return _ack(arg, store, state, now, message_id, message_html)
    if action == "quiz":
        course, _, topic = arg.partition(":")
        return await router_quiz(course, topic, store, agent, state, now)
    if action == "sprint":
        return _sprint(arg, store, state, now)
    if action == "resume":
        state.pause_until = None
        return [Outbound("Back on.", kind="reply")]
    if action == "snooze":
        state.pause_until = now + timedelta(minutes=SNOOZE_MINUTES)
        return [Outbound(f"Ok, {SNOOZE_MINUTES} minutes of quiet.", kind="reply")]
    return [Outbound("I don't know that button.", kind="reply")]


async def _done(path, store, agent, state, now, message_id, message_html, remaining) -> list[Outbound]:
    todo = _get(store, path)
    if todo is None:
        return [Outbound("That item is gone.", kind="reply")]

    apply_actions(store, [ToolCall("update_todo", {"file": path, "status": "done"})], now)

    if todo.verify == "none":
        edited = _rewrite(message_html, todo.title, lambda line: f"<s>{line}</s>")
        if edited is not None:
            return [
                Outbound(
                    edited,
                    buttons=remaining,
                    edit_message_id=message_id,
                    toast=f"Done: {todo.title}",
                    kind="edit",
                )
            ]
        return [Outbound(f"Done: {esc(todo.title)}", kind="reply")]

    question = None
    if todo.verify == "question":
        question = await agent.compose(
            "verify_question", build_context(store, now), f"How did {todo.title} go?"
        )
        text = md_to_html(question)
    else:
        text = _ASK[todo.verify]

    state.pending_verify = PendingVerify(
        todo_path=path, kind=todo.verify, asked_at=now, question=question
    )
    return [
        Outbound(
            text,
            location_button=todo.verify == "location",
            toast="Marked done, verify below",
            kind="reply",
        )
    ]


def _defer(path, store, now, message_id, message_html, remaining) -> list[Outbound]:
    todo = _get(store, path)
    if todo is None:
        return [Outbound("That item is gone.", kind="reply")]
    todo.due = now.date() + timedelta(days=1)
    store.save(todo)
    store.commit(f"defer: {todo.title}")
    edited = _rewrite(message_html, todo.title, lambda line: f"{line} → tomorrow")
    if edited is not None:
        return [
            Outbound(
                edited,
                buttons=remaining,
                edit_message_id=message_id,
                toast="Moved to tomorrow",
                kind="edit",
            )
        ]
    return [Outbound(f"Moved to tomorrow: {esc(todo.title)}", kind="reply")]


def _ack(arg: str, store, state, now, message_id, message_html) -> list[Outbound]:
    close_chain(state)
    if arg == "skip":
        _, end = store.profile().waking_window(now.date())
        state.pause_until = end
        toast = "Quiet until tomorrow morning."
    else:
        toast = "Ok, I'll check back later."
    if message_html is None:
        return [Outbound("Ok.", kind="reply")]
    return [Outbound(message_html, edit_message_id=message_id, toast=toast, kind="edit")]


def _rewrite(message_html: str | None, title: str, mark) -> str | None:
    """The line naming this todo, rewritten in place. None when there's nothing to edit."""
    if message_html is None:
        return None
    lines = message_html.split("\n")
    needle = esc(title)
    for i, line in enumerate(lines):
        if needle in line:
            lines[i] = mark(line)
            return "\n".join(lines)
    return None


def _get(store, path: str):
    try:
        return store.get_todo(path)
    except KeyError:
        return None



def _sprint(path: str, store, state, now) -> list[Outbound]:
    from bot.scheduler.checkins import SPRINT_MINUTES
    from bot.scheduler.chains import close_chain
    todo = _get(store, path)
    if todo is None:
        return [Outbound("That item is gone.", kind="reply")]
    close_chain(state)
    ends_at = now + timedelta(minutes=SPRINT_MINUTES)
    state.sprint = {"path": path, "title": todo.title, "ends_at": ends_at.isoformat()}
    state.pause_until = max(state.pause_until or now, ends_at)  # no check-ins mid-sprint
    return [Outbound(f"Go. {esc(todo.title)}, {SPRINT_MINUTES} minutes. I'll check back at {_hhmm(ends_at)}.", kind="reply")]


def _hhmm(dt) -> str:
    from bot.knowledge.views import fmt_time
    return fmt_time(dt)


async def router_quiz(course: str, topic: str, store, agent, state, now) -> list[Outbound]:
    from bot.scheduler import review
    cards = await review.ensure_cards(store, agent, course, topic, store.profile().cards_per_topic)
    out = review.start_session(now, store, state, cards[:review.MINI_SESSION], f"Quiz: {topic}")
    return [out] if out else [Outbound("Nothing to quiz on that yet.", kind="reply")]
