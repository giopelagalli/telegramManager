# 0008 — AgentHub is polled by the engine; planning runs in the background
Date: 2026-10-01
Decided by: senior-coder
Status: accepted

## Context
JD's project tools call routes that take minutes: a PRD draft or roadmap (`?wait=1`, up to 10 min)
and a turn (longer, keeps running when the client hangs up). Telegram updates are handled one at
a time, so a tool that waits on them freezes every other message. The hub has no push channel to JD.

## Options
- A — await the whole create → draft → roadmap inside the message: simplest, but JD goes silent for
  up to 20 minutes.
- B — a websocket or webhook from the hub: a second surface on both sides for one consumer.
- C (chosen) — create synchronously (fast; a bad slug or a duplicate answers at once), then draft and
  roadmap in an `asyncio` task whose result goes into `Projects.outbox`; turns are fired with a 10 s
  timeout and watched. The engine drains the outbox every tick and polls the hub once a minute
  (`/api/state`, `/api/briefings`, `/turns?since=` for watched slugs) and sends what lands.

## Decision
C. The context block renders from the last poll's snapshot, so `build_context` stays synchronous.
Watch and report bookkeeping lives in `RuntimeState.projects`, saved with the rest of the state.

## Consequences
A report arrives up to a minute after the turn lands. A planning task in flight is lost on restart
(the project exists; draft it from the hub UI). The label JD's turns carry is configuration
(`AGENTHUB_LABEL`, default `JD`) because no allowed route says which token is calling.
