from dataclasses import dataclass
from pathlib import Path

from bot.telegram.app import build_application
from bot.telegram.commands import COMMANDS
from bot.telegram.handlers import Handlers  # noqa: F401  (import smoke test)


@dataclass
class FakeSettings:
    telegram_bot_token: str = "123456:AAHfake-token-for-tests"
    telegram_user_id: int = 42
    data_dir: Path = Path("/tmp")


def test_build_application_registers_every_handler():
    app = build_application(FakeSettings(), router=None, sender=None)
    registered = app.handlers[0]
    # one per command, plus text, voice, photo, location, callback
    assert len(registered) == len(COMMANDS) + 5
