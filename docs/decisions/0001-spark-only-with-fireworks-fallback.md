# 0001 — JD runs on the Spark; Fireworks is fallback and /hard only
Date: 2026-09-18 (privacy revised 2026-09-21)
Decided by: owner
Status: accepted

## Context
JD first ran on a $6 DigitalOcean droplet with Fireworks as primary, the Spark to be wired later. The owner wanted everything private on the Spark, then weighed losing todos whenever the Spark or home wifi is down.

## Options
- A — droplet primary, Spark as model server over Tailscale (the earlier recommendation): survives Spark reboots, but the vault lives on a rented box.
- B — Spark only, no fallback: private, blind when the Spark is down.
- C (chosen) — Spark runs everything; Fireworks (DeepSeek V4.1 Flash or GLM 5.3 Flash only, cost) is the automatic fallback and the explicit `/hard`, with the "remote view": schedule, todos, goals, the day's conversation and matching memories, never the whole vault. Photos and voice wait for the Spark.

## Decision
C. The owner accepted Fireworks seeing conversation-level context (2026-09-21) but not the vault; the droplet was destroyed. JD announces "Spark's down" / "Spark's back".

## Consequences
One box, one failure domain; a Spark reload is a normal event. Fireworks billing must stay valid or the fallback does not exist. README sections about the droplet are historical.
