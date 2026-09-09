# Study System — Design Spec

Date: 2026-09-09. Extends the assistant in `2026-09-03-telegram-assistant-design.md`; everything there still holds.

## 1. Purpose

Make the same bot and the same OKF bundle work for school: ingest course material, tutor from it, keep assignments and exams on track, and keep learning fresh with a daily quiz session (active recall) and a daily digest (passive re-reading). Guiding rule unchanged: it must not be annoying.

## 2. Channels

A private Telegram group with forum topics, the bot added as an admin (so it sees every message). The owner's DM stays the **life** channel: briefings, check-ins, critical mode, everything already built.

| Topic kind | Bound with | What happens there |
|---|---|---|
| `course:<slug>` | `/bind course CS101 Intro to CS` | drop sources, ask questions, get tutored on that course |
| `assignments` | `/bind assignments` | HW, projects, labs across courses |
| `exams` | `/bind exams` | quizzes/exams, study plans, readiness |
| `review` | `/bind review` | daily quiz session and daily digest |

`/bind` run inside a topic stores `chat_id` + `message_thread_id` → kind in `knowledge/channels.md` (`type: channels`, a YAML map keyed `"<chat_id>:<thread_id>"`). `/unbind` removes it. Messages in an unbound topic get one reply explaining `/bind`, then are ignored. Only the owner's user id is ever answered; other group members are ignored. `/bind course` creates the course file if missing.

Outbound messages carry `channel` (`life`, `review`, `assignments`, `exams`, `course:<slug>`); the sender resolves it to `(chat_id, thread_id)` via the map, falling back to the DM when a kind is unbound (with a one-time warning in the log).

## 3. OKF additions

```
knowledge/
├── channels.md                      type: channels
├── courses/<slug>.md                type: course  — title, term, topics: [..]
├── sources/<slug>/<date>-<name>.md  type: source  — course, kind, pages, topics, summary, extracted text with page markers
├── cards/<slug>/<id>.md             type: card    — question, answer, topic, source, SM-2 fields, history   (phase 3)
└── digests/<date>.md                type: digest  — what was posted                                          (phase 3)
```

- **Todo** gains optional `course` and `kind` (`hw|project|lab`). Assignments are todos; they rank in `/todo` as usual and get `/assignments`.
- **Event** gains optional `course`, `kind: exam`, `topics: [..]`.
- **Source** kinds: `slides`, `chapter`, `paper`, `notes`, `photo`, `hw-spec`, `other`. Body is the extracted text; PDFs/slides carry `## p.N` / `## slide N` markers so citations can name pages. Sources over 200k characters are split into `part-1`, `part-2` files sharing a `group` id.
- Per-source `summary` (3–6 sentences) is written by the model at ingest. Per-topic summaries live in the course file under `## Topics` and are refreshed at ingest (phase 3 uses them for the digest).

## 4. Ingest (phase 1)

Trigger: a document, photo, or text/voice note posted in a course topic.

- PDF → `pypdf` per page. PPTX → `python-pptx` per slide, with speaker notes. Photo → vision model OCR (`agent.ocr`), stored as `kind: photo`; if no vision model, stored with the caption only and marked `ocr: unavailable`. Text or voice in a course topic is passed to the model with a `save_note` tool: it either saves a note source (when the message is content, not a question) or answers (tutoring, §5).
- After extraction the model produces `{title, kind, topics, summary}` as JSON (validated; retried once; on failure stored with the filename as title and `topics: []`). The reply names what was stored: title, kind, page/slide count, topics, and a "wrong course? /move" hint.
- Everything is one commit (`ingest: <title>`); `/undo` works.
- Syllabus/assignment-sheet detection → phase 2.

## 5. Tutor (phase 1)

Free text in a course topic (that is a question) → model answer grounded on that course's sources. Selection: sources whose `topics` or title overlap the question's words, then most recent, until `tutor_context_chars` (profile, default 150 000) is reached. Sources are inlined with their titles and page markers so the model can cite `[Lecture 7, slide 12]`. Tools available: `save_note` only. `/sources` lists the course's sources; `/summary <n>` prints one summary. Answers are text; voice reply follows the existing voice rules.

## 6. Assignments and exams (phase 2)

- `/assignments`: open assignment todos grouped by due date across courses; in a course topic, filtered to it. Captured from chat or proposed from a syllabus-like source with an "Add all" button; nothing is added silently.
- Project/lab start nudge: a `Start: <title>` todo due `assignment_lead_days` (default 3) before the due date, created with the assignment.
- Exam creation (chat: "quiz Thursday on ch 4–5") → event with `kind: exam`, `topics`; the model maps topics → sources/pages and posts a reading plan in **exams**; review todos are planted at `exam_review_offsets_days` (default `[7, 3, 1]`) before the date; cards are generated for the topics (phase 3).
- `/exams`: each upcoming exam with days left, planned reviews done, and (phase 3) cards mastered %.

## 7. Review: recall session (phase 3)

- Cards: generated only for exam topics or on `/cards <topic>`, `cards_per_topic` (default 8). Each card: `question`, `answer`, `topic`, `source` link, SM-2 fields (`ease` 2.5, `interval` days, `due`, `reps`, `lapses`), `history` of `(date, grade)`.
- One session a day in **review** at `review_time` (default 18:00). Picks due cards up to `review_daily_cap` (default 8; raised to `review_exam_cap`, default 15, for a course with an exam within `exam_focus_days`, default 7). Asks one at a time; the answer (text or voice) is graded 0–5 by the model with a one-line correction; SM-2 updates the card. Session ends with "n right, m to see again". `/review` starts one on demand; `/review later` snoozes today's. A skipped session leaves cards due; nothing accumulates as a backlog beyond the cap.
- Respects pause, quiet hours, the message budget; session messages are silent notifications.

## 8. Review: digest (phase 3)

- Daily at `digest_time` (default 08:30; `digest_cadence: daily|weekly`), posted in **review** as text + voice note, ~300 words: 2–3 stories, each a headline and a paragraph, written from topic summaries.
- Selection: the topic surfaced longest ago, one topic from this week's new sources, and any topic of an exam within 10 days. Each topic's `last_surfaced` is updated in the course file.
- Each story carries a "Quiz me on this" button that starts a mini session (3 cards) on that topic.
- Skippable, silent, no backlog.

## 9. Profile additions

`tutor_context_chars: 150000`, `assignment_lead_days: 3`, `exam_review_offsets_days: [7, 3, 1]`, `cards_per_topic: 8`, `review_time: "18:00"`, `review_daily_cap: 8`, `review_exam_cap: 15`, `exam_focus_days: 7`, `digest_time: "08:30"`, `digest_cadence: daily`.

## 10. Failure handling

Extraction failure → the raw file is kept under `sources/<slug>/raw/` and the user is told; model failure at ingest → stored with minimal frontmatter; tutor with the model down → "model offline" reply (nothing lost, there is nothing to save). Vision unavailable → photos stored without OCR and flagged.

## 11. Build order

1. Channels + courses + ingest + tutor + per-source summaries.
2. Assignments + exams + study plans.
3. Cards + recall session + digest.
