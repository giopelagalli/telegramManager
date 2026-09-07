from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


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
class WakeState:
    day: str
    phase: str  # alarm|challenge|engage|done
    started_at: datetime
    last_sent_at: datetime | None
    cadence_seconds: int
    attempts: int
    engaged_seconds: int
    last_reply_at: datetime | None
    verified: bool | None


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


def _wake_to_json(w: WakeState | None) -> dict | None:
    if w is None:
        return None
    return {
        "day": w.day,
        "phase": w.phase,
        "started_at": _iso(w.started_at),
        "last_sent_at": _iso(w.last_sent_at),
        "cadence_seconds": w.cadence_seconds,
        "attempts": w.attempts,
        "engaged_seconds": w.engaged_seconds,
        "last_reply_at": _iso(w.last_reply_at),
        "verified": w.verified,
    }


def _wake_from_json(d: dict | None) -> WakeState | None:
    if d is None:
        return None
    return WakeState(
        day=d["day"],
        phase=d["phase"],
        started_at=_from_iso(d["started_at"]),
        last_sent_at=_from_iso(d["last_sent_at"]),
        cadence_seconds=d["cadence_seconds"],
        attempts=d["attempts"],
        engaged_seconds=d["engaged_seconds"],
        last_reply_at=_from_iso(d["last_reply_at"]),
        verified=d["verified"],
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
    wake: WakeState | None = None
    briefing_override: dict[str, str] = field(default_factory=dict)
    pending_verify: PendingVerify | None = None

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
            "wake": _wake_to_json(self.wake),
            "briefing_override": self.briefing_override,
            "pending_verify": _pv_to_json(self.pending_verify),
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
                wake=_wake_from_json(data.get("wake")),
                briefing_override=data.get("briefing_override", {}),
                pending_verify=_pv_from_json(data.get("pending_verify")),
            )
        except Exception as exc:
            logger.warning("failed to load state from %s: %s", path, exc)
            return cls()

    def prune(self, now: datetime) -> None:
        cutoff_date = now.date() - timedelta(days=2)
        kept = set()
        for key in self.fired:
            m = _DATE_RE.search(key)
            if m and date.fromisoformat(m.group(1)) < cutoff_date:
                continue
            kept.add(key)
        self.fired = kept
        self.proactive_sends = [t for t in self.proactive_sends if now - t <= timedelta(hours=1)]
