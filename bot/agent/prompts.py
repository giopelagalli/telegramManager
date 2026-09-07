from __future__ import annotations

from datetime import datetime, timedelta

CAPTURE_SYSTEM = """You are {name}'s assistant. Convert the user's message into tool calls.
One message may need many calls. Use `reply` exactly once with a short, human reply.
Assign `priority` using the active goals in the context.
Never invent times: if a time is missing, ask for it in `reply` and add nothing else.
When the user says "not now", "stop", or "later", call `snooze`.
When the user answers a pending question (see "Awaiting answer" in the context),
treat "yes", "yeah", or "done" as `update_todo` with `status="done"` for that item.
Dates are ISO with the profile's UTC offset. Today is {now}."""

COMPOSE_SYSTEM = """Write for Telegram: plain text, no markdown, at most 3 sentences unless
Kind is "briefing". Address the user by name only when Kind is "followup", "wake", or
"critical". Never invent items that are not in the context."""


def build_context(store, now: datetime, awaiting: str | None = None) -> str:
    profile = store.profile()
    lines = [f"Name: {profile.name}", f"Timezone: {profile.timezone}"]
    if profile.body.strip():
        lines.append(profile.body.strip())

    today = now.date()
    tomorrow = today + timedelta(days=1)
    events = [e for e in store.events() if e.start.date() in (today, tomorrow)]
    lines.append("Today and tomorrow:")
    if events:
        for e in events:
            lines.append(f"- {e.start:%a %H:%M} {e.title}")
    else:
        lines.append("- none")

    todos = [t for t in store.todos() if t.status == "open"]
    lines.append("Open todos:")
    if todos:
        for t in todos:
            due = t.due.isoformat() if t.due else "none"
            lines.append(f"- [{t.path}] {t.title} P{t.priority} due {due}")
    else:
        lines.append("- none")

    goals = [g for g in store.goals() if g.status == "active"]
    lines.append("Active goals:")
    if goals:
        for g in goals:
            lines.append(f"- {g.title} ({g.period})")
    else:
        lines.append("- none")

    lines.append(f"Now: {now.isoformat()}")
    if awaiting:
        lines.append(f"Awaiting answer to: {awaiting}")

    return "\n".join(lines)
