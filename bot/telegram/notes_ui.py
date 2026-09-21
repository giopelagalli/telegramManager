"""/notes: a scratchpad for the week. Seven day buttons, today on top; tap a day for its notes,
tap a note to remove it. Older notes stay in the vault and come back when asked about."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from bot.knowledge.views import esc, fmt_day
from bot.scheduler.outbound import Outbound

DAYS = 7


def _on(store, d: date) -> list:
    return sorted((m for m in store.memories() if m.day == d), key=lambda m: m.path)


def days_view(store, now: datetime, message_id: int | None = None, toast: str | None = None) -> Outbound:
    today = now.date()
    buttons = []
    for i in range(DAYS):
        d = today - timedelta(days=i)
        n = len(_on(store, d))
        label = "Today" if i == 0 else ("Yesterday" if i == 1 else fmt_day(d))
        buttons.append((f"{label} · {n}" if n else label, f"note:day:{d.isoformat()}"))
    return Outbound("<b>Notes</b>", buttons=buttons, kind="edit" if message_id else "reply", edit_message_id=message_id, toast=toast)


def day_view(store, now: datetime, d: date, message_id: int | None = None, toast: str | None = None) -> Outbound:
    notes = _on(store, d)
    label = "Today" if d == now.date() else fmt_day(d)
    lines = [f"<b>{label}</b>"] + ([f"• {esc(m.text)}" + (" <i>(on your mind)</i>" if m.kind == "state" else "") for m in notes] or ["• Nothing kept."])
    buttons = [(m.text[:40], f"note:e:{d.isoformat()}:{i}") for i, m in enumerate(notes)] + [("◀ Days", "note:days")]
    return Outbound("\n".join(lines), buttons=buttons, kind="edit" if message_id else "reply", edit_message_id=message_id, toast=toast)


def item_view(store, now: datetime, d: date, index: int, message_id: int | None = None) -> Outbound:
    notes = _on(store, d)
    if index < 0 or index >= len(notes):
        return day_view(store, now, d, message_id)
    m = notes[index]
    buttons = [("🗑 Remove", f"note:remove:{d.isoformat()}:{index}"), ("◀ Back", f"note:day:{d.isoformat()}")]
    return Outbound(f"<b>{fmt_day(d)}</b>\n{esc(m.text)}", buttons=buttons, kind="edit" if message_id else "reply", edit_message_id=message_id)


def _date(s: str) -> date | None:
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def handle(arg: str, store, state, now: datetime, message_id: int | None) -> list[Outbound]:
    parts = arg.split(":")
    what = parts[0]
    d = _date(parts[1]) if len(parts) > 1 else None
    if what == "day" and d:
        return [day_view(store, now, d, message_id)]
    if what == "e" and d and len(parts) > 2 and parts[2].isdigit():
        return [item_view(store, now, d, int(parts[2]), message_id)]
    if what == "remove" and d and len(parts) > 2 and parts[2].isdigit():
        notes = _on(store, d)
        if int(parts[2]) < len(notes):
            m = notes[int(parts[2])]
            store.delete(m.path)
            store.commit(f"note removed: {m.text[:40]}")
            return [day_view(store, now, d, message_id, toast="Removed.")]
        return [day_view(store, now, d, message_id)]
    return [days_view(store, now, message_id)]
