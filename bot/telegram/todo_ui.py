"""One button-driven list editor for todos, under three doors: /todo (personal, ranked), /due
(everything with a date, soonest first) and a course's assignments (/courses). Tap an item for
Done / Change / Remove; Add asks for one line and the model files it."""
from __future__ import annotations

from datetime import datetime

from bot.knowledge.ranking import rank_todos
from bot.knowledge.views import esc, fmt_clock, fmt_day, fmt_due
from bot.scheduler.outbound import Outbound

TITLES = {"personal": "To do", "due": "Due"}


def items(store, scope: str, now: datetime) -> list:
    todos = [t for t in store.todos() if t.status == "open"]
    if scope == "due":
        return sorted((t for t in todos if t.due is not None), key=lambda t: (t.due, t.priority, t.path))
    if scope.startswith("course:"):
        slug = scope.split(":", 1)[1]
        return rank_todos((t for t in todos if t.course == slug), now.date())
    return rank_todos((t for t in todos if not t.course), now.date())


def _when(t, today) -> str:
    if t.due is None:
        return ""
    day = "today" if t.due == today else ("overdue" if t.due < today else fmt_day(t.due))
    return f"{day} {fmt_clock(t.due_time)}" if t.due_time else day


def list_view(store, now: datetime, scope: str = "personal", message_id: int | None = None,
              toast: str | None = None, back: tuple[str, str] | None = None) -> Outbound:
    todos = items(store, scope, now)
    today = now.date()
    title = TITLES.get(scope) or scope.split(":", 1)[1]
    lines = [f"<b>{esc(title)}</b>"]
    buttons = []
    for i, t in enumerate(todos):
        when = _when(t, today)
        lines.append(f"{when + ' — ' if when else ''}{esc(t.title)}" + ("" if scope == "due" else f" (P{t.priority})"))
        buttons.append((f"{when + ' · ' if when else ''}{t.title}"[:40], f"todo:{scope}:e:{i}"))
    if not todos:
        lines.append("Nothing here.")
    buttons.append(("➕ Add", f"todo:{scope}:add"))
    if back:
        buttons.append(back)
    return Outbound("\n".join(lines), buttons=buttons, kind="edit" if message_id else "reply", edit_message_id=message_id, toast=toast)


def item_view(store, now: datetime, scope: str, index: int, message_id: int | None = None) -> Outbound:
    todos = items(store, scope, now)
    if index < 0 or index >= len(todos):
        return list_view(store, now, scope, message_id)
    t = todos[index]
    lines = [f"<b>{esc(t.title)}</b>", f"P{t.priority}" + (f", due {fmt_due(t)}" if t.due else "")]
    if t.course:
        lines.append(esc(t.course))
    buttons = [("✅ Done", f"todo:{scope}:done:{index}"), ("✏️ Change", f"todo:{scope}:change:{index}"),
               ("🗑 Remove", f"todo:{scope}:remove:{index}"), ("◀ Back", f"todo:{scope}:list")]
    return Outbound("\n".join(lines), buttons=buttons, kind="edit" if message_id else "reply", edit_message_id=message_id)


def _back_for(scope: str) -> tuple[str, str] | None:
    return ("◀ Course", f"course:view:{scope.split(':', 1)[1]}") if scope.startswith("course:") else None


def handle(arg: str, store, state, now: datetime, message_id: int | None) -> list[Outbound]:
    # arg: "<scope>:<what>[:<index>]" where scope is personal | due | course:<slug>
    parts = arg.split(":")
    if parts[0] == "course" and len(parts) >= 3:
        scope, rest = f"course:{parts[1]}", parts[2:]
    else:
        scope, rest = parts[0], parts[1:]
    what = rest[0] if rest else "list"
    idx = rest[1] if len(rest) > 1 else ""
    back = _back_for(scope)
    if what == "list":
        return [list_view(store, now, scope, message_id, back=back)]
    if what == "e" and idx.isdigit():
        return [item_view(store, now, scope, int(idx), message_id)]
    if what == "add":
        state.pending_schedule = {"ui": "todo", "mode": "add", "scope": scope}
        ask = {"due": "What's due, and when? One line, like \"Spanish homework on Connect, Friday\".",
               "personal": "What needs doing? One line; add a day if it has one."}.get(
            scope, f"What's the assignment, and when is it due? One line.")
        return [Outbound(ask, kind="reply")]
    if what in ("done", "change", "remove") and idx.isdigit():
        todos = items(store, scope, now)
        index = int(idx)
        if index >= len(todos):
            return [list_view(store, now, scope, message_id, back=back)]
        t = todos[index]
        if what == "done":
            t.status = "done"
            t.done_at = now
            store.save(t)
            store.commit(f"todo done: {t.title}")
            return [list_view(store, now, scope, message_id, toast=f"Done: {t.title}", back=back)]
        if what == "remove":
            store.delete(t.path)
            store.commit(f"todo removed: {t.title}")
            return [list_view(store, now, scope, message_id, toast=f"Removed {t.title}", back=back)]
        state.pending_schedule = {"ui": "todo", "mode": "change", "scope": scope, "path": t.path, "title": t.title}
        return [Outbound(f"{esc(t.title)}: what changes? \"due Monday\", \"P1\", \"rename to …\".", kind="reply")]
    return [list_view(store, now, scope, message_id, back=back)]


def hint(pending: dict, text: str) -> str:
    scope = pending.get("scope", "personal")
    course = f' with course "{scope.split(":", 1)[1]}"' if scope.startswith("course:") else ""
    if pending.get("mode") == "add":
        if scope == "due":
            return f"[Adding an assignment: one add_todo with a `due` date (homework gets verify photo)] {text}"
        if course:
            return f"[Adding an assignment for a course: one add_todo{course}, with `due` if they gave a date (homework gets verify photo)] {text}"
        return f"[Adding a personal todo: one add_todo, no course, `due` only if they gave a day] {text}"
    return f"[Editing the todo {pending['path']} ({pending.get('title', '')}): use update_todo on it with only the fields that change] {text}"
