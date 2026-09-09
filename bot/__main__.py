from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from bot.agent.agent import Agent
from bot.agent.client import FallbackModelClient, ModelClient, OpenAIModelClient
from bot.config import Settings
from bot.knowledge.store import KnowledgeStore
from bot.maps.client import MapsClient
from bot.scheduler.clock import SystemClock
from bot.scheduler.engine import Engine
from bot.scheduler.state import RuntimeState
from bot.telegram.app import build_application
from bot.telegram.router import Router
from bot.telegram.sender import Sender, channel_resolver
from bot.voice import stt, tts

logger = logging.getLogger(__name__)

KOKORO_MODEL = "kokoro-v1.0.onnx"
KOKORO_VOICES = "voices-v1.0.bin"


def _synthesizer() -> tts.Synthesizer | None:
    model_dir = Path(os.environ.get("KOKORO_MODEL_DIR", "models"))
    model_path = model_dir / KOKORO_MODEL
    voices_path = model_dir / KOKORO_VOICES
    if not tts.available(model_path, voices_path):
        logger.warning("voice output off: kokoro not installed or models missing in %s", model_dir)
        return None
    return tts.Synthesizer(model_path, voices_path)


def _transcriber(model_size: str) -> stt.Transcriber | None:
    if not stt.available():
        logger.warning("voice input off: faster-whisper not installed")
        return None
    return stt.Transcriber(model_size=model_size)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings.from_env()

    # The clock needs the profile's timezone, which lives in the store; the store
    # only calls its clock after init(), so bind it lazily.
    store = KnowledgeStore(
        settings.knowledge_dir,
        clock=lambda: clock.now(),
        remotes=settings.knowledge_remotes,
    )
    store.init()
    clock = SystemClock(store.profile().tz)

    state_path = settings.data_dir / "state.json"
    state = RuntimeState.load(state_path)

    client: ModelClient = OpenAIModelClient(
        settings.openai_base_url,
        settings.openai_api_key,
        settings.chat_model,
        enable_thinking=settings.chat_enable_thinking,
    )
    if settings.fallback_base_url:
        client = FallbackModelClient(
            client,
            OpenAIModelClient(
                settings.fallback_base_url,
                settings.fallback_api_key,
                settings.fallback_model,
                enable_thinking=None,
            ),
        )
    vision: ModelClient | None = (
        OpenAIModelClient(
            settings.vision_base_url or settings.openai_base_url,
            settings.openai_api_key,
            settings.vision_model,
            enable_thinking=settings.chat_enable_thinking,
        )
        if settings.vision_model
        else None
    )
    if vision is not None and settings.fallback_vision_model:
        vision = FallbackModelClient(
            vision,
            OpenAIModelClient(
                settings.fallback_base_url,
                settings.fallback_api_key,
                settings.fallback_vision_model,
                enable_thinking=None,
            ),
        )
    agent = Agent(client, vision, store, clock.now)
    maps = MapsClient(settings.google_maps_api_key) if settings.google_maps_api_key else None

    sender = Sender(
        None,
        settings.telegram_user_id,
        _synthesizer(),
        settings.data_dir / "tmp",
        resolve=channel_resolver(store, settings.telegram_user_id),
    )
    router = Router(store, agent, state, clock, maps)
    engine = Engine(store, agent, state, state_path, clock, sender, maps)

    application = build_application(settings, router, sender, _transcriber(settings.whisper_model))
    sender.bot = application.bot

    set_my_commands = application.post_init
    tasks: set[asyncio.Task] = set()

    async def post_init(app) -> None:
        await set_my_commands(app)
        engine.startup(clock.now())
        task = asyncio.create_task(engine.run())
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    application.post_init = post_init
    application.run_polling(allowed_updates=["message", "edited_message", "callback_query"])


if __name__ == "__main__":
    main()
