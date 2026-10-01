from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from bot.agent.agent import Agent
from bot.agenthub.client import AgentHubClient
from bot.agenthub.projects import Projects
from bot.agent.client import FallbackModelClient, ModelClient, OpenAIModelClient
from bot.cluster import AgentHubStatus, ClusterStatus, SparkStatus
from bot.config import Settings
from bot.connectors import Fanout
from bot.knowledge.models import Channel
from bot.memory.index import Embedder, VectorIndex
from bot.search import BraveSearch
from bot.scheduler import briefings
from bot.weather import MorningWeather
from bot.knowledge.store import KnowledgeStore
from bot.maps.client import MapsClient
from bot.scheduler.clock import SystemClock
from bot.scheduler.engine import Engine
from bot.scheduler.state import RuntimeState
from bot.telegram.app import build_application
from bot.telegram.router import Router
from bot.telegram.sender import Sender, channel_resolver
from bot.voice import stt, tts
from bot.voice.call import TwilioCaller

logger = logging.getLogger(__name__)

KOKORO_MODEL = "kokoro-v1.0.onnx"
KOKORO_VOICES = "voices-v1.0.bin"


def _synthesizer(settings: Settings) -> tts.Synthesizer | tts.ApiSynthesizer | None:
    if settings.tts_api_key:
        return tts.ApiSynthesizer(settings.tts_base_url, settings.tts_api_key, settings.tts_model,
                                  settings.tts_voice, settings.tts_instructions)
    model_dir = settings.kokoro_model_dir
    model_path = model_dir / KOKORO_MODEL
    voices_path = model_dir / KOKORO_VOICES
    if not tts.available(model_path, voices_path):
        logger.warning("voice output off: kokoro not installed or models missing in %s", model_dir)
        return None
    return tts.Synthesizer(model_path, voices_path)


def _transcriber(settings: Settings) -> stt.Transcriber | stt.ApiTranscriber | None:
    if settings.stt_provider == "api":
        return stt.ApiTranscriber(settings.stt_base_url, settings.stt_api_key, settings.stt_model)
    if not stt.available():
        logger.warning("voice input off: faster-whisper not installed")
        return None
    return stt.Transcriber(model_size=settings.whisper_model)


def _web_door(settings: Settings, store, router, synthesizer, transcriber):
    """The web door AgentHub proxies (0010, 0011): off unless JD_WEB_TOKEN is set."""
    if not settings.jd_web_token:
        return None
    from bot.web.audio import AudioStore
    from bot.web.conversation import WebConversation
    from bot.web.door import WebDoor

    tmp_dir = settings.data_dir / "tmp"
    conversation = WebConversation(
        settings.data_dir / "web.json", AudioStore(settings.data_dir / "web-audio"), synthesizer, tmp_dir
    )
    life = Channel(settings.telegram_user_id, None, "life")  # the web is the DM, never a topic
    return WebDoor(router, conversation, life, transcriber, tmp_dir, name=lambda: store.profile().assistant_name)


async def _open_web_door(door, settings: Settings):
    """Start the web door; a busy port is logged and Telegram carries on without it."""
    from bot.web.server import build_app, start

    try:
        return await start(build_app(door, settings.jd_web_token), settings.jd_web_host, settings.jd_web_port)
    except OSError as exc:
        logger.error("web door off: cannot listen on %s:%s (%s)", settings.jd_web_host, settings.jd_web_port, exc)
        return None


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # request URLs contain the bot token
    settings = Settings.from_env()

    # The clock needs the profile's timezone, which lives in the store; the store
    # only calls its clock after init(), so bind it lazily.
    store = KnowledgeStore(
        settings.knowledge_dir,
        clock=lambda: clock.now(),
        remotes=settings.knowledge_remotes,
    )
    store.init()
    profile = store.profile()
    clock = SystemClock(profile.tz)

    state_path = settings.data_dir / "state.json"
    state = RuntimeState.load(state_path)

    client: ModelClient = OpenAIModelClient(
        settings.openai_base_url,
        settings.openai_api_key,
        settings.chat_model,
        # The backend either has a thinking switch (Qwen on vLLM) or it doesn't; when it does,
        # the profile decides, so /think on|off is the one control.
        enable_thinking=profile.thinking if settings.chat_enable_thinking is not None else None,
        extra_body=settings.chat_extra_body,
    )
    if settings.fallback_model:
        client = FallbackModelClient(
            client,
            OpenAIModelClient(
                settings.fallback_base_url,
                settings.fallback_api_key,
                settings.fallback_model,
                enable_thinking=None,
                extra_body=settings.fallback_extra_body,
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
                extra_body=settings.fallback_vision_extra_body,
            ),
        )
    hard: ModelClient | None = None
    if settings.hard_model:
        hard = OpenAIModelClient(
            settings.hard_base_url,
            settings.hard_api_key,
            settings.hard_model,
            enable_thinking=settings.hard_thinking,
            extra_body=settings.hard_extra_body,
            timeout=300,
        )
    search = BraveSearch(settings.brave_api_key) if settings.brave_api_key else None
    briefings.WEATHER = MorningWeather(settings.google_maps_api_key)
    projects = (
        Projects(AgentHubClient(settings.agenthub_url, settings.agenthub_token), settings.agenthub_label)
        if settings.agenthub_url and settings.agenthub_token else None
    )
    briefings.PROJECTS = projects
    agent = Agent(client, vision, store, clock.now, hard=hard, search=search is not None, projects=projects is not None)
    # /hard on another provider than the primary only gets the minimal view
    agent.hard_remote = bool(settings.hard_model) and settings.hard_base_url != settings.openai_base_url
    if settings.fallback_model:
        # /hard is the explicit opt-in to the cloud model; everything automatic stays on the primary
        agent.cloud = OpenAIModelClient(
            settings.fallback_base_url, settings.fallback_api_key, settings.fallback_model,
            enable_thinking=None, extra_body=settings.fallback_extra_body, timeout=300,
        )
    maps = MapsClient(settings.google_maps_api_key) if settings.google_maps_api_key else None

    synthesizer = _synthesizer(settings)
    transcriber = _transcriber(settings)
    sender = Sender(
        None,
        settings.telegram_user_id,
        synthesizer,
        settings.data_dir / "tmp",
        resolve=channel_resolver(store, settings.telegram_user_id),
        caller=(
            TwilioCaller(settings.twilio_account_sid, settings.twilio_auth_token, settings.twilio_from, settings.phone)
            if settings.twilio_account_sid else None
        ),
    )
    index = None
    if settings.embed_model and settings.embed_base_url and settings.embed_api_key:
        index = VectorIndex(settings.data_dir / "index.json",
                            Embedder(settings.embed_base_url, settings.embed_api_key, settings.embed_model))
    cluster = ClusterStatus(
        spark=SparkStatus(settings.openai_base_url) if settings.fallback_model or "localhost" in settings.openai_base_url else None,
        hub=AgentHubStatus(settings.agenthub_url, settings.agenthub_password, token=settings.agenthub_token)
        if settings.agenthub_url else None,
    )
    router = Router(store, agent, state, clock, maps, search=search, index=index, cluster=cluster, projects=projects)
    door = _web_door(settings, store, router, synthesizer, transcriber)
    # Proactive messages reach every connector; replies go back where the message came in (0010).
    proactive = Fanout(sender, door.conversation) if door is not None else sender
    engine = Engine(store, agent, state, state_path, clock, proactive, maps, search=search, projects=projects)

    application = build_application(settings, router, sender, transcriber)
    sender.bot = application.bot

    set_my_commands = application.post_init
    tasks: set[asyncio.Task] = set()

    async def post_init(app) -> None:
        await set_my_commands(app)
        engine.startup(clock.now())
        task = asyncio.create_task(engine.run())
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        if door is not None:
            runner = await _open_web_door(door, settings)
            if runner is not None:
                web_runner.append(runner)

    async def post_shutdown(app) -> None:
        for runner in web_runner:
            await runner.cleanup()

    web_runner: list = []
    application.post_init = post_init
    application.post_shutdown = post_shutdown
    application.run_polling(allowed_updates=["message", "edited_message", "callback_query"])


if __name__ == "__main__":
    main()
