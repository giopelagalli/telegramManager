from pathlib import Path
import pytest
from bot.config import Settings

BASE = {
    "TELEGRAM_BOT_TOKEN": "t",
    "TELEGRAM_USER_ID": "123",
    "OPENAI_BASE_URL": "http://vllm:8000/v1",
    "CHAT_MODEL": "m",
    "KNOWLEDGE_DIR": "/k",
    "DATA_DIR": "/d",
}

def test_from_env_reads_required_and_defaults():
    s = Settings.from_env(BASE)
    assert s.telegram_user_id == 123
    assert s.openai_api_key == "unused"
    assert s.vision_model is None
    assert s.google_maps_api_key is None
    assert s.knowledge_dir == Path("/k")
    assert s.chat_enable_thinking is None

def test_chat_enable_thinking_true():
    env = dict(BASE, CHAT_ENABLE_THINKING="true")
    s = Settings.from_env(env)
    assert s.chat_enable_thinking is True

def test_missing_required_raises_with_name():
    env = dict(BASE); del env["TELEGRAM_BOT_TOKEN"]
    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
        Settings.from_env(env)

def test_fallback_partial_raises():
    env = dict(BASE, FALLBACK_BASE_URL="http://f/v1")
    with pytest.raises(ValueError, match="must be set together"):
        Settings.from_env(env)

def test_fallback_all_three_set():
    env = dict(BASE, FALLBACK_BASE_URL="http://f/v1", FALLBACK_API_KEY="k", FALLBACK_MODEL="m")
    s = Settings.from_env(env)
    assert (s.fallback_base_url, s.fallback_api_key, s.fallback_model) == ("http://f/v1", "k", "m")

def test_fallback_vision_model_without_fallback_chat_raises():
    env = dict(BASE, FALLBACK_VISION_MODEL="v")
    with pytest.raises(ValueError, match="FALLBACK_VISION_MODEL requires FALLBACK_BASE_URL and FALLBACK_API_KEY"):
        Settings.from_env(env)

def test_fallback_vision_model_with_fallback_chat_set():
    env = dict(
        BASE,
        FALLBACK_BASE_URL="http://f/v1",
        FALLBACK_API_KEY="k",
        FALLBACK_MODEL="m",
        FALLBACK_VISION_MODEL="v",
    )
    s = Settings.from_env(env)
    assert s.fallback_vision_model == "v"

def test_whisper_model_default_and_override():
    assert Settings.from_env(BASE).whisper_model == "small"
    assert Settings.from_env(dict(BASE, WHISPER_MODEL="base")).whisper_model == "base"

def test_knowledge_remotes_split_and_strip():
    assert Settings.from_env(BASE).knowledge_remotes == []
    env = dict(BASE, KNOWLEDGE_REMOTES=" git@a:x.git , u@b:y.git ")
    assert Settings.from_env(env).knowledge_remotes == ["git@a:x.git", "u@b:y.git"]

def test_hard_model_default_none():
    assert Settings.from_env(BASE).hard_model is None

def test_hard_model_without_fallback_raises():
    env = dict(BASE, HARD_MODEL="h")
    with pytest.raises(ValueError, match="HARD_MODEL requires FALLBACK_BASE_URL and FALLBACK_API_KEY"):
        Settings.from_env(env)

def test_hard_model_with_fallback_base_and_key_set():
    env = dict(
        BASE,
        HARD_MODEL="h",
        FALLBACK_BASE_URL="http://f/v1",
        FALLBACK_API_KEY="k",
        FALLBACK_MODEL="m",
    )
    assert Settings.from_env(env).hard_model == "h"

def test_extra_body_defaults_to_none():
    s = Settings.from_env(BASE)
    assert s.fallback_extra_body is None
    assert s.hard_extra_body is None
    assert s.fallback_vision_extra_body is None

def test_extra_body_empty_string_is_none():
    env = dict(BASE, FALLBACK_EXTRA_BODY="")
    assert Settings.from_env(env).fallback_extra_body is None

def test_extra_body_valid_json_object():
    env = dict(
        BASE,
        FALLBACK_EXTRA_BODY='{"thinking": {"type": "disabled"}}',
        HARD_EXTRA_BODY='{"reasoning_effort": "high"}',
        FALLBACK_VISION_EXTRA_BODY='{"thinking": {"type": "disabled"}}',
    )
    s = Settings.from_env(env)
    assert s.fallback_extra_body == {"thinking": {"type": "disabled"}}
    assert s.hard_extra_body == {"reasoning_effort": "high"}
    assert s.fallback_vision_extra_body == {"thinking": {"type": "disabled"}}

def test_extra_body_invalid_json_raises():
    env = dict(BASE, FALLBACK_EXTRA_BODY="not json")
    with pytest.raises(ValueError, match="FALLBACK_EXTRA_BODY must be a JSON object"):
        Settings.from_env(env)

def test_extra_body_non_object_json_raises():
    env = dict(BASE, HARD_EXTRA_BODY="[1, 2, 3]")
    with pytest.raises(ValueError, match="HARD_EXTRA_BODY must be a JSON object"):
        Settings.from_env(env)


def test_chat_enable_thinking_false_is_explicit():
    s = Settings.from_env(dict(BASE, CHAT_ENABLE_THINKING="false"))
    assert s.chat_enable_thinking is False
