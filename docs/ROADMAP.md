# Roadmap

Map, not manual. Updated at the end of every session. Details live in the decision files,
`docs/spark-setup.md` (the box) and `docs/plan-jd-web-agenthub.md` (the next build).

## Done

- JD runs on the DGX Spark (`assistant.service`) next to vLLM `:8888` and the voice server `:8890`; the DigitalOcean droplet is gone. Fireworks (DeepSeek V4.1 Flash) is the fallback and `/hard`, with the remote view (schedule, todos, goals, conversation, matching memories; never the whole vault).
- One chat does everything: todos, weekly schedule, goals, places and directions (Places New + Routes), live location, walk/drive modes, food log with 8 nutrients, generic trackers with reminders, notes scratchpad, search that reads pages and can act afterwards, `/queue` for Spark load.
- Memory: the two-day thread is the working context, nightly notes, transcript per day, day-based recall, semantic recall over the vault.
- Study: courses, sources (pdf/pptx/docx/text/code/photo), tutoring in course topics, exam plans with a 21-day horizon and a morning job, syllabus → course (meetings, exams, dated work).
- Files in the DM: code and text are read and discussed and stay in view for three hours; course material is filed; a syllabus builds its course.
- Proactive messaging: morning briefing, three text-only check-ins, evening wrap-up (deterministic), leave-by reminders with traffic, deadline reminders an hour and 30 minutes before a due time, tracker reminders, Sunday reflect, storm mode with Twilio calls.
- Button editors: `/schedule`, `/due`, `/todo`, `/courses`, `/notes`, `/calories`.
- AgentHub phase 1 (branch `agenthub-phase1`): `project_*` tools, the Projects context block, `/projects`, turn reports, briefing roll-up, blocked/failed alerts (0008, 0009).
- The web door (branch `jd-web-door`, on top of `agenthub-phase1`): connector layer with proactive fan-out, the AgentHub 0069 API on aiohttp (`:8891`, bearer), web voice notes in and `.m4a` out, persisted web conversation (0010, 0011).
- Cluster design: vLLM priority scheduling so AgentHub agents queue behind JD; the 7900 XTX PC as worker and image/video node (plan only).

## In progress

- The web door waits on the owner: deploy `jd-web-door` (after phase 1), `pip install -e .` once for aiohttp, `openssl rand -hex 32` on the Spark into both `.env`s as `JD_WEB_TOKEN` (hub also `JD_URL=http://127.0.0.1:8891`), restart both, run the plan's phase 2 acceptance from the hub's JD page.
- AgentHub phase 1 waits on the owner: mint an assistant token (label `JD`), set `AGENTHUB_URL` / `AGENTHUB_TOKEN`, deploy the branch, run the plan's acceptance (start a project from Telegram, see it in the hub, one message when its turn lands, see it in the next briefing).

## Next

Owner side, in order:
1. Pull and restart on the Spark (`cd ~/telegramManager && git pull -q && sudo systemctl restart assistant`).
2. Fix Fireworks billing (account suspended); until then there is no fallback.
3. Enable Google APIs: Places (New), Routes, Geocoding, Time Zone, Pollen.
4. `EXTRA_VLLM_ARGS="--scheduling-policy priority"` in the recipe `.env`, restart `sparkmodel`.
5. Plan phase 0: AgentHub on the Spark, `ant auth login`, droplet + Caddy for hub.rosenroot.com. Confirm plan decisions 1–2.
6. PC (7900 XTX) per AgentHub `deploy/amd/README.md`.

Code side:
- Plan phase 3+: call mode, Discord/email connectors (one `send` each, 0010), `POST /photo` on the web door, self-improvement loop, image/video request tools.
- Optional: Fireworks Whisper as STT fallback while the Spark is down.
- README still describes the droplet topology in §1.5 and the remotes section; rewrite when the web door lands.

## Parked

- SM-2 review cards and the morning digest (built, unwired): the owner wants review as learn-by-doing modules on a platform later (`docs/ideas.md`).
