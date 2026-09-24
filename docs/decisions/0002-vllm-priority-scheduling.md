# 0002 — One vLLM shared by priority, not a second model
Date: 2026-09-21
Decided by: orchestrator (owner agreed)
Status: accepted

## Context
AgentHub agents will land on the Spark. Memory allows exactly one vLLM (see `docs/spark-setup.md`); JD must stay responsive when agents queue work.

## Options
- A — a second, smaller model server for agents: does not fit in memory.
- B — a queue in JD or the hub that gates agent requests: extra code, still shares KV.
- C (chosen) — vLLM `--scheduling-policy priority`; JD sends no priority (0), agents send 10.

## Decision
C. AgentHub branch `feat/vllm-request-priority` adds `ServingEndpoint.priority`; `/queue` shows load.

## Consequences
When the KV budget is full JD still waits, just first. AgentHub `maxStreams` must be 2–3 here, not 48.
