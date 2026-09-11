# Telegram Personal Assistant

A single-user Telegram bot that keeps a plain-text/Markdown knowledge base
(todos, events, goals, a profile) in a git repo, reminds you on schedule,
and talks to a model over an OpenAI-compatible endpoint. It runs as a systemd
service on an always-on Digital Ocean droplet; the model lives elsewhere.

See `docs/superpowers/specs/2026-09-03-telegram-assistant-design.md` for the
full design.

## 1. Setup

`docs/setup-checklist.md` is the shopping list: every token, id, key and
machine detail this section needs, and exactly where to click to get it.

### 1.0 Topology

Three machines, one bot:

- **Droplet (Digital Ocean).** Runs this bot: Telegram polling, the
  scheduler, the knowledge repo, and voice (Whisper + Kokoro) on CPU. It is
  always on — that's the whole point of moving off the Spark.
- **DGX Spark.** A model server and nothing else. Its vLLM
  (`http://<spark-hostname>:8888/v1`, model `qwen3.8-flash-next`) is reached
  from the droplet over Tailscale. It also holds one of the knowledge
  backups.
- **Fireworks AI.** The fallback model endpoint. When the Spark is
  unreachable — rebooting, loading, off the network — the bot's circuit
  breaker routes chat to Fireworks for ten minutes, then retries the Spark.

Knowledge backups go to two places after every change: bare git repos on the
Spark and on the owner's Mac, both reachable over Tailscale and both machines
the owner controls. A nightly, client-side-encrypted `restic` snapshot of
`knowledge/` and `data/` also goes to Backblaze B2. See §1.4.

**Data leaves the building only when the Spark is down.** Message content
(and, for the classroom tutor, the sources it draws on) is otherwise
processed entirely on the Spark, over the tailnet. When the Spark is
unreachable, the circuit breaker falls back to Fireworks AI for ten minutes,
which then sees that traffic on its own servers — a tradeoff the owner has
accepted in exchange for the bot staying responsive.

### 1.1 Tailscale

Install Tailscale on both the droplet and the Spark and run `tailscale up` on
each, joining the same tailnet. Then, from the droplet:

```bash
curl -s http://<spark-hostname>:8888/v1/models
```

`<spark-hostname>` is the Spark's MagicDNS name or its `100.x.y.z` address
(`tailscale status` lists both).

If that hangs or refuses, vLLM on the Spark is almost certainly bound to
`localhost` only — the default in that setup — so it never sees the Tailscale
interface. Fix it on the Spark, not here: publish the port on all interfaces
in the container's `docker run -p 8888:8888` (rather than
`-p 127.0.0.1:8888:8888`), or set the host/PORT variables in that recipe's
own `.env`. Restart the model service and re-run the `curl`.

### 1.2 Telegram bot

1. Talk to [@BotFather](https://t.me/BotFather) on Telegram, run `/newbot`,
   and copy the bot token it gives you. **Create a new bot — do not reuse
   the token from the `sparkbot` service running on the Spark.**
2. Talk to [@userinfobot](https://t.me/userinfobot) to get your own numeric
   Telegram user ID. The bot only responds to this one user.

### 1.3 `.env`

Copy `.env.example` to `.env` and fill it in (`deploy/install.sh`, §1.5,
does the copy and permissions for you):

```bash
cp .env.example .env
chmod 600 .env
```

| Variable | Meaning |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token from BotFather (the new bot from §1.2). |
| `TELEGRAM_USER_ID` | Your numeric user ID from `@userinfobot` (§1.2). |
| `OPENAI_BASE_URL` | `http://<spark-hostname>:8888/v1` — the Spark's vLLM over Tailscale (§1.1). |
| `OPENAI_API_KEY` | `unused` — vLLM does not check it. |
| `CHAT_MODEL` | `qwen3.8-flash-next` — must match what the Spark is serving. |
| `CHAT_ENABLE_THINKING` | `false`. Qwen thinks by default; the Spark's tool-call parser (`qwen3_coder`) works fastest with thinking off. |
| `FALLBACK_BASE_URL` | `https://api.fireworks.ai/inference/v1`. |
| `FALLBACK_API_KEY` | Your Fireworks API key. |
| `FALLBACK_MODEL` | A Fireworks model that supports tool calling — a Qwen3 variant keeps behavior closest to the local one. All three `FALLBACK_*` variables must be set together, or none of them. |
| `FALLBACK_EXTRA_BODY` | Optional JSON object merged into every request to `FALLBACK_MODEL`, e.g. `{"thinking": {"type": "disabled"}}`. See "Thinking on Fireworks models" below. |
| `VISION_BASE_URL` / `VISION_MODEL` | The Spark endpoint / `qwen3.8-flash-next`. Worth trying — if the endpoint rejects image input, photo checks (wake-up, task verification) degrade to "not verified" automatically rather than breaking. Vision does not fall back. |
| `FALLBACK_VISION_MODEL` | Optional; a vision-capable Fireworks model. Requires `FALLBACK_BASE_URL` and `FALLBACK_API_KEY` to also be set. When set, photo checks and OCR fall back to Fireworks too when the Spark is down. |
| `FALLBACK_VISION_EXTRA_BODY` | Optional JSON object merged into every request to `FALLBACK_VISION_MODEL`. |
| `HARD_MODEL` | Optional; a strong Fireworks model (e.g. GLM 5.3 or Kimi K3) for `/hard`, the explicit escape hatch to a bigger cloud model. Requires `FALLBACK_BASE_URL` and `FALLBACK_API_KEY` to also be set. |
| `HARD_EXTRA_BODY` | Optional JSON object merged into every request to `HARD_MODEL`, e.g. `{"thinking": {"type": "enabled"}}` or `{"reasoning_effort": "high"}`. See "Thinking on Fireworks models" below. |
| `GOOGLE_MAPS_API_KEY` | Optional. Without it, travel time falls back to the stored `travel_minutes` on each event. |
| `KNOWLEDGE_DIR` | `/home/<user>/telegramManager/knowledge` |
| `KNOWLEDGE_REMOTES` | `<spark-user>@<spark-hostname>:backups/knowledge.git,<mac-user>@<mac-hostname>:backups/knowledge.git` — see §1.4. |
| `DATA_DIR` | `/home/<user>/telegramManager/data` |
| `WHISPER_MODEL` | `base` on the droplet's CPU; `small` if it has 4+ cores. |
| `KOKORO_MODEL_DIR` | `/home/<user>/telegramManager/models` |

The token in `.env` is a live credential — keep the file at mode `600`
(owner read/write only). The Fireworks key in the same file deserves the
same care.

**Thinking on Fireworks models.** The thinking switch differs per model
family — there's no single flag that works everywhere. Open the model's
page on [app.fireworks.ai](https://app.fireworks.ai), find its
reasoning/thinking parameter, and paste it as JSON into the matching
`*_EXTRA_BODY` variable above. Recommended: thinking on for `HARD_MODEL`
(`HARD_EXTRA_BODY`), off for `FALLBACK_MODEL` (`FALLBACK_EXTRA_BODY`).

### 1.4 Knowledge backups

The knowledge repo is pushed to every URL in `KNOWLEDGE_REMOTES` after each
commit, in a background thread — a remote that's down only logs a warning.
Both remotes are bare repos on machines the owner controls, not GitHub:
a private GitHub repo is still plaintext to GitHub, and this bundle holds
home address, schedule, health and school data.

Set the two up once:

1. On the Spark: `git init --bare ~/backups/knowledge.git`.
2. On the Mac: the same command, e.g. `git init --bare ~/backups/knowledge.git`.
   The Mac needs Tailscale running and Remote Login enabled (System
   Settings → General → Sharing → turn on Remote Login) so it accepts SSH
   over the tailnet.
3. On the droplet, `ssh-keygen -t ed25519` (no passphrase — systemd runs the
   push unattended), then append `~/.ssh/id_ed25519.pub` to
   `~/.ssh/authorized_keys` on both the Spark and the Mac.
4. Test both by hand from `knowledge/` before trusting them:

```bash
git -C knowledge push <spark-user>@<spark-hostname>:backups/knowledge.git HEAD:refs/heads/main
git -C knowledge push <mac-user>@<mac-hostname>:backups/knowledge.git HEAD:refs/heads/main
```

**Restore.** On a fresh box, `git clone <remote> knowledge`, point
`KNOWLEDGE_DIR` at it, and start the bot — it picks the repo up as is.

`deploy/backup.sh` covers the other layer: a nightly `restic` snapshot of
`knowledge/` and `data/` to Backblaze B2, client-side encrypted before it
leaves the droplet. See the script's header and the setup checklist for the
one-time B2 setup.

### 1.5 Install

On the droplet:

```bash
git clone <repo-url> ~/telegramManager
cd ~/telegramManager
bash deploy/install.sh
```

This creates a venv, installs the package with the `voice` extra, creates
`knowledge/`, `data/`, `models/`, copies `.env.example` to `.env` if it
doesn't exist yet (mode `600`), and writes
`deploy/assistant.service.generated` with this user and path filled in. Edit
`.env` with the values from §1.3 and §1.4, then run the `sudo` line the
script prints to install and start the `assistant` systemd unit.

For voice, also run `bash scripts/download_voice_models.sh` (see §3). Both
Whisper (speech-to-text) and Kokoro (text-to-speech) run on the droplet's
CPU.

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

The bot runs as the `assistant` systemd unit, installed in §1.5:

```bash
systemctl status assistant
journalctl -u assistant -f
sudo systemctl restart assistant   # after editing .env or pulling changes
```

`knowledge/` is initialized as a git repo on first run if it isn't one
already (see `bot/knowledge/store.py`).

**Spark warm-up.** After a Spark reboot, vLLM takes about 10 minutes to load.
The bot never waits for it: the first request fails fast on the connection
timeout, the circuit breaker opens, and chat goes to Fireworks for the next
ten minutes before the Spark is tried again. Replies keep working throughout,
just from the fallback model. The same applies if the Spark is off or the
tailnet is down.

Only the chat model falls back. Vision (photo checks) is Spark-only and
degrades to "not verified" while the Spark is away; commands, reminders and
briefings never need a model at all.

## 6. Commands

| Command | What it does |
|---|---|
| `/todo` | Top 5 open todos by rank (`/todo all` for everything). |
| `/backlog` | Everything parked in the backlog. |
| `/goals` | Active goals and progress. |
| `/today` | Today's events and top todos. |
| `/week` | The next 7 days. |
| `/now` | One line: the next thing to do or get ready for. |
| `/brief` | Morning briefing now (`/brief 9am` to shift today's). |
| `/courses` | Courses and their topic counts. |
| `/channels` | Which topic is bound to what. |
| `/bind`, `/unbind` | Fallback bind for a topic created before the bot joined (`/bind course CS101 Intro to CS`). |
| `/sources` | Course material stored here (see §7). |
| `/summary` | Summary of a source from the last `/sources` (`/summary 2`). |
| `/move` | Move the last ingested source to another course (`/move phys1`). |
| `/pause` | Quiet for a while (`/pause 2h`). |
| `/quiet` | Quiet until the end of the day. |
| `/resume` | Cancel the pause. |
| `/undo` | Revert the last change. |
| `/hard` | Ask the big cloud model directly, bypassing the Spark (`/hard why does X happen?`). |
| `/think` | Model thinking on/off for the Spark model (`/think on`). |
| `/help` | List commands. |

Anything else you send — text or a voice note — is treated as free-form
capture: the model turns it into todos/events/goals as needed and replies
with what it stored.

**`/hard`** always goes to `HARD_MODEL` over `FALLBACK_BASE_URL`, never the
Spark, and never opens the circuit breaker if that endpoint is slow or down.
In a course topic it grounds the answer in that course's sources like normal
tutoring, sized for the Spark's full context rather than the smaller fallback
budget; anywhere else it's a plain grounded Q&A over your profile and
schedule, with no capture and no knowledge-base writes. Every reply is
prefixed with a small `via <model>` line so it's obvious when you're not
talking to the usual model. If nothing is configured, `/hard` says so. Phase 2
and 3 (plan generation, flashcards) will reach for `HARD_MODEL` automatically
when it's set, for the same reason: some jobs are worth the trip off the
Spark.

## 7. Study

School runs in a **private Telegram group with Topics turned on**, with the
bot added as an admin (setup checklist step 3) — add the bot **before**
creating any topics, so it sees each one get created. Your DM stays the life
channel — briefings, check-ins, critical mode. Each topic in the group gets a
job from its own name: create a topic and name it, and the bot binds itself
and replies to confirm.

```
CS101                             # one topic per course, named after it
Assignments                       # HW, projects, labs across courses
Exams                             # quizzes, exams, study plans
Review                            # daily quiz session and digest
```

A course topic's name becomes the course title, and the course file is
created if it doesn't exist. Renaming a topic rebinds it — an empty course
is renamed in place, otherwise a new one is created. A topic named `Life`,
`General` or `Main` gets a reply pointing back at your DM instead.

If the bot joined **after** a topic already existed, that topic never fired
the naming event; run `/bind` inside it as a fallback (`/bind course CS101
Intro to CS`, `/bind assignments`, `/bind exams`, `/bind review`). A topic
that isn't bound gets one reply explaining `/bind`, then is ignored.

**Dropping sources.** In a course topic, send a PDF, a `.pptx`, a `.docx`, a
`.txt`/`.md` file, or a photo of the board. PDFs are extracted per page, slides per slide (with
speaker notes), `.docx` files in ~40-paragraph parts, and `.txt`/`.md` files
as the notes they already are, so answers can cite
`[Lecture 7, p.12]`; photos go through the vision model's OCR — on the Spark,
`VISION_BASE_URL`/`VISION_MODEL` can point at the same endpoint as chat,
since `qwen3.8-flash-next` accepts images — or are stored with just their
caption if no vision model is configured. Photo OCR falls back to Fireworks
too when `FALLBACK_VISION_MODEL` is set. The model then writes a title,
kind, topics and a short summary, and the bot replies with what it stored. A
file it can't read is kept as-is under `sources/<course>/raw/` and it says
so. Everything is one commit, so `/undo` works. Telegram caps bot downloads
at **20 MB**: a bigger file is refused with a note asking you to split it.

**Tutoring.** Plain text or a voice note in a course topic is answered from
that course's sources, cited by page. If the message is study content rather
than a question — notes, a definition you want kept — it's filed as a note
source instead and the bot replies `Saved note: …`.

**Looking at what's stored.** `/sources` lists the course's material in a
course topic, or everything grouped by course anywhere else; `/summary 2`
prints the summary of the second item in that listing. If something lands in
the wrong course, `/move phys1` moves the most recent one across.

**`/now`** works anywhere and needs no model: one line, either the event
starting within 90 minutes (with the leave-by time), the top-ranked open
todo, or "nothing urgent".

## 8. End-to-end checklist

Run this manually against the real bot on the droplet before calling a change
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
9. Stop the Spark's model service and send a normal message. The bot
   should still answer within a minute, via Fireworks — `journalctl -u
   assistant -f` shows the "primary model unreachable" warning. Start the
   model service again.
10. At the critical event's leave time, confirm the "you need to leave now"
   escalation fires repeatedly (the "storm") — then tap **Share my
   location** (or send a one-off location) and confirm the messages stop
   once you're far enough from home.

## 9. Testing

```bash
python -m pip install -e '.[dev,voice]'
pytest -q -W error
```
