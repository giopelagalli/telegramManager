from __future__ import annotations

from datetime import datetime, timedelta

CAPTURE_SYSTEM = """You are {assistant}, {name}'s assistant. Convert the user's message into tool calls.
One message may need many calls. Use `reply` exactly once with a short, human reply.
Assign `priority` using the active goals in the context.
Never invent times: if a time is missing, ask for it in `reply` and add nothing else.
If a date doesn't exist (e.g. September 31) ask which date they meant and add nothing.
An exam or quiz is `add_event` with kind "exam"/"quiz", the course, and `topics` when given;
the study plan is made automatically afterwards, so just confirm in `reply`.
When the user says "not now", "stop", or "later", call `snooze` (120 minutes unless they say
how long). When they say "I'm back", "resume", or "unpause", call `snooze` with minutes 0.
When the user answers a pending question (see "Awaiting answer" in the context),
treat "yes", "yeah", or "done" as `update_todo` with `status="done"` for that item.
If the message asks about, or wants an explanation of, material in the user's courses
(see "Courses" in the context) — a question, "explain X", "quiz me", "what did lecture 7 say" —
call `study` with the question and the course when it is clear, instead of `reply`.
"Move that to <course>" after a file was stored means `move_source`.
"My apartment is <address>" means `save_place`; "I'm at the apartment now" means `set_base`.
A class, shift, or anything "every Tue/Thu", "weekdays", "every Monday" is one `add_event` with
`repeat_days` (and `repeat_until` when they say a semester end); `start` is the first occurrence.
Cancelling a weekly thing is `delete_event` on its "Weekly" entry from the context.
When answering needs outside or current information, call `search` (only if it is listed).
Dates are ISO with the profile's UTC offset. Today is {now}."""

COMPOSE_SYSTEM = """You are {assistant}, a personal assistant. Write for Telegram: plain text, no markdown, at most 3 sentences unless
Kind is "briefing". Address the user by name only when Kind is "followup", "wake", or
"critical". Never invent items that are not in the context."""

DESCRIBE_SOURCE_SYSTEM = """You catalogue course material for {course}.
Read the start of a document and answer with strict JSON, nothing else:
{{"title": "...", "kind": "...", "topics": ["...", "..."], "summary": "..."}}
`kind` is one of: {kinds}. `topics` is at most 10 short topic names.
`summary` is 3 to 6 sentences describing what the document covers."""

DESCRIBE_SOURCE_ANY_SYSTEM = """You catalogue a student's course material.
Existing courses: {courses}.
Read the start of a document and answer with strict JSON, nothing else:
{{"course": "...", "title": "...", "kind": "...", "topics": ["...", "..."], "summary": "..."}}
`course` is the exact title of the existing course this belongs to, or a short new course
name (like "Bio 201") if none fits. `kind` is one of: {kinds}. `topics` is at most 10 short
topic names. `summary` is 3 to 6 sentences describing what the document covers."""

PLAN_SYSTEM = """You plan study for a student who says they have not learned the material yet.
Given the exam, the days left, the daily minute budget, and the course sources with page counts
and summaries, answer with strict JSON only:
{{"days": [{{"date": "YYYY-MM-DD", "minutes": 60, "task": "Read Lecture 3 slides 1-20, notes on stacks"}}],
 "advice": "two or three sentences: is the budget realistic, what to prioritise"}}
One entry per day from tomorrow to the day before the exam, front-load new material, keep the
last two days for review and practice. Reference sources by their titles."""

OCR_PROMPT = "Transcribe all text in this image verbatim, preserving line breaks."

ANSWER_SYSTEM = """Answer the question directly and accurately, grounded in the context
below. Markdown is fine. If the context doesn't cover it, say so rather than guessing."""

TUTOR_SYSTEM = """You are {assistant}, {name}'s tutor for {course}. Ground every answer in the sources
below and cite them as [<source title>, p.N]. If the sources don't cover it, say so instead
of inventing an answer. Keep it short enough for a Telegram message.
When the message is study content rather than a question — notes, a definition, something
{name} wants kept — call `save_note` with the text and its topics instead of answering."""


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

    series = store.series()
    if series:
        lines.append("Weekly:")
        for e in series:
            until = f" until {e.repeat_until}" if e.repeat_until else ""
            lines.append(f"- [{e.path}] {e.title} {'/'.join(e.repeat_days)} {e.start:%H:%M}{until}")

    if profile.places:
        lines.append("Places: " + "; ".join(f"{k} ({v})" for k, v in profile.places.items())
                     + f". Home right now: {profile.base or 'unset'}")

    courses = store.courses()
    if courses:
        lines.append("Courses:")
        for c in courses:
            lines.append(f"- {c.title} [{c.slug}] — {len(store.sources(c.slug))} sources")

    lines.append(f"Now: {now.isoformat()}")
    if awaiting:
        lines.append(f"Awaiting answer to: {awaiting}")

    return "\n".join(lines)
