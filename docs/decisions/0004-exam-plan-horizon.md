# 0004 — Exam plans have a 21-day horizon and a morning job
Date: 2026-09-22
Decided by: orchestrator
Status: accepted

## Context
A new exam got a day-by-day plan and one study todo per day at add time. A syllabus adds a whole semester of exams at once; that would have dropped a hundred study todos on the list.

## Options
- A — plan every exam at add time (the old behaviour): floods the list.
- B — never plan automatically; a "plan" tool on demand: the owner has to remember to ask.
- C (chosen) — plan at add time only inside 21 days; a scheduler step at 08:00 plans any exam that has entered the window and has no study todos yet.

## Decision
C, in `bot/study/plans.py`, shared by the router and the engine. "Planned" is inferred from study todos for the course due between today and the exam, so no extra bookkeeping.

## Consequences
One proactive message per exam entering the window. Two exams of one course inside the window at once: the second is planned after the first passes.
