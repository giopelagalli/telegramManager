"""The web door's HTTP API (AgentHub decision 0069), on aiohttp (0011).

Every route wants `Authorization: Bearer <JD_WEB_TOKEN>` (401 otherwise, compared in constant
time); bodies over 10 MB are 413. The hub proxies `/api/jd/*` here and is the only client.
"""
from __future__ import annotations

import hmac
import logging

from aiohttp import WSCloseCode, web

from bot.web.door import UnsupportedAudio

logger = logging.getLogger(__name__)

MAX_BODY = 10 * 1024 * 1024
HISTORY_DEFAULT = 50
DOOR = web.AppKey("door", object)
TOKEN = web.AppKey("token", bytes)


@web.middleware
async def _auth(request: web.Request, handler):
    given = request.headers.get("Authorization", "").encode()
    if not hmac.compare_digest(given, b"Bearer " + request.app[TOKEN]):
        return web.json_response({"error": "unauthorized"}, status=401)
    if (request.content_length or 0) > MAX_BODY:
        return web.json_response({"error": "too large"}, status=413)
    return await handler(request)


async def _json(request: web.Request, key: str) -> str:
    try:
        body = await request.json()
    except ValueError:
        raise web.HTTPBadRequest(text='{"error": "body is not JSON"}', content_type="application/json")
    value = body.get(key) if isinstance(body, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise web.HTTPBadRequest(text=f'{{"error": "{key} is required"}}', content_type="application/json")
    return value


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "name": request.app[DOOR].name()})


async def history(request: web.Request) -> web.Response:
    door = request.app[DOOR]
    try:
        limit = int(request.query.get("limit", HISTORY_DEFAULT))
    except ValueError:
        limit = HISTORY_DEFAULT
    limit = max(1, min(limit, door.conversation.cap))
    return web.json_response({"messages": door.conversation.history(limit)})


async def messages(request: web.Request) -> web.Response:
    text = await _json(request, "text")
    return web.json_response({"messages": await request.app[DOOR].message(text)})


async def callback(request: web.Request) -> web.Response:
    data = await _json(request, "data")
    return web.json_response({"messages": await request.app[DOOR].callback(data)})


async def voice(request: web.Request) -> web.Response:
    audio = await request.read()
    if not audio:
        return web.json_response({"error": "no audio"}, status=400)
    try:
        transcript, replies = await request.app[DOOR].voice(audio, request.headers.get("Content-Type", ""))
    except UnsupportedAudio:
        return web.json_response({"error": "send audio/webm, audio/mp4 or audio/ogg"}, status=415)
    return web.json_response({"transcript": transcript, "messages": replies})


async def audio(request: web.Request) -> web.StreamResponse:
    store = request.app[DOOR].conversation.audio
    path = store.path(request.match_info["id"]) if store is not None else None
    if path is None:
        return web.json_response({"error": "no such audio"}, status=404)
    return web.FileResponse(path, headers={"Content-Type": "audio/mp4"})


async def keys(request: web.Request) -> web.Response:
    return web.json_response({"keys": request.app[DOOR].keys})


async def stream(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    streams = request.app[DOOR].conversation.streams
    streams.add(ws)
    try:
        async for _ in ws:
            pass  # the browser only listens; anything it says is ignored
    finally:
        streams.discard(ws)
    return ws


async def _close_streams(app: web.Application) -> None:
    for ws in list(app[DOOR].conversation.streams):
        await ws.close(code=WSCloseCode.GOING_AWAY, message=b"JD is restarting")


def build_app(door, token: str) -> web.Application:
    app = web.Application(middlewares=[_auth], client_max_size=MAX_BODY)
    app[DOOR] = door
    app[TOKEN] = token.encode()
    app.router.add_get("/health", health)
    app.router.add_get("/history", history)
    app.router.add_post("/messages", messages)
    app.router.add_post("/callback", callback)
    app.router.add_post("/voice", voice)
    app.router.add_get("/audio/{id}", audio)
    app.router.add_get("/keys", keys)
    app.router.add_get("/stream", stream)
    app.on_shutdown.append(_close_streams)
    return app


async def start(app: web.Application, host: str, port: int) -> web.AppRunner:
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        await web.TCPSite(runner, host, port).start()
    except BaseException:
        await runner.cleanup()
        raise
    logger.info("web door listening on %s:%s", host, port)
    return runner
