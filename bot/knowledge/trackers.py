"""Anything with a number and a unit, per day: water, cholesterol, caffeine, steps. A tracker is
set up once (unit, optional daily target, optional reminders) and logged in one line after that.
Lives in the knowledge bundle: trackers.md for the setup, tracking/YYYY-MM-DD.md for the entries."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date

from bot.knowledge.models import dump_frontmatter, parse_frontmatter, slugify

_ENTRY_RE = re.compile(r"^- (\d{2}:\d{2}) ([a-z0-9-]+) (-?\d+(?:\.\d+)?) (\S+)(?: — (.+))?$")


@dataclass
class Tracker:
    name: str  # slug: water, cholesterol
    unit: str  # oz, ml, mg, steps
    target: float | None = None
    remind_at: list[str] = field(default_factory=list)  # ["10:00", "14:00"]
    remind_every_minutes: int | None = None
    active: bool = True

    @property
    def label(self) -> str:
        return self.name.replace("-", " ")


def _fmt(n: float) -> str:
    return str(int(n)) if float(n).is_integer() else f"{n:g}"


class TrackerStore:
    """Mixed into KnowledgeStore: everything here uses self.root, self.clock and self.log."""

    def trackers(self) -> list[Tracker]:
        path = self.root / "trackers.md"
        if not path.exists():
            return []
        meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        out = []
        for t in meta.get("trackers") or []:
            out.append(Tracker(
                name=str(t.get("name", "")), unit=str(t.get("unit", "")),
                target=float(t["target"]) if t.get("target") is not None else None,
                remind_at=[str(x) for x in (t.get("remind_at") or [])],
                remind_every_minutes=int(t["remind_every_minutes"]) if t.get("remind_every_minutes") else None,
                active=bool(t.get("active", True)),
            ))
        return out

    def save_trackers(self, trackers: list[Tracker]) -> None:
        meta = {"type": "trackers", "trackers": [asdict(t) for t in trackers]}
        (self.root / "trackers.md").write_text(dump_frontmatter(meta, ""), encoding="utf-8")
        self.log("save", "trackers.md")

    def tracker(self, name: str) -> Tracker | None:
        slug = slugify(name)
        return next((t for t in self.trackers() if t.name == slug), None)

    def upsert_tracker(self, name: str, unit: str | None = None, target: float | None = None,
                       remind_at: list[str] | None = None, remind_every_minutes: int | None = None,
                       active: bool | None = None) -> Tracker:
        trackers = self.trackers()
        slug = slugify(name)
        t = next((x for x in trackers if x.name == slug), None)
        if t is None:
            t = Tracker(name=slug, unit=unit or "")
            trackers.append(t)
        if unit:
            t.unit = unit
        if target is not None:
            t.target = target or None
        if remind_at is not None:
            t.remind_at = list(remind_at)
        if remind_every_minutes is not None:
            t.remind_every_minutes = remind_every_minutes or None
        if active is not None:
            t.active = active
        self.save_trackers(trackers)
        return t

    def add_tracking(self, name: str, amount: float, unit: str | None = None, note: str | None = None) -> Tracker:
        t = self.tracker(name) or self.upsert_tracker(name, unit=unit or "")
        if unit and not t.unit:
            t = self.upsert_tracker(name, unit=unit)
        now = self.clock()
        folder = self.root / "tracking"
        folder.mkdir(exist_ok=True)
        line = f"- {now:%H:%M} {t.name} {_fmt(amount)} {unit or t.unit or 'x'}" + (f" — {' '.join(note.split())}" if note else "")
        with (folder / f"{now:%Y-%m-%d}.md").open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        self.log("track", f"{t.name} {now:%Y-%m-%d}")
        return t

    def tracking(self, day: date, name: str | None = None) -> list[dict]:
        path = self.root / "tracking" / f"{day.isoformat()}.md"
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            m = _ENTRY_RE.match(line)
            if m and (name is None or m.group(2) == slugify(name)):
                out.append({"time": m.group(1), "name": m.group(2), "amount": float(m.group(3)), "unit": m.group(4), "note": m.group(5) or "", "line": line})
        return out

    def tracking_total(self, day: date, name: str) -> float:
        return sum(e["amount"] for e in self.tracking(day, name))

    def tracking_last_at(self, day: date, name: str) -> str | None:
        entries = self.tracking(day, name)
        return entries[-1]["time"] if entries else None


def status_line(store, day: date, t: Tracker) -> str:
    """'water: 48 / 100 oz' or 'cholesterol: 180 mg'."""
    total = store.tracking_total(day, t.name)
    return f"{t.label}: {_fmt(total)}" + (f" / {_fmt(t.target)}" if t.target else "") + f" {t.unit}".rstrip()
