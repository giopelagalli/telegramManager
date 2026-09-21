"""/notes: what he has kept, newest first by day. Tap one to remove it. Adding is chat:
"keep this", "note: ...", "remember ...". Nightly notes land here too."""
from __future__ import annotations

from datetime import datetime, timedelta

from bot.knowledge.views import esc, fmt_day
from bot.scheduler.outbound import Outbound

DAYS = 14
LIMIT = 40


def _notes(store, now: datetime) -> list:
    cutoff = now.date() - timedelta(days=DAYS)
    items = [m for m in store.memories() if m.day >= cutoff and m.active(now.date())]
    return sorted(items, key=lambda m: (m.day, m.path), reverse=True)[:LIMIT]


def list_view(store, now: datetime, message_id: int | None = None, toast: str | None = None) -> Outbound:
    notes = _notes(store, now)
    lines = ["<b>Notes</b>"]
    buttons = []
    last_day = None
    for i, m in enumerate(notes):
        if m.day != last_day:
            lines.append(f"\n<b>{fmt_day(m.day)}</b>")
            last_day = m.day
        lines.append(f"• {esc(m.text)}" + (" <i>(on your mind)</i>" if m.kind == "state" else ""))
        buttons.append((m.text[:40], f"note:e:{i}"))
    if not notes:
        lines.append("Nothing kept in the last two weeks. Say \"keep this\" or \"note: …\".")
    return Outbound("\n".join(lines), buttons=buttons, kind="edit" if message_id else "reply", edit_message_id=message_id, toast=toast)


def item_view(store, now: datetime, index: int, message_id: int | None = None) -> Outbound:
    notes = _notes(store, now)
    if index < 0 or index >= len(notes):
        return list_view(store, now, message_id)
    m = notes[index]
    text = f"<b>{fmt_day(m.day)}</b>\n{esc(m.text)}"
    buttons = [("🗑 Remove", f"note:remove:{index}"), ("◀ Back", "note:list")]
    return Outbound(text, buttons=buttons, kind="edit" if message_id else "reply", edit_message_id=message_id)


def handle(arg: str, store, state, now: datetime, message_id: int | None) -> list[Outbound]:
    what, _, idx = arg.partition(":")
    if what == "e" and idx.isdigit():
        return [item_view(store, now, int(idx), message_id)]
    if what == "remove" and idx.isdigit():
        notes = _notes(store, now)
        if int(idx) < len(notes):
            m = notes[int(idx)]
            store.delete(m.path)
            store.commit(f"note removed: {m.text[:40]}")
            return [list_view(store, now, message_id, toast="Removed.")]
    return [list_view(store, now, message_id)]
