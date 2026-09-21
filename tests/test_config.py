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
    env = dict(BASE, FALLBACK_MODEL="m2")  # model without URL/key
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


def test_stt_provider_defaults_to_local():
    s = Settings.from_env(BASE)
    assert s.stt_provider == "local"
    assert s.stt_base_url is None
    assert s.stt_api_key is None
    assert s.stt_model is None


def test_stt_provider_api_valid():
    env = dict(BASE, STT_PROVIDER="api", STT_BASE_URL="http://s/v1", STT_API_KEY="k", STT_MODEL="whisper-v3")
    s = Settings.from_env(env)
    assert s.stt_provider == "api"
    assert s.stt_base_url == "http://s/v1"
    assert s.stt_api_key == "k"
    assert s.stt_model == "whisper-v3"


def test_stt_provider_api_missing_fields_raises():
    env = dict(BASE, STT_PROVIDER="api", STT_BASE_URL="http://s/v1")
    with pytest.raises(ValueError, match="STT_PROVIDER=api requires STT_BASE_URL, STT_API_KEY and STT_MODEL"):
        Settings.from_env(env)


MIN = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_USER_ID": "1", "FIREWORKS_API_KEY": "fw"}

def test_minimal_fireworks_only_derives_everything():
    s = Settings.from_env(MIN)
    assert s.openai_base_url.startswith("https://api.fireworks.ai") and s.openai_api_key == "fw"
    assert s.chat_model.endswith("deepseek-v4p1-flash") and s.chat_enable_thinking is None
    assert s.vision_model == s.chat_model and s.fallback_model is None
    assert s.hard_model == s.chat_model and s.hard_base_url == s.openai_base_url and s.hard_api_key == "fw"
    assert s.fallback_base_url and s.fallback_api_key == "fw"
    assert s.stt_provider == "api" and s.stt_model == "whisper-v3"
    assert s.knowledge_dir.name == "knowledge" and s.data_dir.name == "data" and s.kokoro_model_dir.name == "models"

def test_spark_plus_fireworks_derives_fallback():
    s = Settings.from_env(dict(MIN, SPARK_URL="http://spark:8888/v1"))
    assert s.openai_base_url == "http://spark:8888/v1" and s.chat_model == "qwen3.8-flash-next"
    assert s.chat_enable_thinking is True and s.vision_base_url == "http://spark:8888/v1"
    assert s.fallback_model.endswith("deepseek-v4p1-flash")
    # with the Spark in front, voice notes and the memory index stay home; chat and photos fall back
    assert s.fallback_vision_model == s.fallback_model and s.stt_provider == "local" and s.embed_base_url is None
    # ...and neither does anything "hard": that is the Spark thinking harder
    assert s.hard_base_url == "http://spark:8888/v1" and s.hard_model == "qwen3.8-flash-next" and s.hard_thinking is True


def test_spark_voice_server_drives_stt_tts_and_embeddings():
    s = Settings.from_env(dict(MIN, SPARK_URL="http://spark:8888/v1", SPARK_VOICE_URL="http://spark:8890/v1"))
    assert s.stt_provider == "api" and s.stt_base_url == "http://spark:8890/v1" and s.stt_model == "whisper"
    assert s.tts_base_url == "http://spark:8890/v1" and s.tts_model == "kokoro" and s.tts_voice == "am_onyx"
    assert s.embed_base_url == "http://spark:8890/v1" and s.embed_model == "nomic-ai/nomic-embed-text-v1.5"


def test_fireworks_only_keeps_embeddings_and_stt_on_fireworks():
    s = Settings.from_env(MIN)
    assert s.embed_base_url == "https://api.fireworks.ai/inference/v1" and s.embed_model
    assert s.stt_provider == "api"

def test_explicit_advanced_vars_win():
    s = Settings.from_env(dict(MIN, SPARK_URL="http://spark:8888/v1", OPENAI_BASE_URL="http://other/v1", STT_PROVIDER="local"))
    assert s.openai_base_url == "http://other/v1" and s.stt_provider == "local"

def test_nothing_configured_raises():
    with pytest.raises(ValueError, match="FIREWORKS_API_KEY"):
        Settings.from_env({"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_USER_ID": "1"})


def test_inline_comments_are_ignored():
    s = Settings.from_env(dict(MIN, TELEGRAM_USER_ID="42  # my id", FIREWORKS_API_KEY="fw   # key"))
    assert s.telegram_user_id == 42 and s.openai_api_key == "fw"


def test_brave_key_optional():
    assert Settings.from_env(MIN).brave_api_key is None
    assert Settings.from_env(dict(MIN, BRAVE_API_KEY="b")).brave_api_key == "b"


def test_chat_extra_body():
    assert Settings.from_env(MIN).chat_extra_body is None
    assert Settings.from_env(dict(MIN, CHAT_EXTRA_BODY='{"thinking": {"type": "disabled"}}')).chat_extra_body == {"thinking": {"type": "disabled"}}


def test_tts_key_derives_the_openai_speech_defaults():
    s = Settings.from_env(BASE)
    assert s.tts_api_key is None and s.tts_model is None
    s = Settings.from_env(dict(BASE, TTS_API_KEY="sk"))
    assert s.tts_base_url == "https://api.openai.com/v1" and s.tts_model == "gpt-4o-mini-tts"
    assert s.tts_voice == "onyx" and "unhurried" in s.tts_instructions


def test_twilio_is_all_or_nothing():
    s = Settings.from_env(BASE)
    assert s.twilio_account_sid is None and s.phone is None
    s = Settings.from_env(dict(BASE, TWILIO_ACCOUNT_SID="AC", TWILIO_AUTH_TOKEN="t", TWILIO_FROM="+1", PHONE="+2"))
    assert s.twilio_from == "+1" and s.phone == "+2"
    with pytest.raises(ValueError, match="all four"):
        Settings.from_env(dict(BASE, TWILIO_ACCOUNT_SID="AC"))
