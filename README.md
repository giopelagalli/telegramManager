# Telegram Personal Assistant

A single-user Telegram bot that keeps a plain-text/Markdown knowledge base
(todos, events, goals, a profile) in a git repo, reminds you on schedule,
and talks to a local model over an OpenAI-compatible endpoint. Deployed as a
second, independent systemd service on a DGX Spark that already runs vLLM
for another bot.

See `docs/superpowers/specs/2026-09-03-telegram-assistant-design.md` for the
full design.

## 1. Setup

### 1.0 Prerequisite: the model service

This bot does not run or manage vLLM — it talks to the `sparkmodel.service`
that's already running on the box, OpenAI-compatible at
`http://localhost:8888/v1`, model id `qwen3.8-flash-next`. Before doing
anything else, confirm it's up:

```bash
curl -s localhost:8888/v1/models
```

If that doesn't respond, check `systemctl status sparkmodel` first — nothing
below will work without it. The GPU's unified memory is fully committed to
that model; this bot never loads a model of its own and runs entirely on
CPU.

### 1.1 Telegram bot

1. Talk to [@BotFather](https://t.me/BotFather) on Telegram, run `/newbot`,
   and copy the bot token it gives you. **Create a new bot — do not reuse
   the token from the `sparkbot` service already running on this box.**
2. Talk to [@userinfobot](https://t.me/userinfobot) to get your own numeric
   Telegram user ID. The bot only responds to this one user.

### 1.2 `.env`

Copy `.env.example` to `.env` and fill it in (`deploy/install.sh`, §1.4,
does the copy and permissions for you):

```bash
cp .env.example .env
chmod 600 .env
```

| Variable | Meaning |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token from BotFather (the new bot from §1.1). |
| `TELEGRAM_USER_ID` | Your numeric user ID from `@userinfobot`. |
| `OPENAI_BASE_URL` | `http://localhost:8888/v1` — the running `sparkmodel` service. |
| `OPENAI_API_KEY` | `unused` — vLLM does not check it. |
| `CHAT_MODEL` | `qwen3.8-flash-next` — must match what `sparkmodel` is serving. |
| `CHAT_ENABLE_THINKING` | `false`. Qwen thinks by default; this box's tool-call parser (`qwen3_coder`) works fastest with thinking off. |
| `VISION_BASE_URL` / `VISION_MODEL` | `http://localhost:8888/v1` / `qwen3.8-flash-next`. Worth trying — if the endpoint rejects image input, photo checks (wake-up, task verification) degrade to "not verified" automatically rather than breaking. |
| `GOOGLE_MAPS_API_KEY` | Optional. Without it, travel time falls back to the stored `travel_minutes` on each event. |
| `KNOWLEDGE_DIR` | `/home/giospark1/telegramManager/knowledge` |
| `DATA_DIR` | `/home/giospark1/telegramManager/data` |
| `KOKORO_MODEL_DIR` | `/home/giospark1/telegramManager/models` |

The token in `.env` is a live credential — keep the file at mode `600`
(owner read/write only). This box has had a token leaked in plaintext
before; don't repeat that.

### 1.3 Install

On the Spark, as `giospark1`:

```bash
git clone <repo-url> ~/telegramManager
cd ~/telegramManager
bash deploy/install.sh
```

This creates a venv, installs the package with the `voice` extra, creates
`knowledge/`, `data/`, `models/`, and copies `.env.example` to `.env` if it
doesn't exist yet (setting it to mode `600`). Edit `.env` with the values
from §1.2, then run the two `sudo` lines the script prints at the end to
install and start the `spark-assistant` systemd unit
(`deploy/spark-assistant.service`).

For voice, also run `bash scripts/download_voice_models.sh` (see §3).
Both Whisper (speech-to-text) and Kokoro (text-to-speech) run on CPU here —
the GPU is fully committed to `sparkmodel`, and that's expected.

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

The bot runs as the `spark-assistant` systemd unit, installed in §1.3:

```bash
systemctl status spark-assistant
journalctl -u spark-assistant -f
sudo systemctl restart spark-assistant   # after editing .env or pulling changes
```

`knowledge/` is initialized as a git repo on first run if it isn't one
already (see `bot/knowledge/store.py`).

After a Spark reboot, `sparkmodel` takes about 10 minutes to load. Until
it's ready, the bot is already up but replies to free text with "The model
is offline; saved your message to the inbox" — commands, reminders, and
briefings still work in the meantime, since they don't need the model.

**Memory:** this bot plus CPU Whisper needs roughly 2 GiB, which lives
inside the 26 GiB host reserve — it does not compete with `sparkmodel` for
GPU memory. Never run a second model container or service on this box; the
GPU has no headroom left.

**Don't restart `sparkmodel`** while a critical-mode leave escalation or
wake-up verification is in progress — that's the one time this bot actually
depends on the model responding promptly.

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
