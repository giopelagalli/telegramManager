# 0005 — Files sent in the DM are read; only course material is filed
Date: 2026-09-22
Decided by: orchestrator
Status: accepted

## Context
The DM ignored files; only course topics ingested them, and only .txt/.md counted as text. The owner wants JD to read any file (code, md, pdf) and talk about it.

## Options
- A — file everything under an inferred course, as course topics do: a project brief ends up under "General".
- B (chosen) — code by extension goes straight to conversation; everything else is described first: syllabus builds the course, study material is filed, `code`/`other` is discussed with the caption as the question.
- Keeping the file in the thread for follow-ups: the thread caps entries at 2k chars, so instead the last file lives in runtime state and stays in the model's view for three hours.

## Decision
B plus the three-hour view. The transcript keeps only a "[sent name]" line.

## Consequences
Two new source kinds (`syllabus`, `code`). Unknown extensions are sniffed as UTF-8 text. Everything the model reads is capped at 40k chars.
