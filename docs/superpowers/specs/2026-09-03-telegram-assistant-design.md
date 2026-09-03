# Telegram Personal Assistant — Design Spec

Date: 2026-09-03
Status: approved in brainstorming, pending user review of this document

## 1. Purpose

A single-user Telegram bot that keeps Giovanni on time and on track. It captures todos, goals, and schedule from text or voice notes, stores them as an Open Knowledge Format (OKF) bundle of markdown files, and proactively messages him: morning and evening briefings, hourly check-ins, leave-by reminders, and, for things flagged critical, an escalating "storm" that only stops on verified action.

Guiding rule from the user: **it must actually work and must not be annoying, or it will not get used.** Every proactive behavior below has a cap, an off switch, and a tunable default.

## 2. Non-goals (v1)

- Phone calls in either direction. Voice is Telegram voice notes only.
- Calendar integrations (Google Calendar etc.). Schedule is fed by chat.
- Multi-user. The bot answers exactly one Telegram user ID.
- General-purpose agent tasks (web research, coding). This is a personal assistant only.
- Any web UI. The OKF folder in an editor is the UI beyond Telegram.

## 3. Decisions and reasons

| Decision | Choice | Why |
|---|---|---|
| Platform | Telegram Bot API | Unrestricted proactive messages, native `/command` menu, inline buttons, location-request buttons, voice notes. WhatsApp official API restricts proactive messages to templates after 24h; Discord voice-receive is unsupported/fragile. |
| Architecture | Deterministic core + LLM at the edges | Anything time-critical (reminders, briefings, views, escalation) is plain code and never waits on or trusts a model. The model handles language: parsing captures, assigning priority, writing prose. |
| Hosting | User's NVIDIA DGX Spark (arm64, 128GB unified memory), Docker Compose | Always on; hosts the LLM locally. |
| LLM | Any OpenAI-compatible endpoint (vLLM on the Spark) | Model is a config value. Start with Nemotron-3-Super-120B-A12B-NVFP4 (validated by vLLM on Spark, ~23 tok/s, tool calling); Qwen3.6-35B NVFP4 as faster fallback. |
| Language | Python 3.12 | faster-whisper, Kokoro, vLLM tooling all live in Python. |
| Storage | OKF markdown bundle in a git repo | Human-readable, editable in any editor, diffable, `/undo` = git revert. |
| Timezone / hours | America/New_York, waking hours 08:00–22:00 | User's answer; in profile, changeable. |

## 4. Architecture

### 4.1 Processes (docker-compose on the Spark)

- **vllm**: NVIDIA-validated vLLM container serving the chat model on `http://vllm:8000/v1` with `--enable-auto-tool-choice` and the model's tool-call parser. Optionally a second small vision model (see §16).
- **bot**: the Python app. One process: Telegram long-polling, APScheduler, model client, STT/TTS in-process. `knowledge/` mounted as a volume.

No database. Runtime state that is not knowledge (open follow-up chains, pause-until, message budget counters, last-user-message time) lives in `data/state.json`, outside the bundle and not committed.

### 4.2 Modules (`bot/` package)

| Module | One job | Depends on |
|---|---|---|
| `telegram/` | Command handlers, free-text and voice handlers, button callbacks, outbound send with retry. Formats views for Telegram. | knowledge, agent, voice, scheduler |
| `knowledge/` | Read/write the OKF bundle. Typed models `Todo`, `Event`, `Goal`, `Profile`. Ranking, view rendering (`/todo`, `/today`, `/week`, `/goals`, `/backlog`), git commit per write, `log.md` append. Only module that touches disk. | nothing internal |
| `agent/` | Wraps the model. `capture(message, context) -> (actions, reply)`; `compose(kind, context) -> text` for briefings, check-ins, follow-ups. Validates tool calls against schemas. | knowledge (read-only types) |
| `scheduler/` | Jobs: briefings, check-ins, per-event reminders, follow-up chains, critical-mode state machines, message budget. Rebuilds jobs from files on startup and after every schedule change. | knowledge, agent, telegram (send), maps |
| `voice/` | `transcribe(ogg_path) -> str` (faster-whisper), `synthesize(text) -> ogg_opus_path` (Kokoro + ffmpeg). | nothing internal |
| `maps/` | `geocode(address) -> (lat, lng)`, `travel_minutes(origin, dest, depart_at) -> int`, `distance_m(a, b)`. Google Maps; returns `None` on any failure. | nothing internal |

Interfaces are functions with typed inputs; no module reads another's internals.

## 5. Knowledge bundle (OKF)

Location: `knowledge/` (a git repo; every write is one commit with message `"<action>: <title>"`).

```
knowledge/
├── index.md              # what is here and how it is organized
├── profile.md            # type: profile — settings and facts about the user
├── log.md                # append-only, one line per change: ISO time, action, file
├── inbox/                # raw messages the model could not parse (type: note)
├── todos/                # one file per todo (type: todo)
│   └── index.md
├── backlog/              # todos with no deadline / low urgency, same schema
│   └── index.md
├── goals/                # one file per goal (type: goal)
│   └── index.md
└── schedule/             # one file per event (type: event)
    └── index.md
```

`index.md` files are regenerated by the bot after each write: a bulleted list of files with title and key fields, so a human or model can navigate by progressive disclosure.

### 5.1 Common frontmatter (OKF)

`type` (required), `title`, `description`, `tags`, `timestamp` (last modified, ISO with offset). Cross-links in bodies use relative markdown links, e.g. `[goal](../goals/2026-ship-app.md)`.

### 5.2 `type: todo` (todos/ and backlog/)

```yaml
---
type: todo
title: Call dentist to reschedule
priority: 1            # 1 high, 2 medium, 3 low
due: 2026-09-05        # date, optional
status: open           # open | done | dropped
verify: none           # none | photo | location | question
goal: ../goals/2026-W36-health.md   # optional
done_at:               # ISO, set when status=done
confirmed: true        # false when marked done without passing verify
tags: [health]
timestamp: 2026-09-03T14:10:00-04:00
---
Free-form notes.
```

Filename: `YYYY-MM-DD-<slug>.md` (date created); suffix `-2`, `-3` on collision. Moving between `todos/` and `backlog/` is a `git mv`.

### 5.3 `type: event` (schedule/)

```yaml
---
type: event
title: Gym
start: 2026-09-04T18:00:00-04:00
end: 2026-09-04T19:00:00-04:00     # optional
location: Equinox Bond St           # free text, optional
location_latlng: [40.72, -73.99]    # set by geocode, optional
travel_minutes: 20                  # from user, or last Maps estimate; 0 if none
prep_minutes: 15                    # default from profile
importance: normal                  # normal | critical
verify: none                        # none | location  (critical events default to location)
status: upcoming                    # upcoming | left | arrived | done | missed
timestamp: 2026-09-03T14:12:00-04:00
---
```

### 5.4 `type: goal` (goals/)

```yaml
---
type: goal
title: Ship the app beta
period: 2026            # "2026" (year) or "2026-W36" (ISO week) or "2026-09" (month)
status: active          # active | done | dropped
timestamp: ...
---
Why this matters, notes.
```

Progress is computed as done/total over todos whose `goal:` links here.

### 5.5 `type: profile` (profile.md)

```yaml
---
type: profile
name: Giovanni
timezone: America/New_York
waking_hours: ["08:00", "22:00"]
home_address: ""                    # set by saying it in chat
home_latlng: []                     # set by geocode
morning_briefing: "08:00"
evening_briefing: "21:00"
wake_time: ""                       # empty = no wake-up mode
wake_photo_spot: "kitchen sink"     # physical challenge for wake-up
checkin_interval_minutes: 60
checkin_skip_if_active_minutes: 20
followup_gaps_minutes: [15, 30, 60, 120]   # briefing chain; check-in chain uses first 2
proactive_budget_per_hour: 3
default_prep_minutes: 15
leave_lead_minutes: 5
critical_leave_cap_minutes: 20
wakeup_cap_minutes: 30
wakeup_engage_seconds: 120
voice_on_proactive: true            # briefings + check-ins get a voice note
voice_reply_mode: on_voice          # on_voice | always | never (replies to your messages)
---
Free-form facts the model may use (gym name, commute habits, etc.).
```

Any field can be changed by free text ("move my evening briefing to 10pm") or by editing the file.

## 6. Commands (instant, no model call)

| Command | Behavior |
|---|---|
| `/todo` | Top 5 open todos by rank (§8). Each has a ✅ button (`done:<file>`). `/todo all` lists everything open. |
| `/backlog` | All items in `backlog/`, oldest first. |
| `/goals` | Active goals grouped by period (year, month, this week) with `done/total` progress. |
| `/today` | Today's events in time order with start, leave-by, travel; then top 5 todos. |
| `/week` | Next 7 days, grouped by day, events with times; days with nothing say "free". |
| `/brief` | Send the morning briefing now. `/brief 9am` shifts today's morning briefing to 9am (one-time). |
| `/pause [duration]` | Silence check-ins and follow-up chains for the duration (default 2h). Reminders and critical mode still fire. |
| `/quiet` | Same as pause until end of waking hours today. |
| `/resume` | Cancel pause. |
| `/undo` | `git revert` the last bot-made commit; reply with what was reverted. |
| `/help` | List commands. |

Button callbacks: `done:<file>`, `defer:<file>` (move due to tomorrow), `snooze:<chain>` (30 min), plus the location-request keyboard button (§13.1).

## 7. Free text and voice capture

Flow: message (or transcribed voice note) → `agent.capture(text, context)` → validated actions applied by `knowledge` (one git commit per message) → reply echoing exactly what was stored → if the message answered an open chain, the chain closes.

Context given to the model: profile body + settings that matter (name, timezone), today's and tomorrow's events, open todos (compact), active goals, current time, and whether an open chain is awaiting an answer (with what was asked).

Tools the model may call (all validated against JSON schemas; unknown/invalid calls are rejected and retried once):

- `add_todo(title, priority, due?, goal?, backlog?, verify?, notes?)`
- `update_todo(file, status?, priority?, due?, goal?, verify?, notes?)`
- `move_todo(file, to: "todos"|"backlog")`
- `add_event(title, start, end?, location?, travel_minutes?, prep_minutes?, importance?)`
- `update_event(file, ...same fields..., status?)`
- `delete_event(file)`
- `add_goal(title, period, notes?)`, `update_goal(file, status?, notes?)`
- `set_profile(field, value)` — restricted to the fields in §5.5
- `snooze(minutes)` — for "not now" / "stop" (§14)
- `reply(text)` — the message to send; required exactly once

Rules for the model (system prompt): one message may produce many actions; assign `priority` using the goals; never invent times, ask instead; when marking done a todo with `verify != none`, do not set `confirmed`, the verification flow (§13.3) does that.

Undo: every applied message is one commit; `/undo` reverts the latest bot commit.

## 8. Ranking rule for `/todo` (deterministic)

Sort open todos in `todos/` by, in order: overdue (due < today) first; due today next; then `priority` ascending; then `due` ascending (no due date last); then created date ascending. Top 5 shown. The model never re-orders this; it can only change `priority` or `due` via actions.

## 9. Schedule and reminders

Computed by `scheduler` whenever an event is created, changed, or on startup:

```
leave_by       = start - travel_minutes
get_ready_at   = leave_by - prep_minutes
leave_at       = leave_by - leave_lead_minutes      # default 5
```

- At `get_ready_at`: if the event has `location` and profile has `home_latlng` and a Maps key exists, refresh `travel_minutes` via Maps with `depart_at = leave_by`; if it changed, rewrite the event file and reschedule `leave_at`. Send "Get ready for {title}. Leave by {leave_by}." (text only).
- At `leave_at`: send "Leave in the next {lead} minutes for {title}." (text only). For `importance: normal` this is the last message for the event.
- Events with no location: `travel_minutes = 0`, same two pings relative to `start`.
- Jobs are persisted implicitly by the event files; a job whose time is more than 15 minutes in the past at startup is dropped, not fired.
- After `start + 15 min` an `upcoming` event becomes `missed` unless status was set otherwise; missed events appear in the evening briefing.

## 10. Check-ins

- Every `checkin_interval_minutes` during waking hours, at a random minute within the interval (jitter is computed per slot, seeded from the date so it is stable across restarts).
- Skip if: paused; user messaged within `checkin_skip_if_active_minutes`; now is inside an event (`start <= now < end`); message budget exhausted (§14).
- Content: `agent.compose("checkin", context)`: 1–3 sentences, grounded in what is due today, what is next, and ends with one concrete question. Fallback template when the model is unavailable: "Next up: {event} at {time}. Top todo: {title}. Done yet?"
- Sent as text + voice note (§15). Opens a follow-up chain with the check-in gap profile (2 steps).

## 11. Briefings

- **Morning** (`morning_briefing`, default 08:00): today's events with leave-by times; top 5 todos with ✅ buttons; this week's goals with progress; one model-written line. If `wake_time` is set, the morning briefing is delivered after wake-up verification (§13.2) instead of at a fixed time.
- **Evening** (`evening_briefing`, default 21:00): what got done today; what slipped (each with a "→ tomorrow" `defer:` button); todos marked done but `confirmed: false` listed as "unconfirmed"; missed events; tomorrow's first event with get-ready and leave-by times.
- Both sent as text + voice note. Both open a follow-up chain (4 steps).
- Moving: `/brief 9am` shifts today's only; `set_profile(morning_briefing, "09:00")` via free text moves it permanently.

## 12. Follow-up chains (non-critical)

A chain is opened by a briefing or check-in and is closed by **any** inbound message or button tap from the user, by `/pause`, by `snooze`, or by exhausting its steps.

| Step | After (defaults) | Content |
|---|---|---|
| 1 | 15 min | Short, by name, restates the single most relevant item. |
| 2 | +30 min | More direct; one thing that matters most right now. |
| 3 | +60 min | Motivational, references an active goal by name. |
| 4 | +120 min | Final; says it will stop until the next scheduled message. |

Check-in chains use only steps 1–2. Each step is `agent.compose("followup", context including previous steps)` so wording does not repeat; fixed templates if the model is down. Follow-ups are text only. Chains never send outside waking hours; a chain still open at end of waking hours is closed and the item resurfaces in the next morning briefing. Only one chain is open at a time; a new briefing/check-in replaces the old chain.

## 13. Critical mode and verification

Triggers: (a) an event with `importance: critical`, at its `leave_at`; (b) wake-up, when `wake_time` is set. Critical mode ignores `/pause` and the message budget, and respects only its own cap.

### 13.1 Critical leave

1. At `leave_at`: "Giovanni, you need to leave now for {title}. You will be late after {leave_by}. Confirm you have left." with a reply keyboard containing a **Share my location** button (Telegram `request_location`). Text replies like "I left" are acknowledged but do not stop the chain.
2. From `leave_by`: message every 60 s for 5 minutes, then every 30 s, each by name, each restating the consequence, each with the location button. Cap: `critical_leave_cap_minutes` after `leave_by`, then a final "I'll stop now" and the event is marked `missed`.
3. Verification: a location message whose `distance_m(fix, home_latlng) > 150` sets `status: left` and ends the chain with a short confirmation. A fix within 150 m replies "You are still at home, Giovanni." and the chain continues. If `home_latlng` is unset, any location fix counts (degraded, and the bot says so once).
4. If the user starts a **live location** share (Telegram sends periodic `edited_message` location updates), the bot tracks it and, when `distance_m(fix, location_latlng) < 200`, sets `status: arrived` and sends one confirmation. Live tracking is optional; the chain already ended at step 3.

### 13.2 Wake-up

At `wake_time` (from profile, or a one-day override via free text):

1. **Alarm phase**: message every 60 s, by name, until any reply. Cap: `wakeup_cap_minutes` total for the whole flow; on cap, stop and send the morning briefing anyway, noting the wake-up was not verified.
2. **Challenge phase**: "Send me a photo of the {wake_photo_spot}." Photo is checked by the vision model (§16): must look like a fresh photo of that kind of spot and not a screenshot/black frame. Up to 3 attempts; each failure explains why. If no vision model is configured, any photo passes and the bot says verification is degraded.
3. **Engagement phase**: `wakeup_engage_seconds` (default 120) of conversation. The model asks about the first task, the plan, what a good day looks like; a reply counts only if it is substantive (≥ 3 words and not in a small "ok/yes/fine" list). If 60 s pass without a substantive reply, return to the alarm phase (cadence 30 s). When cumulative engaged time reaches the target, send "You're up." and then the morning briefing.

### 13.3 Task verification

Set per todo via `verify:`; the model sets it when the user marks a task important, user can override.

- `photo`: the ✅ button asks for a photo; the vision model judges whether it plausibly shows the task done. Pass → `status: done, confirmed: true`. Fail or no photo within 10 min → `status: done, confirmed: false` and listed as unconfirmed in the evening briefing.
- `location`: same as photo but with the location button and a `distance_m < 200` check against the todo's geocoded place (from its notes/location line).
- `question`: the bot asks one specific follow-up ("what did they say about the cleaning?"); the model rates the answer specific/vague; vague → `confirmed: false`.
- `none`: ✅ marks done and confirmed.

Stated limit (in the spec on purpose): none of this proves anything against a determined liar; the goal is to make lying more effort than doing the task, and to reflect unconfirmed items back honestly.

## 14. Non-annoyance rules (hard requirements)

1. Nothing escalates unless the user flagged it critical or set a wake time.
2. Any inbound message or button closes an open non-critical chain.
3. "not now", "stop", "later" in free text → `snooze(120)`: no non-critical proactive messages for 2h, acknowledged with one short line.
4. Proactive budget: at most `proactive_budget_per_hour` non-critical proactive messages per rolling hour (check-ins + follow-ups). Reminders (§9) are exempt because they are time-bound and single-shot.
5. Voice notes only on briefings and check-ins; follow-ups and reminders are text.
6. Check-ins skip when the user is active or inside an event.
7. Nothing non-critical outside waking hours.
8. Every cadence, gap, and cap is in `profile.md`.

## 15. Voice

- Inbound voice note: download OGG → `voice.transcribe` (faster-whisper, `small` or `medium` model; GPU if CTranslate2 CUDA is available on arm64, else CPU) → treated as free text. Reply includes "heard: …" only when confidence is low.
- Outbound: `voice.synthesize` (Kokoro-82M) → WAV → ffmpeg → OGG/Opus → `send_voice`, so it appears as a native voice bubble.
- When: briefings and check-ins always (if `voice_on_proactive`); replies per `voice_reply_mode` (default: voice reply when the user sent voice).

## 16. Model and configuration

Environment (`.env`):

```
TELEGRAM_BOT_TOKEN=
TELEGRAM_USER_ID=            # the only user the bot talks to
OPENAI_BASE_URL=http://vllm:8000/v1
OPENAI_API_KEY=unused
CHAT_MODEL=                  # e.g. nvidia/Nemotron-3-Super-120B-A12B-NVFP4
VISION_BASE_URL=             # optional; OpenAI-compatible endpoint with image input
VISION_MODEL=                # optional
GOOGLE_MAPS_API_KEY=         # optional
KNOWLEDGE_DIR=/knowledge
DATA_DIR=/data
```

The chat model is used with tool calling (`tools=` in the chat completion). Temperature low for `capture`, moderate for `compose`. Max one retry on invalid tool output.

## 17. Failure handling

| Failure | Behavior |
|---|---|
| Model returns invalid/no tool calls | Retry once with the validation error appended; then save the raw text to `knowledge/inbox/` and reply "saved, couldn't parse". |
| vLLM unreachable | Commands, reminders, briefings, chains, critical mode all still run on fixed templates. Free text → inbox with an honest reply. |
| Maps unreachable / no key | Use stored `travel_minutes`; geocode failure → location verification accepts any fix and says so. |
| Vision model missing | Photo checks pass with a "not verified" note. |
| Bot restart | Rebuild all jobs from files; drop jobs > 15 min late; resume an open critical chain if its event has not passed its cap; reload `state.json`. |
| Telegram send fails | Retry with exponential backoff (3 tries), log; never drop silently. |
| Malformed file in bundle | Skip it, log the path, mention it in the next briefing. |

## 18. Testing

- **knowledge**: round-trip every type; ranking order on crafted sets; view rendering against golden text files; index regeneration; git commit and `/undo`.
- **scheduler**: reminder arithmetic across DST and midnight; jitter determinism; follow-up chain state machine; critical leave and wake-up state machines (with a fake clock); message budget; startup rebuild and late-job dropping.
- **agent**: fake model client returning canned tool calls; schema validation and retry path; inbox fallback.
- **maps / voice**: thin, mocked at the HTTP / library boundary.
- **telegram**: handlers are thin; one smoke test per command using python-telegram-bot test helpers.
- **End-to-end**: run on the Spark with the real bot token, walk through capture, `/today`, a leave-by reminder, a check-in, and a critical leave with a real location share before calling v1 done.

## 19. Deployment

`docker-compose.yml` on the Spark: `vllm` (NVIDIA container, `--gpu-memory-utilization 0.85`, `--max-num-seqs 4`, tool parser per model) and `bot` (arm64 Python image, `restart: always`), volumes `./knowledge:/knowledge` and `./data:/data`. `knowledge/` is a git repo initialised on first run.

## 20. Phase-two hooks (not built now)

- Twilio voice call as a final escalation step in critical mode (`scheduler` exposes an `escalate_external()` no-op hook).
- Google Calendar as a second event source (`knowledge` event schema already has `location`, `start`, `end`).
- Discord or WhatsApp transports (only `telegram/` would be swapped).
