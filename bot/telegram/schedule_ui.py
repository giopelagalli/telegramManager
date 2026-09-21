"""/schedule: the weekly template, day by day, with buttons. Buttons navigate, pick and remove;
the details of a class (time, room, travel) are typed in one line and parsed by the model."""
from __future__ import annotations

from datetime import datetime

from bot.knowledge.store import _WEEKDAYS
from bot.knowledge.views import esc, fmt_time
from bot.scheduler.outbound import Outbound

DAY_NAMES = {"MO": "Monday", "TU": "Tuesday", "WE": "Wednesday", "TH": "Thursday", "FR": "Friday", "SA": "Saturday", "SU": "Sunday"}


def _entries(store, day: str | None = None) -> list:
    items = store.series()
    if day is not None:
        items = [e for e in items if day in e.repeat_days]
    return sorted(items, key=lambda e: (e.start.hour, e.start.minute, e.title))


def days_view(store, message_id: int | None = None) -> Outbound:
    buttons = []
    for code in _WEEKDAYS:
        n = len(_entries(store, code))
        buttons.append((f"{DAY_NAMES[code][:3]} · {n}" if n else DAY_NAMES[code][:3], f"sched:day:{code}"))
    return Outbound("Your week. Pick a day.", buttons=buttons, kind="edit" if message_id else "reply", edit_message_id=message_id)


def day_view(store, code: str, message_id: int | None = None, toast: str | None = None) -> Outbound:
    entries = _entries(store, code)
    lines = [f"<b>{DAY_NAMES[code]}</b>"]
    buttons = []
    everything = store.series()
    for e in entries:
        span = fmt_time(e.start) + (f"–{fmt_time(e.end)}" if e.end else "")
        where = f" · {esc(e.location)}" if e.location else ""
        lines.append(f"{span} {esc(e.title)}{where}")
        buttons.append((f"{fmt_time(e.start)} {e.title}"[:40], f"sched:e:{everything.index(e)}"))
    if not entries:
        lines.append("Nothing yet.")
    buttons.append((f"➕ Add to {DAY_NAMES[code]}", f"sched:add:{code}"))
    buttons.append(("◀ Days", "sched:days"))
    return Outbound("\n".join(lines), buttons=buttons, kind="edit" if message_id else "reply", edit_message_id=message_id, toast=toast)


def entry_view(store, index: int, message_id: int | None = None) -> Outbound:
    everything = store.series()
    if index < 0 or index >= len(everything):
        return days_view(store, message_id)
    e = everything[index]
    days = "/".join(DAY_NAMES[d][:3] for d in _WEEKDAYS if d in e.repeat_days)
    span = fmt_time(e.start) + (f"–{fmt_time(e.end)}" if e.end else "")
    lines = [f"<b>{esc(e.title)}</b>", f"{days} {span}"]
    if e.location:
        lines.append(esc(e.location))
    lines.append(f"{e.travel_minutes} min to get there" if e.travel_minutes else "travel time unknown")
    if e.repeat_until:
        lines.append(f"until {e.repeat_until:%b %-d}")
    back = next((d for d in _WEEKDAYS if d in e.repeat_days), "MO")
    buttons = [("✏️ Change", f"sched:change:{index}"), ("🗑 Remove", f"sched:remove:{index}"), ("◀ Back", f"sched:day:{back}")]
    return Outbound("\n".join(lines), buttons=buttons, kind="edit" if message_id else "reply", edit_message_id=message_id)


def handle(arg: str, store, state, now: datetime, message_id: int | None) -> list[Outbound]:
    what, _, rest = arg.partition(":")
    if what == "days":
        return [days_view(store, message_id)]
    if what == "day" and rest in DAY_NAMES:
        return [day_view(store, rest, message_id)]
    if what == "e" and rest.isdigit():
        return [entry_view(store, int(rest), message_id)]
    if what == "add" and rest in DAY_NAMES:
        state.pending_schedule = {"ui": "schedule", "mode": "add", "day": rest}
        return [Outbound(
            f"{DAY_NAMES[rest]}: class, time, room, how long to get there. One line, like "
            f"\"CSCI 1730 2:55–4:15 at Conner Hall 104, 20 min\".", kind="reply",
        )]
    if what in ("change", "remove") and rest.isdigit():
        everything = store.series()
        index = int(rest)
        if index >= len(everything):
            return [days_view(store, message_id)]
        entry = everything[index]
        if what == "remove":
            back = next((d for d in _WEEKDAYS if d in entry.repeat_days), "MO")
            n = store.delete_series(entry.path, now)
            store.commit(f"schedule: remove weekly {entry.title}")
            return [day_view(store, back, message_id, toast=f"Removed {entry.title} ({n} upcoming cleared)")]
        state.pending_schedule = {"ui": "schedule", "mode": "change", "path": entry.path, "title": entry.title}
        return [Outbound(
            f"{esc(entry.title)}: what changes? \"moves to 2pm\", \"ends 4:15\", \"room is Boyd 201\", "
            f"\"25 min to get there\", \"Tue/Thu instead\".", kind="reply",
        )]
    return [days_view(store, message_id)]


def hint(pending: dict, text: str) -> str:
    """Wrap the typed details so the model knows which weekly entry they are for."""
    if pending.get("mode") == "add":
        day = pending["day"]
        return (f"[Weekly schedule, adding to {DAY_NAMES[day]}: make one add_event with repeat_days [\"{day}\"], "
                f"end, location with campus and city, travel_minutes] {text}")
    return (f"[Weekly schedule, editing the entry {pending['path']} ({pending.get('title', '')}): "
            f"use update_event on that file with only the fields that change] {text}")
