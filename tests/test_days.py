from datetime import date, datetime
from zoneinfo import ZoneInfo

from bot.memory.days import dates_in

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=ZoneInfo("America/New_York"))  # a Monday


def test_relative_and_absolute_days():
    assert dates_in("yesterdays notes", NOW) == [date(2026, 9, 20)]
    assert dates_in("what did we talk about yesterday", NOW) == [date(2026, 9, 20)]
    assert dates_in("last tuesday", NOW) == [date(2026, 9, 15)]
    assert dates_in("on Sep 20 what happened", NOW) == [date(2026, 9, 20)]
    assert dates_in("2026-09-18", NOW) == [date(2026, 9, 18)]
    assert dates_in("last week", NOW) == [date(2026, 9, 14 + i) for i in range(7)]
    assert dates_in("how far is mags", NOW) == []
