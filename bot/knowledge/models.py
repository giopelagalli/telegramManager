from __future__ import annotations
import re
from dataclasses import dataclass, field, fields
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import frontmatter
import yaml


def slugify(title: str) -> str:
    words = re.findall(r"[a-z0-9]+", title.lower())
    return "-".join(words)[:40]


def parse_frontmatter(text: str) -> tuple[dict, str]:
    post = frontmatter.loads(text)
    return dict(post.metadata), post.content


class _FrontmatterDumper(yaml.SafeDumper):
    pass


def _represent_isoformat(dumper: yaml.SafeDumper, data: date | datetime) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:timestamp", data.isoformat())


def _represent_tuple(dumper: yaml.SafeDumper, data: tuple) -> yaml.SequenceNode:
    return dumper.represent_list(list(data))


_FrontmatterDumper.add_representer(date, _represent_isoformat)
_FrontmatterDumper.add_representer(datetime, _represent_isoformat)
_FrontmatterDumper.add_representer(tuple, _represent_tuple)


def dump_frontmatter(meta: dict, body: str) -> str:
    yaml_block = yaml.dump(
        meta, Dumper=_FrontmatterDumper, sort_keys=False, allow_unicode=True, default_flow_style=False
    ).strip()
    return "---\n" + yaml_block + "\n---\n" + body.rstrip("\n") + "\n"


def hm_to_time(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _parse_datetime(value, field_name: str) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError(f"{field_name} must include a UTC offset")
    return dt


def _parse_date(value) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(value)


@dataclass
class Todo:
    path: str
    title: str
    priority: int = 2
    due: date | None = None
    status: str = "open"
    verify: str = "none"
    goal: str | None = None
    done_at: datetime | None = None
    confirmed: bool = True
    tags: list[str] = field(default_factory=list)
    timestamp: datetime | None = None
    body: str = ""

    @property
    def in_backlog(self) -> bool:
        return self.path.startswith("backlog/")

    @property
    def created(self) -> date:
        m = re.match(r"^(\d{4}-\d{2}-\d{2})-", self.path.rsplit("/", 1)[-1])
        if m:
            return date.fromisoformat(m.group(1))
        if self.timestamp is not None:
            return self.timestamp.date()
        raise ValueError("cannot determine created date: no filename date prefix and no timestamp")

    def to_markdown(self) -> str:
        meta: dict = {"type": "todo", "title": self.title}
        meta["priority"] = self.priority
        if self.due is not None:
            meta["due"] = self.due
        meta["status"] = self.status
        meta["verify"] = self.verify
        if self.goal is not None:
            meta["goal"] = self.goal
        if self.done_at is not None:
            meta["done_at"] = self.done_at
        meta["confirmed"] = self.confirmed
        if self.tags:
            meta["tags"] = self.tags
        if self.timestamp is not None:
            meta["timestamp"] = self.timestamp
        return dump_frontmatter(meta, self.body)

    @classmethod
    def from_markdown(cls, path: str, text: str) -> "Todo":
        meta, body = parse_frontmatter(text)
        due = meta.get("due")
        done_at = meta.get("done_at")
        timestamp = meta.get("timestamp")
        return cls(
            path=path,
            title=meta.get("title", ""),
            priority=meta.get("priority", 2),
            due=_parse_date(due) if due is not None else None,
            status=meta.get("status", "open"),
            verify=meta.get("verify", "none"),
            goal=meta.get("goal"),
            done_at=_parse_datetime(done_at, "done_at") if done_at is not None else None,
            confirmed=meta.get("confirmed", True),
            tags=list(meta.get("tags", [])),
            timestamp=_parse_datetime(timestamp, "timestamp") if timestamp is not None else None,
            body=body,
        )


@dataclass(frozen=True)
class EventTimes:
    leave_by: datetime
    get_ready_at: datetime
    leave_at: datetime


@dataclass
class Event:
    path: str
    title: str
    start: datetime
    end: datetime | None = None
    location: str | None = None
    location_latlng: tuple[float, float] | None = None
    travel_minutes: int = 0
    prep_minutes: int | None = None
    importance: str = "normal"
    verify: str = "none"
    status: str = "upcoming"
    timestamp: datetime | None = None
    body: str = ""

    def times(self, profile: "Profile") -> EventTimes:
        prep = self.prep_minutes if self.prep_minutes is not None else profile.default_prep_minutes
        leave_by = self.start - timedelta(minutes=self.travel_minutes)
        get_ready_at = leave_by - timedelta(minutes=prep)
        leave_at = leave_by - timedelta(minutes=profile.leave_lead_minutes)
        return EventTimes(leave_by=leave_by, get_ready_at=get_ready_at, leave_at=leave_at)

    def to_markdown(self) -> str:
        meta: dict = {"type": "event", "title": self.title, "start": self.start}
        if self.end is not None:
            meta["end"] = self.end
        if self.location is not None:
            meta["location"] = self.location
        if self.location_latlng is not None:
            meta["location_latlng"] = self.location_latlng
        meta["travel_minutes"] = self.travel_minutes
        if self.prep_minutes is not None:
            meta["prep_minutes"] = self.prep_minutes
        meta["importance"] = self.importance
        meta["verify"] = self.verify
        meta["status"] = self.status
        if self.timestamp is not None:
            meta["timestamp"] = self.timestamp
        return dump_frontmatter(meta, self.body)

    @classmethod
    def from_markdown(cls, path: str, text: str) -> "Event":
        meta, body = parse_frontmatter(text)
        end = meta.get("end")
        timestamp = meta.get("timestamp")
        latlng = meta.get("location_latlng")
        return cls(
            path=path,
            title=meta.get("title", ""),
            start=_parse_datetime(meta["start"], "start"),
            end=_parse_datetime(end, "end") if end is not None else None,
            location=meta.get("location"),
            location_latlng=tuple(latlng) if latlng is not None else None,
            travel_minutes=meta.get("travel_minutes", 0),
            prep_minutes=meta.get("prep_minutes"),
            importance=meta.get("importance", "normal"),
            verify=meta.get("verify", "none"),
            status=meta.get("status", "upcoming"),
            timestamp=_parse_datetime(timestamp, "timestamp") if timestamp is not None else None,
            body=body,
        )


@dataclass
class Goal:
    path: str
    title: str
    period: str
    status: str = "active"
    timestamp: datetime | None = None
    body: str = ""

    def to_markdown(self) -> str:
        meta: dict = {"type": "goal", "title": self.title, "period": self.period, "status": self.status}
        if self.timestamp is not None:
            meta["timestamp"] = self.timestamp
        return dump_frontmatter(meta, self.body)

    @classmethod
    def from_markdown(cls, path: str, text: str) -> "Goal":
        meta, body = parse_frontmatter(text)
        timestamp = meta.get("timestamp")
        return cls(
            path=path,
            title=meta.get("title", ""),
            period=meta.get("period", ""),
            status=meta.get("status", "active"),
            timestamp=_parse_datetime(timestamp, "timestamp") if timestamp is not None else None,
            body=body,
        )


@dataclass
class Profile:
    name: str = "Giovanni"
    timezone: str = "America/New_York"
    waking_hours: list[str] = field(default_factory=lambda: ["08:00", "22:00"])
    home_address: str = ""
    home_latlng: tuple[float, float] | None = None
    morning_briefing: str = "08:00"
    evening_briefing: str = "21:00"
    wake_time: str = ""
    wake_photo_spot: str = "kitchen sink"
    checkin_interval_minutes: int = 60
    checkin_skip_if_active_minutes: int = 20
    followup_gaps_minutes: list[int] = field(default_factory=lambda: [15, 30, 60, 120])
    proactive_budget_per_hour: int = 3
    default_prep_minutes: int = 15
    leave_lead_minutes: int = 5
    critical_leave_cap_minutes: int = 20
    wakeup_cap_minutes: int = 30
    wakeup_engage_seconds: int = 120
    voice_on_proactive: bool = True
    voice_reply_mode: str = "on_voice"
    body: str = ""

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def waking_window(self, day: date) -> tuple[datetime, datetime]:
        start_t = hm_to_time(self.waking_hours[0])
        end_t = hm_to_time(self.waking_hours[1])
        tz = self.tz
        start = datetime.combine(day, start_t, tzinfo=tz)
        end = datetime.combine(day, end_t, tzinfo=tz)
        return start, end

    def to_markdown(self) -> str:
        meta = {
            "type": "profile",
            "name": self.name,
            "timezone": self.timezone,
            "waking_hours": self.waking_hours,
            "home_address": self.home_address,
            "home_latlng": self.home_latlng,
            "morning_briefing": self.morning_briefing,
            "evening_briefing": self.evening_briefing,
            "wake_time": self.wake_time,
            "wake_photo_spot": self.wake_photo_spot,
            "checkin_interval_minutes": self.checkin_interval_minutes,
            "checkin_skip_if_active_minutes": self.checkin_skip_if_active_minutes,
            "followup_gaps_minutes": self.followup_gaps_minutes,
            "proactive_budget_per_hour": self.proactive_budget_per_hour,
            "default_prep_minutes": self.default_prep_minutes,
            "leave_lead_minutes": self.leave_lead_minutes,
            "critical_leave_cap_minutes": self.critical_leave_cap_minutes,
            "wakeup_cap_minutes": self.wakeup_cap_minutes,
            "wakeup_engage_seconds": self.wakeup_engage_seconds,
            "voice_on_proactive": self.voice_on_proactive,
            "voice_reply_mode": self.voice_reply_mode,
        }
        return dump_frontmatter(meta, self.body)

    @classmethod
    def from_markdown(cls, text: str) -> "Profile":
        meta, body = parse_frontmatter(text)
        latlng = meta.get("home_latlng")
        kwargs = {}
        for f in fields(cls):
            if f.name in ("home_latlng", "body"):
                continue
            if f.name in meta:
                kwargs[f.name] = meta[f.name]
        if latlng:
            kwargs["home_latlng"] = tuple(latlng)
        kwargs["body"] = body
        return cls(**kwargs)
