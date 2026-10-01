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
A report arrives up to a minute after the turn lands. Worst case against a hub that hangs
(reads time out at 8 s; `/api/state` and `/api/briefings` are fetched together): a project tool
in a message waits ~8 s to resolve the name (only when it isn't in the last snapshot) plus 8 s for
the write (10 s for a turn) — about 18 s; `/projects` and each button ~8 s, because a write's
returned manifest updates the snapshot instead of re-reading the hub; a briefing waits at most
15 s for its Projects line (the `/turns` calls run together) and goes out without it after that;
an engine poll ~8 s plus 8 s per watched project. A turn POST that times out *reading* is a turn
running; one that times out connecting, writing or waiting for a pooled connection never reached
the hub and is an error. A planning task in flight is lost on restart
(the project exists; draft it from the hub UI). The label JD's turns carry is configuration
(`AGENTHUB_LABEL`, default `JD`) because no allowed route says which token is calling.
Project tool calls run in the router's general path, like `directions`: when the same model answer
also calls `study`, `coach`, `recall`, `briefing` or `search`, that path returns first and the
project call is not made (he says it again). Running them ahead would double-fire on `search`, whose
second capture can emit the same call again.
