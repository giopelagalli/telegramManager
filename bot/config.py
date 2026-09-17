from __future__ import annotations
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

FIREWORKS_URL = "https://api.fireworks.ai/inference/v1"
FIREWORKS_MODEL_DEFAULT = "accounts/fireworks/models/deepseek-v4p1-flash"
FIREWORKS_STT_DEFAULT = "whisper-v3"
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
    if spark:
        default("OPENAI_BASE_URL", spark); default("OPENAI_API_KEY", "unused")
        default("CHAT_MODEL", spark_model); default("CHAT_ENABLE_THINKING", "false")
        default("VISION_BASE_URL", spark); default("VISION_MODEL", spark_model)
        if key:
            default("FALLBACK_MODEL", fw_model); default("FALLBACK_VISION_MODEL", fw_model)
    elif key:
        default("OPENAI_BASE_URL", FIREWORKS_URL); default("OPENAI_API_KEY", key)
        default("CHAT_MODEL", fw_model)
        default("VISION_BASE_URL", FIREWORKS_URL); default("VISION_MODEL", fw_model)
    if key:
        default("FALLBACK_BASE_URL", FIREWORKS_URL); default("FALLBACK_API_KEY", key)
        default("HARD_MODEL", fw_model)
        if "STT_PROVIDER" not in d:
            d["STT_PROVIDER"] = "api"
            default("STT_BASE_URL", FIREWORKS_URL); default("STT_API_KEY", key)
            default("STT_MODEL", d.get("FIREWORKS_STT_MODEL", "").strip() or FIREWORKS_STT_DEFAULT)
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
        if hard_model and not (fallback["FALLBACK_BASE_URL"] and fallback["FALLBACK_API_KEY"]):
            raise ValueError("HARD_MODEL requires FALLBACK_BASE_URL and FALLBACK_API_KEY")
        stt_provider = opt("STT_PROVIDER") or "local"
        stt_base_url = opt("STT_BASE_URL")
        stt_api_key = opt("STT_API_KEY")
        stt_model = opt("STT_MODEL")
        if stt_provider == "api" and not (stt_base_url and stt_api_key and stt_model):
            raise ValueError("STT_PROVIDER=api requires STT_BASE_URL, STT_API_KEY and STT_MODEL")
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
        )


def _tristate(raw: str) -> bool | None:
    """Blank means "don't send the flag at all" (non-Qwen backends)."""
    v = raw.strip().lower()
    if not v:
        return None
    return v in ("1", "true", "yes", "on")
