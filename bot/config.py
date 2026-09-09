from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

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
    google_maps_api_key: str | None
    knowledge_dir: Path
    data_dir: Path
    chat_enable_thinking: bool
    whisper_model: str
    knowledge_remotes: list[str]

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        e = os.environ if env is None else env
        def req(k: str) -> str:
            v = e.get(k, "").strip()
            if not v:
                raise ValueError(f"missing required environment variable {k}")
            return v
        def opt(k: str) -> str | None:
            v = e.get(k, "").strip()
            return v or None
        fallback = {k: opt(k) for k in ("FALLBACK_BASE_URL", "FALLBACK_API_KEY", "FALLBACK_MODEL")}
        if any(fallback.values()) and not all(fallback.values()):
            raise ValueError("FALLBACK_BASE_URL, FALLBACK_API_KEY and FALLBACK_MODEL must be set together")
        fallback_vision_model = opt("FALLBACK_VISION_MODEL")
        if fallback_vision_model and not all(fallback.values()):
            raise ValueError("FALLBACK_VISION_MODEL requires FALLBACK_BASE_URL and FALLBACK_API_KEY")
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
            google_maps_api_key=opt("GOOGLE_MAPS_API_KEY"),
            knowledge_dir=Path(req("KNOWLEDGE_DIR")),
            data_dir=Path(req("DATA_DIR")),
            chat_enable_thinking=(e.get("CHAT_ENABLE_THINKING", "").strip().lower() in ("1", "true", "yes", "on")),
            whisper_model=opt("WHISPER_MODEL") or "small",
            knowledge_remotes=[r.strip() for r in e.get("KNOWLEDGE_REMOTES", "").split(",") if r.strip()],
        )
