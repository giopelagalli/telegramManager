# 0006 — The evening wrap-up is deterministic
Date: 2026-09-23
Decided by: orchestrator (owner complaint)
Status: accepted

## Context
The wrap-up ended with model-written lines that repeated tomorrow's plan ("two tomorrows") and brought up calories and water from the context.

## Options
- A — keep the composed lines and instruct the composer not to repeat tomorrow or mention food: a prompt promise at temperature 0.6.
- B (chosen) — drop the composed prose; the wrap-up is done today, slipped, unconfirmed, a starving goal, one "Tomorrow" line with get-ready and leave-by.

## Decision
B. The morning briefing was already deterministic; nightly notes are still written quietly.

## Consequences
One fewer model call a day. If a human line is wanted later it goes in as a separate, short, rule-bound compose with the deterministic body in its context.
