from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
# Only these keys carry the day they belong to. Event keys embed the file's
# creation date, which says nothing about when the job fires.
_DAY_KEY_PREFIXES = ("checkin:", "morning:", "evening:", "reflect:", "track:", "plans:")


@dataclass
class Chain:
    kind: str
    opened_at: datetime
    last_sent_at: datetime
    step: int
    item: str
    history: list[str]


@dataclass
class CriticalLeaveState:
    event_path: str
    phase: str  # lead|storm|done
    started_at: datetime
    last_sent_at: datetime | None
    sent_count: int


@dataclass
class PendingVerify:
    todo_path: str
    kind: str
    asked_at: datetime
    question: str | None


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _from_iso(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s is not None else None


def _chain_to_json(c: Chain | None) -> dict | None:
    if c is None:
        return None
    return {
        "kind": c.kind,
        "opened_at": _iso(c.opened_at),
        "last_sent_at": _iso(c.last_sent_at),
        "step": c.step,
        "item": c.item,
        "history": list(c.history),
    }


def _chain_from_json(d: dict | None) -> Chain | None:
    if d is None:
        return None
    return Chain(
        kind=d["kind"],
        opened_at=_from_iso(d["opened_at"]),
        last_sent_at=_from_iso(d["last_sent_at"]),
        step=d["step"],
        item=d["item"],
        history=list(d["history"]),
    )


def _critical_to_json(c: CriticalLeaveState | None) -> dict | None:
    if c is None:
        return None
    return {
        "event_path": c.event_path,
        "phase": c.phase,
        "started_at": _iso(c.started_at),
        "last_sent_at": _iso(c.last_sent_at),
        "sent_count": c.sent_count,
    }


def _critical_from_json(d: dict | None) -> CriticalLeaveState | None:
    if d is None:
        return None
    return CriticalLeaveState(
        event_path=d["event_path"],
        phase=d["phase"],
        started_at=_from_iso(d["started_at"]),
        last_sent_at=_from_iso(d["last_sent_at"]),
        sent_count=d["sent_count"],
    )


def _pv_to_json(p: PendingVerify | None) -> dict | None:
    if p is None:
        return None
    return {
        "todo_path": p.todo_path,
        "kind": p.kind,
        "asked_at": _iso(p.asked_at),
        "question": p.question,
    }


def _pv_from_json(d: dict | None) -> PendingVerify | None:
    if d is None:
        return None
    return PendingVerify(
        todo_path=d["todo_path"],
        kind=d["kind"],
        asked_at=_from_iso(d["asked_at"]),
        question=d["question"],
    )


@dataclass
class RuntimeState:
    fired: set[str] = field(default_factory=set)
    pause_until: datetime | None = None
    last_user_message_at: datetime | None = None
    proactive_sends: list[datetime] = field(default_factory=list)
    chain: Chain | None = None
    critical: CriticalLeaveState | None = None
    briefing_override: dict[str, str] = field(default_factory=dict)
    pending_verify: PendingVerify | None = None
    # source paths in the order the last /sources listing numbered them, for /summary <n>
    last_sources_listing: list[str] = field(default_factory=list)
    sprint: dict | None = None  # {"path": todo path, "title": str, "ends_at": ISO}
    backend_down: bool = False  # the primary (Spark) model is unreachable; running on the backup
    last_location: dict | None = None  # {"lat", "lng", "at": ISO} — the last location he shared
    pending_schedule: dict | None = None  # /schedule is waiting for typed details: {mode, day|path}
    last_file: dict | None = None  # {"name", "text", "at": ISO} — the last file he sent to talk about
    review: dict | None = None  # an open recall session: queue, current, right, again
    recent: list = field(default_factory=list)  # last few [role, text] exchanges, capped
    projects: dict = field(default_factory=dict)  # AgentHub: turns watched/reported, statuses seen

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "fired": list(self.fired),
            "pause_until": _iso(self.pause_until),
            "last_user_message_at": _iso(self.last_user_message_at),
            "proactive_sends": [_iso(t) for t in self.proactive_sends],
            "chain": _chain_to_json(self.chain),
            "critical": _critical_to_json(self.critical),
            "briefing_override": self.briefing_override,
            "pending_verify": _pv_to_json(self.pending_verify),
            "last_sources_listing": list(self.last_sources_listing),
            "sprint": self.sprint,
            "backend_down": self.backend_down,
            "last_location": self.last_location,
            "pending_schedule": self.pending_schedule,
            "last_file": self.last_file,
            "review": self.review,
            "recent": [list(x) for x in self.recent],
            "projects": self.projects,
        }
        path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: Path) -> "RuntimeState":
        path = Path(path)
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
            return cls(
                fired=set(data.get("fired", [])),
                pause_until=_from_iso(data.get("pause_until")),
                last_user_message_at=_from_iso(data.get("last_user_message_at")),
                proactive_sends=[_from_iso(t) for t in data.get("proactive_sends", [])],
                chain=_chain_from_json(data.get("chain")),
                critical=_critical_from_json(data.get("critical")),
                briefing_override=data.get("briefing_override", {}),
                pending_verify=_pv_from_json(data.get("pending_verify")),
                last_sources_listing=list(data.get("last_sources_listing", [])),
                sprint=data.get("sprint"),
                backend_down=bool(data.get("backend_down", False)),
                last_location=data.get("last_location"),
                pending_schedule=data.get("pending_schedule"),
                last_file=data.get("last_file"),
                review=data.get("review"),
                recent=[list(x) for x in data.get("recent", [])],
                projects=data.get("projects") or {},
            )
        except Exception as exc:
            logger.warning("failed to load state from %s: %s", path, exc)
            return cls()

    def prune(self, now: datetime) -> None:
        cutoff_date = now.date() - timedelta(days=2)
        kept = set()
        for key in self.fired:
            if key.startswith(_DAY_KEY_PREFIXES):
                m = _DATE_RE.search(key)
                if m and date.fromisoformat(m.group(1)) < cutoff_date:
                    continue
            kept.add(key)
        self.fired = kept
        self.proactive_sends = [t for t in self.proactive_sends if now - t <= timedelta(hours=1)]
