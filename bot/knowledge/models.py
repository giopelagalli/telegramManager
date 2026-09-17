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


# A group topic with no binding: carries the thread so the router can warn there once.
UNBOUND = "unbound"


def channel_key(chat_id: int, thread_id: int | None) -> str:
    return f"{chat_id}:{thread_id or 0}"


@dataclass
class Channel:
    chat_id: int
    thread_id: int | None
    kind: str
    course: str | None = None

    @property
    def key(self) -> str:
        return channel_key(self.chat_id, self.thread_id)

    @property
    def name(self) -> str:
        """The string an Outbound carries to be routed back here."""
        return f"course:{self.course}" if self.kind == "course" else self.kind


@dataclass
class Channels:
    bindings: dict[str, Channel] = field(default_factory=dict)

    def by_key(self, key: str) -> Channel | None:
        return self.bindings.get(key)

    def for_kind(self, kind: str, course: str | None = None) -> Channel | None:
        for channel in self.bindings.values():
            if channel.kind == kind and (course is None or channel.course == course):
                return channel
        return None

    def bind(self, channel: Channel) -> None:
        self.bindings[channel.key] = channel

    def unbind(self, key: str) -> None:
        self.bindings.pop(key, None)

    def to_markdown(self) -> str:
        meta = {
            "type": "channels",
            "bindings": {
                key: {
                    "chat_id": c.chat_id,
                    "thread_id": c.thread_id,
                    "kind": c.kind,
                    "course": c.course,
                }
                for key, c in self.bindings.items()
            },
        }
        lines = ["# Channels\n"]
        for key, c in self.bindings.items():
            lines.append(f"- {key} — {c.name}")
        return dump_frontmatter(meta, "\n".join(lines))

    @classmethod
    def from_markdown(cls, text: str) -> "Channels":
        meta, _body = parse_frontmatter(text)
        bindings = {}
        for key, value in (meta.get("bindings") or {}).items():
            bindings[str(key)] = Channel(
                chat_id=value["chat_id"],
                thread_id=value.get("thread_id"),
                kind=value["kind"],
                course=value.get("course"),
            )
        return cls(bindings=bindings)


@dataclass
class Course:
    path: str
    title: str
    term: str | None = None
    topics: list[str] = field(default_factory=list)
    timestamp: datetime | None = None
    body: str = ""

    @property
    def slug(self) -> str:
        name = self.path.rsplit("/", 1)[-1]
        return name[:-3] if name.endswith(".md") else name

    def to_markdown(self) -> str:
        meta: dict = {"type": "course", "title": self.title}
        if self.term is not None:
            meta["term"] = self.term
        meta["topics"] = self.topics
        if self.timestamp is not None:
            meta["timestamp"] = self.timestamp
        return dump_frontmatter(meta, self.body)

    @classmethod
    def from_markdown(cls, path: str, text: str) -> "Course":
        meta, body = parse_frontmatter(text)
        timestamp = meta.get("timestamp")
        return cls(
            path=path,
            title=meta.get("title", ""),
            term=meta.get("term"),
            topics=list(meta.get("topics", [])),
            timestamp=_parse_datetime(timestamp, "timestamp") if timestamp is not None else None,
            body=body,
        )


SOURCE_KINDS = ("slides", "chapter", "paper", "notes", "photo", "hw-spec", "other")


@dataclass
class Source:
    path: str
    title: str
    course: str
    kind: str = "other"
    topics: list[str] = field(default_factory=list)
    summary: str = ""
    pages: int | None = None
    group: str | None = None
    ocr: str | None = None
    last_surfaced: date | None = None  # when the digest last re-read it to the user
    timestamp: datetime | None = None
    body: str = ""

    def to_markdown(self) -> str:
        meta: dict = {"type": "source", "title": self.title, "course": self.course, "kind": self.kind}
        if self.last_surfaced is not None:
            meta["last_surfaced"] = self.last_surfaced
        meta["topics"] = self.topics
        meta["summary"] = self.summary
        if self.pages is not None:
            meta["pages"] = self.pages
        if self.group is not None:
            meta["group"] = self.group
        if self.ocr is not None:
            meta["ocr"] = self.ocr
        if self.timestamp is not None:
            meta["timestamp"] = self.timestamp
        return dump_frontmatter(meta, self.body)

    @classmethod
    def from_markdown(cls, path: str, text: str) -> "Source":
        meta, body = parse_frontmatter(text)
        timestamp = meta.get("timestamp")
        return cls(
            path=path,
            title=meta.get("title", ""),
            course=meta.get("course", ""),
            kind=meta.get("kind", "other"),
            topics=list(meta.get("topics", [])),
            summary=meta.get("summary", ""),
            pages=meta.get("pages"),
            group=meta.get("group"),
            ocr=meta.get("ocr"),
            last_surfaced=_parse_date(meta.get("last_surfaced")) if meta.get("last_surfaced") else None,
            timestamp=_parse_datetime(timestamp, "timestamp") if timestamp is not None else None,
            body=body,
        )


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
    course: str | None = None
    kind: str | None = None
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
        if self.course is not None:
            meta["course"] = self.course
        if self.kind is not None:
            meta["kind"] = self.kind
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
            course=meta.get("course"),
            kind=meta.get("kind"),
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
    course: str | None = None
    kind: str | None = None
    topics: list[str] = field(default_factory=list)
    repeat_days: list[str] = field(default_factory=list)  # MO..SU on a series template
    repeat_until: date | None = None
    series: str | None = None  # path of the series this occurrence was made from
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
        if self.course is not None:
            meta["course"] = self.course
        if self.kind is not None:
            meta["kind"] = self.kind
        if self.topics:
            meta["topics"] = self.topics
        if self.repeat_days:
            meta["repeat_days"] = self.repeat_days
        if self.repeat_until is not None:
            meta["repeat_until"] = self.repeat_until
        if self.series is not None:
            meta["series"] = self.series
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
            course=meta.get("course"),
            kind=meta.get("kind"),
            topics=list(meta.get("topics", [])),
            repeat_days=list(meta.get("repeat_days", [])),
            repeat_until=_parse_date(meta.get("repeat_until")) if meta.get("repeat_until") else None,
            series=meta.get("series"),
            timestamp=_parse_datetime(timestamp, "timestamp") if timestamp is not None else None,
            body=body,
        )


@dataclass
class Memory:
    """One dated line. kind "fact" stays forever; kind "state" is what's on their mind and expires."""
    path: str
    text: str
    day: date
    kind: str = "fact"
    expires: date | None = None
    timestamp: datetime | None = None

    def active(self, today: date) -> bool:
        return self.expires is None or today <= self.expires

    @property
    def title(self) -> str:
        return self.text[:60]

    def to_markdown(self) -> str:
        meta: dict = {"type": "memory", "day": self.day, "kind": self.kind}
        if self.expires is not None:
            meta["expires"] = self.expires
        if self.timestamp is not None:
            meta["timestamp"] = self.timestamp
        return dump_frontmatter(meta, self.text)

    @classmethod
    def from_markdown(cls, path: str, text: str) -> "Memory":
        meta, body = parse_frontmatter(text)
        ts = meta.get("timestamp")
        return cls(path=path, text=body.strip(), day=_parse_date(meta["day"]),
                   kind=meta.get("kind", "fact"),
                   expires=_parse_date(meta["expires"]) if meta.get("expires") else None,
                   timestamp=_parse_datetime(ts, "timestamp") if ts is not None else None)


@dataclass
class Card:
    """One recall question. SM-2 fields decide when it comes back."""
    path: str
    question: str
    answer: str
    course: str
    topic: str = ""
    source: str | None = None
    ease: float = 2.5
    interval: int = 0          # days
    due: date | None = None
    reps: int = 0
    lapses: int = 0
    history: list = field(default_factory=list)  # [[date, grade], ...]
    timestamp: datetime | None = None
    body: str = ""

    @property
    def title(self) -> str:
        return self.question[:60]

    def to_markdown(self) -> str:
        meta: dict = {
            "type": "card", "question": self.question, "answer": self.answer, "course": self.course,
            "topic": self.topic, "ease": self.ease, "interval": self.interval, "reps": self.reps,
            "lapses": self.lapses, "history": [[str(d), g] for d, g in self.history],
        }
        if self.source is not None:
            meta["source"] = self.source
        if self.due is not None:
            meta["due"] = self.due
        if self.timestamp is not None:
            meta["timestamp"] = self.timestamp
        return dump_frontmatter(meta, self.body)

    @classmethod
    def from_markdown(cls, path: str, text: str) -> "Card":
        meta, body = parse_frontmatter(text)
        ts = meta.get("timestamp")
        return cls(
            path=path, question=meta.get("question", ""), answer=meta.get("answer", ""),
            course=meta.get("course", ""), topic=meta.get("topic", ""), source=meta.get("source"),
            ease=float(meta.get("ease", 2.5)), interval=int(meta.get("interval", 0)),
            due=_parse_date(meta.get("due")) if meta.get("due") else None,
            reps=int(meta.get("reps", 0)), lapses=int(meta.get("lapses", 0)),
            history=[(_parse_date(d), int(g)) for d, g in meta.get("history", [])],
            timestamp=_parse_datetime(ts, "timestamp") if ts is not None else None, body=body,
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
    assistant_name: str = "Luna"
    timezone: str = "America/New_York"
    waking_hours: list[str] = field(default_factory=lambda: ["08:00", "22:00"])
    home_address: str = ""
    home_latlng: tuple[float, float] | None = None
    base: str = ""                                  # name of the place currently acting as home
    places: dict = field(default_factory=dict)      # name -> address
    places_latlng: dict = field(default_factory=dict)  # name -> [lat, lng]
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
    tutor_context_chars: int = 150000
    tutor_context_chars_fallback: int = 40000
    assignment_lead_days: int = 3
    exam_review_offsets_days: list[int] = field(default_factory=lambda: [7, 3, 1])
    study_daily_minutes: int = 60          # default daily budget when planning for an exam
    cards_per_topic: int = 8
    review_time: str = "18:00"
    checkin_on_the_hour: bool = True       # predictable check-ins; False = random minute
    review_daily_cap: int = 8
    review_exam_cap: int = 15
    exam_focus_days: int = 7
    digest_time: str = "08:30"
    digest_cadence: str = "daily"
    thinking: bool = False
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
            "base": self.base,
            "places": dict(self.places),
            "places_latlng": {k: list(v) for k, v in self.places_latlng.items()},
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
            "tutor_context_chars": self.tutor_context_chars,
            "tutor_context_chars_fallback": self.tutor_context_chars_fallback,
            "assignment_lead_days": self.assignment_lead_days,
            "exam_review_offsets_days": self.exam_review_offsets_days,
            "study_daily_minutes": self.study_daily_minutes,
            "cards_per_topic": self.cards_per_topic,
            "checkin_on_the_hour": self.checkin_on_the_hour,
            "review_time": self.review_time,
            "review_daily_cap": self.review_daily_cap,
            "review_exam_cap": self.review_exam_cap,
            "exam_focus_days": self.exam_focus_days,
            "digest_time": self.digest_time,
            "digest_cadence": self.digest_cadence,
            "thinking": self.thinking,
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
