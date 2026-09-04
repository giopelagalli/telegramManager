from datetime import datetime
from zoneinfo import ZoneInfo
import pytest

NY = ZoneInfo("America/New_York")

@pytest.fixture
def ny():
    return NY

@pytest.fixture
def now_ny():
    return datetime(2026, 9, 3, 14, 0, tzinfo=NY)
