from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo


class Clock(Protocol):
    def now(self) -> datetime: ...


@dataclass
class SystemClock:
    tz: ZoneInfo

    def now(self) -> datetime:
        return datetime.now(self.tz)


class FakeClock:
    def __init__(self, start: datetime):
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: int = 0, minutes: int = 0) -> None:
        self._now += timedelta(seconds=seconds, minutes=minutes)

    def set(self, dt: datetime) -> None:
        self._now = dt
