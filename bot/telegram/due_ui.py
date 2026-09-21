"""/due: assignments by due date, with buttons. Tap one for Done / Change / Remove; Add asks for
one line ("Spanish homework on Connect, Friday") and the model files it."""
from __future__ import annotations

from datetime import datetime

from bot.knowledge.views import esc, fmt_day
from bot.scheduler.outbound import Outbound


def _due(store) -> list:
    todos = [t for t in store.todos() if t.status == "open" and t.due is not None]
    return sorted(todos, key=lambda t: (t.due, t.priority, t.path))


def list_view(store, now: datetime, message_id: int | None = None, toast: str | None = None) -> Outbound:
    items = _due(store)
    today = now.date()
    lines = ["<b>Due</b>"]
    buttons = []
    for i, t in enumerate(items):
        when = "today" if t.due == today else ("overdue" if t.due < today else fmt_day(t.due))
        lines.append(f"{when} — {esc(t.title)}")
        buttons.append((f"{when} · {t.title}"[:40], f"due:e:{i}"))
    if not items:
        lines.append("Nothing with a date on it.")
    buttons.append(("➕ Add", "due:add"))
    return Outbound("\n".join(lines), buttons=buttons, kind="edit" if message_id else "reply", edit_message_id=message_id, toast=toast)


def item_view(store, now: datetime, index: int, message_id: int | None = None) -> Outbound:
    items = _due(store)
    if index < 0 or index >= len(items):
        return list_view(store, now, message_id)
    t = items[index]
    lines = [f"<b>{esc(t.title)}</b>", f"due {fmt_day(t.due)}, P{t.priority}"]
    if t.course:
        lines.append(esc(t.course))
    buttons = [("✅ Done", f"due:done:{index}"), ("✏️ Change", f"due:change:{index}"), ("🗑 Remove", f"due:remove:{index}"), ("◀ Back", "due:list")]
    return Outbound("\n".join(lines), buttons=buttons, kind="edit" if message_id else "reply", edit_message_id=message_id)


def handle(arg: str, store, state, now: datetime, message_id: int | None) -> list[Outbound]:
    what, _, rest = arg.partition(":")
    if what == "list":
        return [list_view(store, now, message_id)]
    if what == "e" and rest.isdigit():
        return [item_view(store, now, int(rest), message_id)]
    if what == "add":
        state.pending_schedule = {"ui": "due", "mode": "add"}
        return [Outbound("What's due, and when? One line, like \"Spanish homework on Connect, Friday\".", kind="reply")]
    if what in ("done", "change", "remove") and rest.isdigit():
        items = _due(store)
        index = int(rest)
        if index >= len(items):
            return [list_view(store, now, message_id)]
        t = items[index]
        if what == "done":
            t.status = "done"
            t.done_at = now
            store.save(t)
            store.commit(f"todo done: {t.title}")
            return [list_view(store, now, message_id, toast=f"Done: {t.title}")]
        if what == "remove":
            store.delete(t.path)
            store.commit(f"todo removed: {t.title}")
            return [list_view(store, now, message_id, toast=f"Removed {t.title}")]
        state.pending_schedule = {"ui": "due", "mode": "change", "path": t.path, "title": t.title}
        return [Outbound(f"{esc(t.title)}: what changes? \"due Monday\", \"P1\", \"rename to …\".", kind="reply")]
    return [list_view(store, now, message_id)]


def hint(pending: dict, text: str) -> str:
    if pending.get("mode") == "add":
        return f"[Adding an assignment: make one add_todo with a `due` date (homework gets verify photo)] {text}"
    return f"[Editing the todo {pending['path']} ({pending.get('title', '')}): use update_todo on it with only the fields that change] {text}"
