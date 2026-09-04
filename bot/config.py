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
    vision_base_url: str | None
    vision_model: str | None
    google_maps_api_key: str | None
    knowledge_dir: Path
    data_dir: Path

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
        return cls(
            telegram_bot_token=req("TELEGRAM_BOT_TOKEN"),
            telegram_user_id=int(req("TELEGRAM_USER_ID")),
            openai_base_url=req("OPENAI_BASE_URL"),
            openai_api_key=opt("OPENAI_API_KEY") or "unused",
            chat_model=req("CHAT_MODEL"),
            vision_base_url=opt("VISION_BASE_URL"),
            vision_model=opt("VISION_MODEL"),
            google_maps_api_key=opt("GOOGLE_MAPS_API_KEY"),
            knowledge_dir=Path(req("KNOWLEDGE_DIR")),
            data_dir=Path(req("DATA_DIR")),
        )
