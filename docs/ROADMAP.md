# Roadmap

Map, not manual. Updated at the end of every session. Details live in the decision files,
`docs/spark-setup.md` (the box) and `docs/plan-jd-web-agenthub.md` (the next build).

## Done

- JD runs on the DGX Spark (`assistant.service`) next to vLLM `:8888` and the voice server `:8890`; the DigitalOcean droplet is gone. Fireworks (DeepSeek V4.1 Flash) is the fallback and `/hard`, with the remote view (schedule, todos, goals, conversation, matching memories; never the whole vault).
- One chat does everything: todos, weekly schedule, goals, places and directions (Places New + Routes), live location, walk/drive modes, food log with 8 nutrients, generic trackers with reminders, notes scratchpad, search that reads pages and can act afterwards, `/queue` for Spark load.
- Memory: the two-day thread is the working context, nightly notes, transcript per day, day-based recall, semantic recall over the vault.
- Study: courses, sources (pdf/pptx/docx/text/code/photo), tutoring in course topics, exam plans with a 21-day horizon and a morning job, syllabus → course (meetings, exams, dated work).
- Files in the DM: code and text are read and discussed and stay in view for three hours; course material is filed; a syllabus builds its course.
- Proactive messaging, all text, no voice notes: morning briefing, three check-ins, evening wrap-up (a Todo/Done checklist), leave-by reminders with traffic, deadline reminders an hour and 30 minutes before a due time, tracker reminders, Sunday reflect, storm mode with Twilio calls.
- Button editors: `/schedule`, `/due`, `/todo`, `/courses`, `/notes`, `/calories`.
- Cluster design: vLLM priority scheduling so AgentHub agents queue behind JD; the 7900 XTX PC as worker and image/video node (plan only).

## In progress

- Nothing mid-flight. Last commits: reminder and wrap-up fixes (2026-09-23), the wrap-up as a checklist and no voice on proactive messages (2026-09-24).

## Next

Owner side, in order:
1. Pull and restart on the Spark (`cd ~/telegramManager && git pull -q && sudo systemctl restart assistant`).
2. Fix Fireworks billing (account suspended); until then there is no fallback.
3. Enable Google APIs: Places (New), Routes, Geocoding, Time Zone, Pollen.
4. `EXTRA_VLLM_ARGS="--scheduling-policy priority"` in the recipe `.env`, restart `sparkmodel`.
5. Plan phase 0: AgentHub on the Spark, `ant auth login`, droplet + Caddy for hub.rosenroot.com. Confirm plan decisions 1–2.
6. PC (7900 XTX) per AgentHub `deploy/amd/README.md`.

Code side:
- `rank_todos` puts priority before due date for future items, so a P1 study todo two weeks out outranks a P2 homework due tomorrow in the morning Top 5, `/todo` and the check-ins. The wrap-up sidesteps it; the ranking itself is worth a look.
- Plan phase 1: JD ↔ AgentHub tools and context; then connector layer + web API, call mode, Discord/email, self-improvement loop, image/video request tools.
- Optional: Fireworks Whisper as STT fallback while the Spark is down.
- README still describes the droplet topology in §1.5 and the remotes section; rewrite when the web door lands.

## Parked

- SM-2 review cards and the morning digest (built, unwired): the owner wants review as learn-by-doing modules on a platform later (`docs/ideas.md`).
