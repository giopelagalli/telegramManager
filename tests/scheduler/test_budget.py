from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from bot.knowledge.models import Profile
from bot.scheduler.state import RuntimeState
from bot.scheduler.budget import budget_ok, record_send
NY = ZoneInfo("America/New_York"); NOW = datetime(2026, 9, 3, 14, 0, tzinfo=NY)

def test_budget_three_per_rolling_hour():
    s = RuntimeState.load(Path("/nonexistent")); p = Profile()
    for _ in range(3):
        assert budget_ok(NOW, s, p); record_send(NOW, s)
    assert not budget_ok(NOW, s, p)
    assert budget_ok(NOW + timedelta(hours=1, seconds=1), s, p)
