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

def test_missing_required_raises_with_name():
    env = dict(BASE); del env["TELEGRAM_BOT_TOKEN"]
    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
        Settings.from_env(env)
