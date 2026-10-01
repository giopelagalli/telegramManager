# 0011 — aiohttp serves the web door
Date: 2026-10-01
Decided by: senior-coder
Status: accepted

## Context
The web door is JD's first listener: eight HTTP routes and a websocket (`/stream`), inside the
asyncio loop python-telegram-bot already runs. JD's dependencies have no server: httpx is a
client, h11 has no websocket, and FastAPI/uvicorn live only in the Spark voice server's own venv.

## Options
- A — hand-rolled on asyncio + h11: websocket framing and body limits by hand; no.
- B — Starlette + uvicorn (+ a websocket lib): three packages, and uvicorn wants to own the loop
  that PTB's `run_polling` already owns.
- C — tornado (PTB's optional webhook extra): heavier API, not installed either.
- D (chosen) — aiohttp: one package with server, websockets, a body cap (`client_max_size`)
  and a test client; its `AppRunner` starts inside PTB's `post_init` on the existing loop.

## Decision
`aiohttp>=3.9,<4` is a core dependency, imported only when `JD_WEB_TOKEN` is set. The server
starts in `post_init`, stops in `post_shutdown` (open streams closed with "going away").

## Consequences
- One more dependency to keep current; it also pulls in its small helpers (yarl, multidict,
  frozenlist, aiosignal, propcache, aiohappyeyeballs).
- Deploying this needs one `pip install -e .` on the Spark; a plain `git pull` is not enough.
