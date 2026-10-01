# 0009 — What JD says about projects, and when
Date: 2026-10-01
Decided by: senior-coder (within the plan's FR-C3 and 0007)
Status: accepted

## Context
The plan (AgentHub `docs/plan-jd-web-agenthub.md`, phase 1) wants one message per turn JD started,
a roll-up of the rest in the briefings, and an immediate message when a project blocks. 0007 says
the owner decides volume.

## Options
- A — every landed turn as its own message: auto-run makes that six a day per project.
- B (chosen) — split by who asked.

## Decision
- A turn JD started (`requestedBy` = its label) → exactly one message, deduped by session id; not
  budgeted, because he asked for it. Watches expire after 3 hours.
- Turns JD didn't start since the previous briefing → "Projects: rosenroot 2 turns,
  probability-engine blocked on …" in the morning and evening briefings; nothing when quiet.
- A project newly `blocked`, or a turn ending in `error` → one message each, spending the hourly
  proactive budget, held (not dropped) while over budget or snoozed. A project's first sight is
  recorded silently so an install doesn't alert on old state. An errored turn JD already reported
  is not alerted again.
- No turn number in the report: `/turns` returns only the last 20, so a count would be wrong.

## Consequences
The briefing roll-up costs one `/turns` call per non-done project at briefing time. "Node offline"
alerts from the plan are not built: no allowed route reports node health beyond `/api/state`'s
nodes, and that is a separate decision.
