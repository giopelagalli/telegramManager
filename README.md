# Telegram Personal Assistant

A single-user Telegram bot that keeps a plain-text/Markdown knowledge base
(todos, events, goals, a profile) in a git repo, reminds you on schedule,
and talks to a local model over an OpenAI-compatible endpoint. Designed to
run on an NVIDIA DGX Spark (arm64) with vLLM serving the model.

See `docs/superpowers/specs/2026-09-03-telegram-assistant-design.md` for the
full design.

## 1. Setup

### 1.1 Telegram bot

1. Talk to [@BotFather](https://t.me/BotFather) on Telegram, run `/newbot`,
   and copy the bot token it gives you.
2. Talk to [@userinfobot](https://t.me/userinfobot) to get your own numeric
   Telegram user ID. The bot only responds to this one user.

### 1.2 `.env`

Copy `.env.example` to `.env` and fill it in:

```bash
cp .env.example .env
```

| Variable | Meaning |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token from BotFather. |
| `TELEGRAM_USER_ID` | Your numeric user ID from `@userinfobot`. |
| `OPENAI_BASE_URL` | OpenAI-compatible chat endpoint. In compose this is `http://vllm:8000/v1` (set automatically by `docker-compose.yml`, so you can leave it blank in `.env`). |
| `OPENAI_API_KEY` | Anything (e.g. `unused`) — vLLM does not check it. |
| `CHAT_MODEL` | Model name to request at `OPENAI_BASE_URL`. Must match what vLLM is serving (`VLLM_MODEL` below). |
| `VISION_BASE_URL` / `VISION_MODEL` | Optional OpenAI-compatible vision endpoint, for wake-up photo and task-photo verification. Leave blank to run without photo verification (degraded, not broken — see §17 of the design spec). |
| `GOOGLE_MAPS_API_KEY` | Optional. Without it, travel time falls back to the stored `travel_minutes` on each event. |
| `KNOWLEDGE_DIR` | Directory holding the knowledge base Markdown files. In compose this is `/knowledge`, mounted from `./knowledge`. |
| `DATA_DIR` | Directory for runtime state (`state.json`, temp voice files). In compose this is `/data`, mounted from `./data`. |
| `VLLM_MODEL` | Compose-only: the model id passed to `vllm serve`. Should match `CHAT_MODEL`. |
| `VLLM_TOOL_PARSER` | Compose-only: the `--tool-call-parser` value vLLM uses for that model. Defaults to `hermes` if unset. |
| `KOKORO_MODEL_DIR` | Directory containing the Kokoro voice model files (see §3). Defaults to `/models` if unset; in compose this is mounted read-only from `./models`. |

### 1.3 Model choice

Two checkpoints are known to work well on the Spark's 128 GB unified memory
in NVFP4 quantization. Set `CHAT_MODEL` and `VLLM_MODEL` to the one you pick,
and `VLLM_TOOL_PARSER` to match:

- **`nvidia/Nemotron-3-Super-120B-A12B-NVFP4`** — larger, higher quality.
  Tool parser: `nemotron` if the vLLM version you deploy ships one for it,
  otherwise `hermes`. Check the model card and your vLLM release notes
  before choosing.
- **`Qwen/Qwen3.6-35B-A3B-NVFP4`** — smaller, faster, lower memory headroom
  needed. Tool parser: `hermes`.

Either way, confirm the tool-call parser against the model card you deploy —
picking the wrong one silently breaks tool calling (the model replies in
plain text instead of calling `add_todo`, etc.) rather than erroring loudly.

The `vllm` service's image tag in `docker-compose.yml`
(`nvcr.io/nvidia/vllm:26.04-py3`) is a **placeholder**. Confirm the current
arm64/GB10-compatible vLLM container tag on
[NVIDIA NGC](https://catalog.ngc.nvidia.com/) before deploying, and update
the compose file if it has moved.

## 2. Knowledge base

`KNOWLEDGE_DIR` is a git repo of Markdown files (todos, events, goals, a
profile) — see §5 of the design spec for the exact schema. It's safe to edit
by hand: open a file in any editor, change frontmatter or body text, and
save. The bot re-reads files from disk on each interaction, and every change
it makes (yours or the bot's) is a git commit, so `/undo` and `git log` both
work regardless of who made the edit. If you hand-edit while the bot is
mid-flow (e.g. between generating and rendering a message), your edit is
picked up on the bot's next read — there's no locking, so avoid editing the
exact file the bot is actively writing to at that instant.

## 3. Voice models

Voice replies use [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) via
`kokoro-onnx`; voice input (transcribing your voice notes) uses
`faster-whisper`. Both are optional — the bot runs fine without them, just
without spoken audio (see `bot/__main__.py`: `_synthesizer()` and
`_transcriber()` log a warning and return `None` if the `voice` extra or the
model files aren't available, degrading to text only).

To enable voice replies, fetch the two Kokoro ONNX files into `./models`:

```bash
./scripts/download_voice_models.sh
```

This does not download automatically — it checks whether
`kokoro-v1.0.onnx` and `voices-v1.0.bin` are already in `./models` and, if
not, prints where to get them (the `kokoro-onnx` project's release assets)
and exits non-zero. Fetch the two files by hand into `./models`, then rerun
the script to confirm.

## 4. Phone-side setup for critical mode

Critical mode (a `critical` event's leave time, or wake-up verification) is
only useful if the message actually reaches you. On the phone you carry:

- **Exempt the Telegram app (or at least this chat) from Focus/Do Not
  Disturb.** iOS: Settings → Focus → your active Focus → Apps → allow
  Telegram, or add the bot's chat as an allowed person under
  Notifications → per-app override. Android: Settings → Notifications → Do
  Not Disturb → Apps → allow Telegram.
- **Set a distinct, loud notification sound for this specific chat**, not
  the default Telegram tone — open the chat → chat settings/mute icon →
  notification sound, and pick something you won't sleep through. This
  matters most for the wake-up alarm phase (§13.2 of the design spec), which
  repeats every 60 seconds until you respond.
- Turn notification banners/sound back to full volume before bed if your
  phone auto-lowers volume overnight.

## 5. Running it

```bash
docker compose up -d
```

This builds and starts two services:

- `vllm` — serves `VLLM_MODEL` on `:8000` with tool calling enabled.
- `bot` — the Telegram bot, connecting to `vllm` at
  `http://vllm:8000/v1`, with `./knowledge`, `./data`, and `./models`
  (read-only) mounted in.

`knowledge/` is initialized as a git repo on first run if it isn't one
already (see `bot/knowledge/store.py`).

To check logs: `docker compose logs -f bot` / `docker compose logs -f vllm`.
To stop: `docker compose down`.

## 6. Commands

| Command | What it does |
|---|---|
| `/todo` | Top 5 open todos by rank (`/todo all` for everything). |
| `/backlog` | Everything parked in the backlog. |
| `/goals` | Active goals and progress. |
| `/today` | Today's events and top todos. |
| `/week` | The next 7 days. |
| `/brief` | Morning briefing now (`/brief 9am` to shift today's). |
| `/pause` | Quiet for a while (`/pause 2h`). |
| `/quiet` | Quiet until the end of the day. |
| `/resume` | Cancel the pause. |
| `/undo` | Revert the last change. |
| `/help` | List commands. |

Anything else you send — text or a voice note — is treated as free-form
capture: the model turns it into todos/events/goals as needed and replies
with what it stored.

## 7. End-to-end checklist

Run this manually against the real bot on the Spark before calling a change
done. Each step should behave as described; if it doesn't, something in the
chain (scheduler, agent, voice, maps, or Telegram wiring) needs fixing
before shipping.

1. Send `call dentist friday` as a normal message.
2. Run `/todo` — the dentist call should appear.
3. Send `gym at 6, 20 min away` as a normal message (an event with travel
   time).
4. Run `/today` — it should show the gym event with "leave by 5:40".
5. Wait for the scheduled get-ready message, then the leave message, for
   the gym event.
6. Send a voice note (any short message). You should get a voice reply
   back (heard as a native Telegram voice bubble, not just text).
7. Send `/pause 1h`. Confirm no check-in message arrives during that hour.
8. Send `flight tomorrow 6pm, this one is critical` to flag a critical
   event.
9. At the critical event's leave time, confirm the "you need to leave now"
   escalation fires repeatedly (the "storm") — then tap **Share my
   location** (or send a one-off location) and confirm the messages stop
   once you're far enough from home.

## 8. Testing

```bash
python -m pip install -e '.[dev,voice]'
pytest -q -W error
```
