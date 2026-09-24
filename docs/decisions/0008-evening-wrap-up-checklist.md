# 0008 — The evening wrap-up is a checklist
Date: 2026-09-24
Decided by: owner
Status: accepted

## Context
The wrap-up after 0006 read "Nothing marked done today. Slipped: … Tomorrow: CSCI 2720 at 8:15am —
get ready 7:35am, leave by 7:50am." The owner: it should literally just be the things to work on,
what to study, what's due, the todos, like a checklist for today with Todo and Done.

## Options
- A — keep the 0006 body (done, slipped, unconfirmed, starving goal, tomorrow line) and add a
  todo list on top: longer, and the slipped and goal lines are the nagging the owner is objecting to.
- B (chosen) — the body is two sections, Todo and Done, and nothing else. Todo is everything due by
  tomorrow whatever its priority, then the top of the ranked list to make five, each with a ✅
  button like the morning. Done is what was marked done today, "(unconfirmed)" where a photo or
  location is still owed. Empty sections are left out; an empty list says "Nothing on your list."

## Decision
B, on the owner's word. The tomorrow line goes (leave-by reminders cover getting to class, 0007),
the starving-goal line goes (goals stay in the morning briefing), the "→ tomorrow" defer buttons go
("push it to tomorrow" in the DM does the same). 0006 still holds: no composed prose.

## Consequences
The evening reads like a list, not a report. The Todo selection is the wrap-up's own, not
`rank_todos` alone, because the ranking puts priority before due date for future items; if that
ranking is fixed, this selection can collapse back to `top`.
