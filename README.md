# Telegram Personal Assistant

A single-user Telegram bot that keeps a plain-text/Markdown knowledge base
(todos, events, goals, a profile) in a git repo, reminds you on schedule,
and talks to a model over an OpenAI-compatible endpoint. Since 2026-09-18 it
runs on the DGX Spark itself (`assistant.service`, see `docs/spark-setup.md`)
next to the vLLM it talks to; the DigitalOcean droplet described in §1.5 and
the remotes section is history, kept for reference. `docs/ROADMAP.md`,
`docs/ARCHITECTURE.md` and `docs/decisions/` are the current map.

See `docs/superpowers/specs/2026-09-03-telegram-assistant-design.md` for the
full design.

## 1. Setup

`docs/setup-checklist.md` is the shopping list: every token, id, key and
machine detail this section needs, and exactly where to click to get it.

### 1.0 Topology

Two machines, one bot:

- **DigitalOcean droplet.** Runs the bot 24/7: Telegram polling, scheduler,
  knowledge repo, and voice (Kokoro for replies). A $6/month 1 vCPU / 1 GB
  droplet is enough — the 1 GB is plenty because Whisper is never loaded
  here (`STT_PROVIDER=api`, §1.2); `pip install -e '.[voice]'` still pulls in
  faster-whisper alongside Kokoro, it's just dead weight in this topology.
- **DGX Spark.** Serves the model only, reached over Tailscale at
  `OPENAI_BASE_URL=http://<spark-hostname>:8888/v1` — a network hop instead
  of the same box.
- **Fireworks AI.** `FALLBACK_MODEL` for chat when the Spark is slow or
  unreachable, `FALLBACK_VISION_MODEL` for photo checks, `HARD_MODEL` for
  the explicit `/hard` escape hatch, and `STT_MODEL` (Fireworks Whisper) for
  voice transcription, since the droplet has no spare CPU for faster-whisper.

Knowledge backups go to two places after every change: bare git repos on the
Spark and the owner's Mac, both reachable over Tailscale, and a nightly,
client-side encrypted `restic` snapshot of `knowledge/` and `data/` to
Backblaze B2. See §1.3.

**Honest cost of this topology:** the bot and the model no longer share a
failure domain — when the Spark reboots, the droplet keeps running, replies
and reminders keep flowing, and chat/vision fall back to Fireworks for
roughly the 10 minutes vLLM takes to reload (§5). What you trade for that:
the model is a network hop away instead of local, so the Spark being
unreachable (not just slow) leans on Fireworks until Tailscale or the Spark
recovers, and it costs $6/month instead of nothing. Running everything on
the Spark instead is free but puts the bot back in the same failure domain
as the model — see §1.5.

Message content is processed wherever it's answered — normally the Spark,
over Tailscale, but Fireworks whenever it's used as the chat/vision
fallback, for `/hard`, or to transcribe a voice note — a tradeoff the owner
has accepted in exchange for the bot staying responsive when the local model
is struggling or unreachable.

### 1.1 Telegram bot

1. Talk to [@BotFather](https://t.me/BotFather) on Telegram, run `/newbot`,
   and copy the bot token it gives you. **Create a new bot for this
   purpose** — don't reuse a token from another bot you run (e.g.
   `sparkbot` on the Spark).
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
| `TELEGRAM_USER_ID` | Your numeric user ID from `@userinfobot` (§1.1). |
| `OPENAI_BASE_URL` | `http://<spark-hostname>:8888/v1` — the Spark's vLLM, reached over Tailscale (`<spark-hostname>` is the Spark's MagicDNS name or `100.x.y.z` address, from `tailscale status`). |
| `OPENAI_API_KEY` | `unused` — vLLM does not check it. |
| `CHAT_MODEL` | `qwen3.8-flash-next` — must match what `sparkmodel` is serving. |
| `CHAT_ENABLE_THINKING` | `false`. Qwen thinks by default; the Spark's tool-call parser (`qwen3_coder`) works fastest with thinking off. |
| `FALLBACK_BASE_URL` | `https://api.fireworks.ai/inference/v1`. |
| `FALLBACK_API_KEY` | Your Fireworks API key. |
| `FALLBACK_MODEL` | A Fireworks model that supports tool calling — a Qwen3 variant keeps behavior closest to the local one. All three `FALLBACK_*` variables must be set together, or none of them. |
| `FALLBACK_EXTRA_BODY` | Optional JSON object merged into every request to `FALLBACK_MODEL`, e.g. `{"thinking": {"type": "disabled"}}`. See "Thinking on Fireworks models" below. |
| `VISION_BASE_URL` / `VISION_MODEL` | `http://<spark-hostname>:8888/v1` / `qwen3.8-flash-next`. Worth trying — if the endpoint rejects image input, photo checks (wake-up, task verification) degrade to "not verified" automatically rather than breaking. Vision does not fall back. |
| `FALLBACK_VISION_MODEL` | Optional; a vision-capable Fireworks model. Requires `FALLBACK_BASE_URL` and `FALLBACK_API_KEY` to also be set. When set, photo checks and OCR fall back to Fireworks too when the Spark is down. |
| `FALLBACK_VISION_EXTRA_BODY` | Optional JSON object merged into every request to `FALLBACK_VISION_MODEL`. |
| `HARD_MODEL` | Optional; a strong Fireworks model (e.g. GLM 5.3 or Kimi K3) for `/hard`, the explicit escape hatch to a bigger cloud model. Requires `FALLBACK_BASE_URL` and `FALLBACK_API_KEY` to also be set. |
| `HARD_EXTRA_BODY` | Optional JSON object merged into every request to `HARD_MODEL`, e.g. `{"thinking": {"type": "enabled"}}` or `{"reasoning_effort": "high"}`. See "Thinking on Fireworks models" below. |
| `GOOGLE_MAPS_API_KEY` | Optional. One key with Geocoding, Routes, Places (New), Time Zone and Pollen enabled, restricted to those five. Without it, travel time falls back to the stored `travel_minutes` on each event. |
| `SPARK_VOICE_URL` | Optional. The Spark's voice server (`spark/README.md`): voice notes in and out and memory embeddings stay local. With `SPARK_URL` set and this unset, those three are simply off rather than sent to Fireworks. |
| `TTS_API_KEY` | Optional. An OpenAI key: voice notes come from `gpt-4o-mini-tts` (voice `onyx`, style in `TTS_INSTRUCTIONS`) instead of local Kokoro. Voice notes only go out in replies, so this costs cents. |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_FROM` / `PHONE` | Optional, all four together. A critical event's leave-now storm also rings your phone: at the first storm message and every fifth after. Save the Twilio number as a contact with Emergency Bypass so it rings through Silent and Sleep Focus. |
| `KNOWLEDGE_DIR` | `/home/<droplet-user>/telegramManager/knowledge` |
| `KNOWLEDGE_REMOTES` | `<spark-user>@<spark-hostname>:backups/knowledge.git,<mac-user>@<mac-hostname>:backups/knowledge.git` — see §1.3. |
| `DATA_DIR` | `/home/<droplet-user>/telegramManager/data` |
| `WHISPER_MODEL` | Unused on this path (`STT_PROVIDER=api` below) — leave blank. |
| `STT_PROVIDER` | `api` — the droplet transcribes voice notes through an OpenAI-compatible endpoint instead of running faster-whisper locally. |
| `STT_BASE_URL` / `STT_API_KEY` / `STT_MODEL` | `https://audio-prod.us-virginia-1.direct.fireworks.ai/v1` (Fireworks serves audio on its own host) / your Fireworks key / a Whisper model id, e.g. `whisper-v3` — see app.fireworks.ai/models. All three are required when `STT_PROVIDER=api`. |
| `KOKORO_MODEL_DIR` | `/home/<droplet-user>/telegramManager/models` |

The token in `.env` is a live credential — keep the file at mode `600`
(owner read/write only). The Fireworks key in the same file deserves the
same care.

**Thinking on Fireworks models.** The thinking switch differs per model
family — there's no single flag that works everywhere. Open the model's
page on [app.fireworks.ai](https://app.fireworks.ai), find its
reasoning/thinking parameter, and paste it as JSON into the matching
`*_EXTRA_BODY` variable above. Recommended: thinking on for `HARD_MODEL`
(`HARD_EXTRA_BODY`), off for `FALLBACK_MODEL` (`FALLBACK_EXTRA_BODY`).

A complete example for the droplet, with secrets left blank:

```bash
TELEGRAM_BOT_TOKEN=
TELEGRAM_USER_ID=
OPENAI_BASE_URL=http://<spark-hostname>:8888/v1
OPENAI_API_KEY=unused
CHAT_MODEL=qwen3.8-flash-next
CHAT_ENABLE_THINKING=
FALLBACK_BASE_URL=https://api.fireworks.ai/inference/v1
FALLBACK_API_KEY=
FALLBACK_MODEL=
FALLBACK_EXTRA_BODY=
VISION_BASE_URL=http://<spark-hostname>:8888/v1
VISION_MODEL=qwen3.8-flash-next
FALLBACK_VISION_MODEL=
FALLBACK_VISION_EXTRA_BODY=
HARD_MODEL=
HARD_EXTRA_BODY=
GOOGLE_MAPS_API_KEY=
KNOWLEDGE_DIR=/home/<droplet-user>/telegramManager/knowledge
KNOWLEDGE_REMOTES=<spark-user>@<spark-hostname>:backups/knowledge.git,<mac-user>@<mac-hostname>:backups/knowledge.git
DATA_DIR=/home/<droplet-user>/telegramManager/data
WHISPER_MODEL=
STT_PROVIDER=api
STT_BASE_URL=https://audio-prod.us-virginia-1.direct.fireworks.ai/v1
STT_API_KEY=
STT_MODEL=whisper-v3
KOKORO_MODEL_DIR=/home/<droplet-user>/telegramManager/models
```

### 1.3 Knowledge backups

The knowledge repo is pushed to every URL in `KNOWLEDGE_REMOTES` after each
commit, in a background thread — a remote that's down only logs a warning.
On the droplet there are two remotes to set up: bare repos on the Spark and
on the owner's Mac, neither on GitHub — a private GitHub repo is still
plaintext to GitHub, and this bundle holds home address, schedule, health
and school data.

Set it up once:

1. Install Tailscale on the droplet, the Spark and the Mac (`tailscale up`
   on each, joining the same tailnet) if it isn't already there.
2. On the Spark: `git init --bare ~/backups/knowledge.git`.
3. On the Mac: `git init --bare ~/backups/knowledge.git`. The Mac needs
   Tailscale running and Remote Login enabled (System Settings → General →
   Sharing → turn on Remote Login) so it accepts SSH over the tailnet.
4. On the droplet, `ssh-keygen -t ed25519` (no passphrase — systemd runs the
   push unattended), then append `~/.ssh/id_ed25519.pub` to
   `~/.ssh/authorized_keys` on both the Spark and the Mac.
5. Test it by hand from `knowledge/` before trusting it:

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

### 1.4 Install

On the droplet, as `<droplet-user>`:

1. The repo is private, so the droplet needs a deploy key. Generate one
   there: `ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_telegrammanager -N
   ""`, then on GitHub go to the repo → **Settings → Deploy keys → Add
   deploy key**, paste `~/.ssh/id_ed25519_telegrammanager.pub` (read-only is
   enough).
2. Confirm the Spark is reachable over Tailscale before going further:

```bash
curl -s http://<spark-hostname>:8888/v1/models
```

   If that hangs or refuses, vLLM on the Spark is almost certainly bound to
   `localhost` only — publish the port on all interfaces (`-p 8888:8888`
   rather than `-p 127.0.0.1:8888:8888` if it runs in a container, or the
   equivalent host/PORT setting otherwise), restart the model service, and
   re-run the `curl`.
3. Clone and install:

```bash
git clone git@github.com:giopelagalli/telegramManager.git ~/telegramManager
cd ~/telegramManager
bash deploy/install.sh
```

This creates a venv, installs the package with the `voice` extra, creates
`knowledge/`, `data/`, `models/`, copies `.env.example` to `.env` if it
doesn't exist yet (mode `600`), and writes
`deploy/assistant.service.generated` with this user and path filled in.
`pip install -e '.[voice]'` still installs faster-whisper along with Kokoro,
but with `STT_PROVIDER=api` (§1.2) faster-whisper is never loaded — only
Kokoro (voice replies) runs on the droplet's CPU, which is why the 1 GB
droplet from the setup checklist is enough. Edit `.env` with the values from
§1.2 and §1.3, then run the `sudo` line the script prints to install and
start the `assistant` systemd unit.

For voice replies, also run `bash scripts/download_voice_models.sh` (see
§3) to fetch the Kokoro models onto the droplet.

### 1.5 Alternative: run everything on the Spark (free, but down when the Spark is down)

Use this instead of §1.1–§1.4 if you'd rather not pay for a droplet — at
the cost of the bot sharing the Spark's failure domain: no replies, no
reminders, nothing, whenever the Spark is down or rebooting.

- **Topology.** The Spark runs both the bot and the model as two systemd
  services side by side (the existing `sparkbot`/`sparkmodel`, plus a new
  `assistant`). The bot talks to vLLM at `OPENAI_BASE_URL=http://localhost:8888/v1`
  — no Tailscale hop, no network round trip, since both are on the same
  box. Memory is cheap here: the bot itself is ~200 MB and CPU Whisper adds
  ~1.5 GB, both drawn from the host's general reserve; neither ever touches
  the GPU, which stays fully committed to `sparkmodel`. Voice input runs
  locally too (`STT_PROVIDER=local`, faster-whisper) instead of through
  Fireworks.
- **Knowledge backups.** Only one remote is needed: a bare repo on the
  owner's Mac (§1.3 steps 3–5, skipping the Spark leg — the working
  `knowledge/` repo already lives on the Spark, so a second copy on the same
  box buys nothing).
  `KNOWLEDGE_REMOTES=<mac-user>@<mac-hostname>:backups/knowledge.git`.
- **`.env`.** Same table as §1.2, except: `OPENAI_BASE_URL=http://localhost:8888/v1`
  and `VISION_BASE_URL=http://localhost:8888/v1` (no Tailscale hop),
  `KNOWLEDGE_DIR`/`DATA_DIR`/`KOKORO_MODEL_DIR` under
  `/home/giospark1/telegramManager/...`, `STT_PROVIDER=local` with
  `WHISPER_MODEL` set to `base` or `small` depending on how many CPU cores
  the Spark's host reserve gives you (leave `STT_BASE_URL`/`STT_API_KEY`/
  `STT_MODEL` blank).
- **Install.** Same steps as §1.4, run on the Spark instead, as
  `giospark1`. The Tailscale/`curl` check in step 2 doesn't apply — vLLM is
  local. The unit is still named `assistant`, distinct from `sparkbot` and
  `sparkmodel`. `deploy/assistant.service` doesn't declare
  `After=sparkmodel.service`; the bot handles the model being unready fine
  (§5), but if you'd rather the unit not even start until `sparkmodel` is
  up, add that line to `deploy/assistant.service.generated` by hand before
  the `sudo` step, or to the installed unit file afterward followed by
  `sudo systemctl daemon-reload`. For voice, also run `bash
  scripts/download_voice_models.sh` (see §3); both Whisper and Kokoro run
  on the Spark's CPU, out of the host reserve mentioned above — never the
  GPU.

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

Critical mode (a `critical` event's leave time) is only useful if the message
actually reaches you. On the phone you carry:

- **Exempt the Telegram app (or at least this chat) from Focus/Do Not
  Disturb.** iOS: Settings → Focus → your active Focus → Apps → allow
  Telegram, or add the bot's chat as an allowed person under
  Notifications → per-app override. Android: Settings → Notifications → Do
  Not Disturb → Apps → allow Telegram.
- **Set a distinct, loud notification sound for this specific chat**, not
  the default Telegram tone — open the chat → chat settings/mute icon →
  notification sound, and pick something you won't miss.
- Turn notification banners/sound back to full volume before bed if your
  phone auto-lowers volume overnight.

## 5. Running it

The bot runs as the `assistant` systemd unit, installed in §1.4:

```bash
systemctl status assistant
journalctl -u assistant -f
sudo systemctl restart assistant   # after editing .env or pulling changes
```

`knowledge/` is initialized as a git repo on first run if it isn't one
already (see `bot/knowledge/store.py`).

**Warm-up.** vLLM (`sparkmodel`) takes about 10 minutes to load, whether
after a full Spark reboot or just its own service restarting. `assistant` on
the droplet keeps running throughout: the first chat request fails fast on
the connection timeout, the circuit breaker opens, and chat goes to
Fireworks for the next ten minutes before the Spark's own model is tried
again. (Running on the Spark instead, per §1.5, `assistant` shares its
failure domain and is down until systemd brings the whole box back up too —
see that section's honest-cost note.)

Only the chat model falls back. Vision (photo checks) degrades to "not
verified" while the Spark is unreachable and no `FALLBACK_VISION_MODEL` is
set; commands, reminders and briefings never need a model at all, but they
do need `assistant` itself to be running.

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

Everything happens in the bot's DM. Drop a PDF, a slide deck, a Word doc, a text file, or a photo of a page and it is stored under the course it belongs to (it infers the course from the content, creating one if needed, and tells you where it filed it — say "move that to Bio 201" if it guessed wrong). Ask a question about anything you have stored and it answers from your material with page citations. `/hard` before a question sends it to the big model.

Optional: if you want a separate chat per course, make a private group with Topics on, add the bot as admin, and name topics after courses ("CS101") or "Assignments", "Exams", "Review". Nothing requires this.

## 8. End-to-end checklist

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
