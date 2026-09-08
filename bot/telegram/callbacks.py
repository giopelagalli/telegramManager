from __future__ import annotations

from datetime import timedelta

from bot.agent.agent import apply_actions
from bot.agent.client import ToolCall
from bot.agent.prompts import build_context
from bot.knowledge.views import esc
from bot.scheduler.outbound import Outbound
from bot.scheduler.state import PendingVerify

SNOOZE_MINUTES = 30

_ASK = {
    "photo": "Send a photo showing it's done.",
    "location": "Share your location to confirm.",
}


async def handle(data: str, store, agent, state, now) -> list[Outbound]:
    action, _, arg = data.partition(":")
    if action == "done":
        return await _done(arg, store, agent, state, now)
    if action == "defer":
        return _defer(arg, store, now)
    if action == "snooze":
        state.pause_until = now + timedelta(minutes=SNOOZE_MINUTES)
        return [Outbound(f"Ok, {SNOOZE_MINUTES} minutes of quiet.", kind="reply")]
    return [Outbound("I don't know that button.", kind="reply")]


async def _done(path: str, store, agent, state, now) -> list[Outbound]:
    todo = _get(store, path)
    if todo is None:
        return [Outbound("That item is gone.", kind="reply")]

    apply_actions(store, [ToolCall("update_todo", {"file": path, "status": "done"})], now)

    if todo.verify == "none":
        return [Outbound(f"Done: {esc(todo.title)}", kind="reply")]

    question = None
    if todo.verify == "question":
        question = await agent.compose(
            "verify_question", build_context(store, now), f"How did {todo.title} go?"
        )
        text = esc(question)
    else:
        text = _ASK[todo.verify]

    state.pending_verify = PendingVerify(
        todo_path=path, kind=todo.verify, asked_at=now, question=question
    )
    return [Outbound(text, location_button=todo.verify == "location", kind="reply")]


def _defer(path: str, store, now) -> list[Outbound]:
    todo = _get(store, path)
    if todo is None:
        return [Outbound("That item is gone.", kind="reply")]
    todo.due = now.date() + timedelta(days=1)
    store.save(todo)
    store.commit(f"defer: {todo.title}")
    return [Outbound(f"Moved to tomorrow: {esc(todo.title)}", kind="reply")]


def _get(store, path: str):
    try:
        return store.get_todo(path)
    except KeyError:
        return None
