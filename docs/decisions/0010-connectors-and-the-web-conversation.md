# 0010 — Connectors: Telegram is one, the web another; proactive messages fan out
Date: 2026-10-01
Decided by: senior-coder (within AgentHub's 0069 and the plan's phase 2 "Connector layer")
Status: accepted

## Context
The web door (AgentHub `docs/decisions/0069-the-jd-web-door-contract.md`) needs JD's output on a
second surface without forking the router or changing a byte of Telegram behaviour. JD's output
already lives on `Outbound` (text, buttons, voice, edits); it leaves through two places only:
`Handlers` (replies) and `Engine._send` (everything proactive, project reports included).

## Options
- A — a router-level "reply to" argument so the router knows the surface: touches every UI and
  the router's signatures; the router would stop being surface-free.
- B — rewrite `Sender` into a generic dispatcher with Telegram and web backends: rewrites
  tested Telegram code for no gain.
- C (chosen) — `Connector` = anything with `async send(out)`. `Sender` is the Telegram connector
  unchanged; `WebConversation` is the web one. Replies go back where the message came in
  (`Handlers` → Telegram, `WebDoor` → the HTTP response); the engine gets a `Fanout(sender, web)`.

## Decision
`bot/connectors.py` holds the protocol and `Fanout`: Telegram first, exactly as before (its
errors still reach the engine); then the web, whose failures are logged and swallowed. The web
connector delivers only while a browser holds a `/stream` open, and records what it delivers in
the web conversation (`DATA_DIR/web.json`, last 200 messages, written on every change; its own
file rather than `RuntimeState`, which the engine rewrites every tick). Web message ids are a
persisted counter, so `Outbound.edit_message_id` (an int) addresses a web message the same way it
addresses a Telegram one, and a web button tap finds its message by the newest one carrying that
`data`. Web requests are served one at a time (a lock in `WebDoor`), since the router owns shared
state. Telegram HTML maps onto the contract's subset (`bot/web/format.py`); what Telegram would
refuse goes out `plain`, the sender's own fallback.

## Consequences
- Telegram and the web are two views of one JD: a web exchange is in the conversation memory but
  not in the Telegram chat; a proactive message is in both only while a browser is attached.
- `Outbound.toast` and `location_button` have no web equivalent in 0069 and are dropped there.
- A proactive voice note is synthesized twice (once per connector) while a browser is attached.
- A new connector (Discord, email; plan phase 4) is one `send` and one entry in the fan-out.
