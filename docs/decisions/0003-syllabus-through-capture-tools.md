# 0003 — A syllabus builds its course through the normal capture tools
Date: 2026-09-22
Decided by: orchestrator
Status: accepted

## Context
The owner wants JD to learn a class from its syllabus PDF: meetings, exams, dated work, grading, office hours.

## Options
- A — a dedicated strict-JSON `parse_syllabus` and code that synthesises tool calls from it: two schemas to keep in step, a second copy of the schedule-dump rules.
- B (chosen) — describe the file first; when it is a syllabus, keep it as a `syllabus` source, put the summary on the course page, then run the ordinary capture loop with the syllabus text and a hint that names the course slug and what to add.

## Decision
B. The model already knows how to turn a pasted schedule into `add_event` with `repeat_days`, exams and todos; validation, geocoding, week rendering and undo come for free.

## Consequences
Quality depends on the capture model reading a long document in one turn (40k chars cap; fine on the Spark and on DeepSeek). A scanned syllabus with no text layer is still unreadable; a photo of it goes through OCR.
