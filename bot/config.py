from __future__ import annotations
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

FIREWORKS_URL = "https://api.fireworks.ai/inference/v1"
FIREWORKS_AUDIO_URL = "https://audio-prod.us-virginia-1.direct.fireworks.ai/v1"
FIREWORKS_MODEL_DEFAULT = "accounts/fireworks/models/deepseek-v4p1-flash"
FIREWORKS_STT_DEFAULT = "whisper-v3"
SPARK_EMBED_DEFAULT = "nomic-ai/nomic-embed-text-v1.5"
OPENAI_TTS_URL = "https://api.openai.com/v1"
OPENAI_TTS_MODEL = "gpt-4o-mini-tts"
OPENAI_TTS_VOICE = "onyx"
TTS_INSTRUCTIONS_DEFAULT = (
    "An older guy talking to a younger friend on the phone. Low, dry, unhurried, a little "
    "amused. No announcer energy, no upbeat customer-service tone. Plain and direct."
)
FIREWORKS_EMBED_DEFAULT = "nomic-ai/nomic-embed-text-v1.5"
SPARK_MODEL_DEFAULT = "qwen3.8-flash-next"
REPO_ROOT = Path(__file__).resolve().parent.parent


def _strip_comment(v: str) -> str:
    return re.sub(r"\s+#.*$", "", v).strip()


def _derive(e: Mapping[str, str]) -> dict[str, str]:
    """Fill the advanced variables from the four simple ones unless set explicitly."""
    # systemd's EnvironmentFile keeps inline "# comments" in the value; strip them here
    d = {k: _strip_comment(str(v)) for k, v in e.items()}
    d = {k: v for k, v in d.items() if v}
    def default(k: str, v: str | None) -> None:
        if v and k not in d:
            d[k] = v
    key = d.get("FIREWORKS_API_KEY", "").strip()
    fw_model = d.get("FIREWORKS_MODEL", "").strip() or FIREWORKS_MODEL_DEFAULT
    spark = d.get("SPARK_URL", "").strip()
    spark_model = d.get("SPARK_MODEL", "").strip() or SPARK_MODEL_DEFAULT
    voice = d.get("SPARK_VOICE_URL", "").strip()
    if spark:
        # Spark first. Fireworks is the fallback for chat and photos; voice notes and the memory
        # index stay on the Spark's voice server and are simply off without it.
        default("OPENAI_BASE_URL", spark); default("OPENAI_API_KEY", "unused")
        default("CHAT_MODEL", spark_model); default("CHAT_ENABLE_THINKING", "true")
        default("VISION_BASE_URL", spark); default("VISION_MODEL", spark_model)
        # "hard" (coaching, exam plans, /hard) is the Spark thinking harder, never the cloud
        default("HARD_BASE_URL", spark); default("HARD_API_KEY", "unused")
        default("HARD_MODEL", spark_model); default("HARD_THINKING", "true")
        if key:
            default("FALLBACK_MODEL", fw_model); default("FALLBACK_VISION_MODEL", fw_model)
    elif key:
        default("OPENAI_BASE_URL", FIREWORKS_URL); default("OPENAI_API_KEY", key)
        default("CHAT_MODEL", fw_model)
        default("VISION_BASE_URL", FIREWORKS_URL); default("VISION_MODEL", fw_model)
        default("HARD_BASE_URL", FIREWORKS_URL); default("HARD_API_KEY", key); default("HARD_MODEL", fw_model)
        default("EMBED_BASE_URL", FIREWORKS_URL); default("EMBED_API_KEY", key)
        default("EMBED_MODEL", FIREWORKS_EMBED_DEFAULT)
        if "STT_PROVIDER" not in d:
            d["STT_PROVIDER"] = "api"
            default("STT_BASE_URL", FIREWORKS_AUDIO_URL); default("STT_API_KEY", key)
            default("STT_MODEL", d.get("FIREWORKS_STT_MODEL", "").strip() or FIREWORKS_STT_DEFAULT)
    if key:
        default("FALLBACK_BASE_URL", FIREWORKS_URL); default("FALLBACK_API_KEY", key)
    if voice:
        # The Spark's voice server (spark/voice_server.py): whisper in, speech out, embeddings.
        if "STT_PROVIDER" not in d:
            d["STT_PROVIDER"] = "api"
        default("STT_BASE_URL", voice); default("STT_API_KEY", "unused"); default("STT_MODEL", "whisper")
        default("TTS_API_KEY", "unused"); default("TTS_BASE_URL", voice)
        default("TTS_MODEL", "kokoro"); default("TTS_VOICE", "am_onyx"); default("TTS_INSTRUCTIONS", "")
        default("EMBED_BASE_URL", voice); default("EMBED_API_KEY", "unused"); default("EMBED_MODEL", SPARK_EMBED_DEFAULT)
    if d.get("TTS_API_KEY", "").strip():
        default("TTS_BASE_URL", OPENAI_TTS_URL); default("TTS_MODEL", OPENAI_TTS_MODEL)
        default("TTS_VOICE", OPENAI_TTS_VOICE); default("TTS_INSTRUCTIONS", TTS_INSTRUCTIONS_DEFAULT)
    default("KNOWLEDGE_DIR", str(REPO_ROOT / "knowledge"))
    default("DATA_DIR", str(REPO_ROOT / "data"))
    default("KOKORO_MODEL_DIR", str(REPO_ROOT / "models"))
    if "OPENAI_BASE_URL" not in d:
        raise ValueError("set FIREWORKS_API_KEY and/or SPARK_URL (or the advanced OPENAI_* variables)")
    return d


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_user_id: int
    openai_base_url: str
    openai_api_key: str
    chat_model: str
    fallback_base_url: str | None
    fallback_api_key: str | None
    fallback_model: str | None
    vision_base_url: str | None
    vision_model: str | None
    fallback_vision_model: str | None
    hard_model: str | None
    hard_base_url: str | None
    hard_api_key: str | None
    hard_thinking: bool | None
    chat_extra_body: dict | None
    fallback_extra_body: dict | None
    hard_extra_body: dict | None
    fallback_vision_extra_body: dict | None
    google_maps_api_key: str | None
    knowledge_dir: Path
    data_dir: Path
    chat_enable_thinking: bool | None
    whisper_model: str
    knowledge_remotes: list[str]
    stt_provider: str
    stt_base_url: str | None
    stt_api_key: str | None
    stt_model: str | None
    kokoro_model_dir: Path
    brave_api_key: str | None
    embed_model: str | None
    embed_base_url: str | None
    embed_api_key: str | None
    tts_api_key: str | None
    tts_base_url: str | None
    tts_model: str | None
    tts_voice: str | None
    tts_instructions: str | None
    agenthub_url: str | None
    agenthub_password: str | None
    agenthub_token: str | None  # an `assistant` API token (ah_…): the project tools and /projects
    agenthub_label: str  # that token's label on the hub; turns it starts carry it as requestedBy
    twilio_account_sid: str | None
    twilio_auth_token: str | None
    twilio_from: str | None
    phone: str | None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        e = _derive(os.environ if env is None else env)
        def req(k: str) -> str:
            v = e.get(k, "").strip()
            if not v:
                raise ValueError(f"missing required environment variable {k}")
            return v
        def opt(k: str) -> str | None:
            v = e.get(k, "").strip()
            return v or None
        def json_obj(k: str) -> dict | None:
            v = e.get(k, "").strip()
            if not v:
                return None
            try:
                parsed = json.loads(v)
            except json.JSONDecodeError:
                raise ValueError(f"{k} must be a JSON object")
            if not isinstance(parsed, dict):
                raise ValueError(f"{k} must be a JSON object")
            return parsed
        fallback = {k: opt(k) for k in ("FALLBACK_BASE_URL", "FALLBACK_API_KEY", "FALLBACK_MODEL")}
        if fallback["FALLBACK_MODEL"] and not (fallback["FALLBACK_BASE_URL"] and fallback["FALLBACK_API_KEY"]):
            raise ValueError("FALLBACK_BASE_URL, FALLBACK_API_KEY and FALLBACK_MODEL must be set together")
        if not fallback["FALLBACK_MODEL"]:
            fallback = {k: (v if k != "FALLBACK_MODEL" else None) for k, v in fallback.items()}
        fallback_vision_model = opt("FALLBACK_VISION_MODEL")
        if fallback_vision_model and not (fallback["FALLBACK_BASE_URL"] and fallback["FALLBACK_API_KEY"]):
            raise ValueError("FALLBACK_VISION_MODEL requires FALLBACK_BASE_URL and FALLBACK_API_KEY")
        hard_model = opt("HARD_MODEL")
        hard_base_url = opt("HARD_BASE_URL") or fallback["FALLBACK_BASE_URL"]
        hard_api_key = opt("HARD_API_KEY") or fallback["FALLBACK_API_KEY"]
        if hard_model and not (hard_base_url and hard_api_key):
            raise ValueError("HARD_MODEL requires FALLBACK_BASE_URL and FALLBACK_API_KEY")
        stt_provider = opt("STT_PROVIDER") or "local"
        stt_base_url = opt("STT_BASE_URL")
        stt_api_key = opt("STT_API_KEY")
        stt_model = opt("STT_MODEL")
        if stt_provider == "api" and not (stt_base_url and stt_api_key and stt_model):
            raise ValueError("STT_PROVIDER=api requires STT_BASE_URL, STT_API_KEY and STT_MODEL")
        twilio = {k: opt(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM", "PHONE")}
        if any(twilio.values()) and not all(twilio.values()):
            raise ValueError("calls need all four of TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM and PHONE")
        return cls(
            telegram_bot_token=req("TELEGRAM_BOT_TOKEN"),
            telegram_user_id=int(req("TELEGRAM_USER_ID")),
            openai_base_url=req("OPENAI_BASE_URL"),
            openai_api_key=opt("OPENAI_API_KEY") or "unused",
            chat_model=req("CHAT_MODEL"),
            fallback_base_url=fallback["FALLBACK_BASE_URL"],
            fallback_api_key=fallback["FALLBACK_API_KEY"],
            fallback_model=fallback["FALLBACK_MODEL"],
            vision_base_url=opt("VISION_BASE_URL"),
            vision_model=opt("VISION_MODEL"),
            fallback_vision_model=fallback_vision_model,
            hard_model=hard_model,
            hard_base_url=hard_base_url if hard_model else None,
            hard_api_key=hard_api_key if hard_model else None,
            hard_thinking=_tristate(e.get("HARD_THINKING", "")),
            chat_extra_body=json_obj("CHAT_EXTRA_BODY"),
            fallback_extra_body=json_obj("FALLBACK_EXTRA_BODY"),
            hard_extra_body=json_obj("HARD_EXTRA_BODY"),
            fallback_vision_extra_body=json_obj("FALLBACK_VISION_EXTRA_BODY"),
            google_maps_api_key=opt("GOOGLE_MAPS_API_KEY"),
            knowledge_dir=Path(req("KNOWLEDGE_DIR")),
            data_dir=Path(req("DATA_DIR")),
            chat_enable_thinking=_tristate(e.get("CHAT_ENABLE_THINKING", "")),
            whisper_model=opt("WHISPER_MODEL") or "small",
            knowledge_remotes=[r.strip() for r in e.get("KNOWLEDGE_REMOTES", "").split(",") if r.strip()],
            stt_provider=stt_provider,
            stt_base_url=stt_base_url,
            stt_api_key=stt_api_key,
            stt_model=stt_model,
            kokoro_model_dir=Path(req("KOKORO_MODEL_DIR")),
            brave_api_key=opt("BRAVE_API_KEY"),
            embed_model=opt("EMBED_MODEL"),
            embed_base_url=opt("EMBED_BASE_URL"),
            embed_api_key=opt("EMBED_API_KEY"),
            tts_api_key=opt("TTS_API_KEY"),
            tts_base_url=opt("TTS_BASE_URL"),
            tts_model=opt("TTS_MODEL"),
            tts_voice=opt("TTS_VOICE"),
            tts_instructions=opt("TTS_INSTRUCTIONS"),
            agenthub_url=opt("AGENTHUB_URL"),
            agenthub_password=opt("AGENTHUB_PASSWORD"),
            agenthub_token=opt("AGENTHUB_TOKEN"),
            agenthub_label=opt("AGENTHUB_LABEL") or "JD",
            twilio_account_sid=twilio["TWILIO_ACCOUNT_SID"],
            twilio_auth_token=twilio["TWILIO_AUTH_TOKEN"],
            twilio_from=twilio["TWILIO_FROM"],
            phone=twilio["PHONE"],
        )


def _tristate(raw: str) -> bool | None:
    """Blank means "don't send the flag at all" (non-Qwen backends)."""
    v = raw.strip().lower()
    if not v:
        return None
    return v in ("1", "true", "yes", "on")
