from __future__ import annotations

import logging
import re
import subprocess
import threading
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from bot.knowledge.models import (
    Card,
    Memory,
    Channels,
    Course,
    Event,
    Goal,
    Profile,
    Source,
    Todo,
    dump_frontmatter,
    slugify,
)

logger = logging.getLogger(__name__)

# A source longer than this is split across `-part-N.md` files sharing a group.
SOURCE_SPLIT_CHARS = 200_000

_FOLDERS = ["todos", "backlog", "goals", "schedule", "inbox", "courses", "sources"]
_DEFAULT_FOLDER = {Todo: "todos", Event: "schedule", Goal: "goals"}
_ROOT_INDEX = """# Knowledge Bundle

- [todos](todos/index.md) — active and completed to-dos
- [backlog](backlog/index.md) — deferred to-dos
- [goals](goals/index.md) — ongoing goals
- [schedule](schedule/index.md) — calendar events
- [courses](courses/index.md) — courses and their topics
- [sources](sources/index.md) — course material, by course

See `profile.md` for personal settings, `channels.md` for topic bindings and
`log.md` for the action history.
"""


def _part_index(name: str) -> int:
    match = re.search(r"-part-(\d+)\.md$", name)
    return int(match.group(1)) if match else 0


def _split_body(body: str, limit: int) -> list[str]:
    """Whole lines, each chunk at most `limit` characters (a longer single line stays whole)."""
    if len(body) <= limit:
        return [body]
    chunks: list[str] = []
    current = ""
    for line in body.splitlines(keepends=True):
        if current and len(current) + len(line) > limit:
            chunks.append(current)
            current = ""
        current += line
    if current:
        chunks.append(current)
    return chunks


_WEEKDAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


class KnowledgeStore:
    def __init__(self, root: Path, clock: Callable[[], datetime], remotes: list[str] | None = None):
        self.root = Path(root)
        self.clock = clock
        self.remotes = list(remotes or [])
        self.broken_files: list[str] = []
        self._push_threads: list[threading.Thread] = []

    # -- git -----------------------------------------------------------

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout

    # -- backup remotes ---------------------------------------------------

    def _push_remotes(self) -> None:
        """Push HEAD to every backup remote in a daemon thread; never raises."""
        if not self.remotes:
            return
        thread = threading.Thread(target=self._push_sync, daemon=True)
        self._push_threads.append(thread)
        thread.start()

    def _push_sync(self) -> None:
        for remote in self.remotes:
            try:
                result = subprocess.run(
                    ["git", "push", "-q", remote, "HEAD:refs/heads/main"],
                    cwd=self.root,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
            except Exception as exc:
                logger.warning("push to %s failed: %s", remote, exc)
                continue
            if result.returncode != 0:
                logger.warning("push to %s failed: %s", remote, result.stderr.strip())

    def wait_for_pushes(self, timeout: float = 30.0) -> None:
        for thread in self._push_threads:
            thread.join(timeout)
        self._push_threads = [t for t in self._push_threads if t.is_alive()]

    # -- init -----------------------------------------------------------

    def init(self) -> None:
        for d in _FOLDERS:
            (self.root / d).mkdir(parents=True, exist_ok=True)
        profile_path = self.root / "profile.md"
        if not profile_path.exists():
            profile_path.write_text(Profile().to_markdown(), encoding="utf-8")
        log_path = self.root / "log.md"
        if not log_path.exists():
            log_path.write_text("# Log\n", encoding="utf-8")
        (self.root / "index.md").write_text(_ROOT_INDEX, encoding="utf-8")
        self.regenerate_indexes()
        fresh = not (self.root / ".git").exists()
        if fresh:
            self._git("init", "-q")
        # Set every time: the repo may predate us, or come from a bind mount.
        self._git("config", "user.name", "assistant-bot")
        self._git("config", "user.email", "bot@local")
        if fresh:
            self._git("add", "-A")
            self._git("commit", "-q", "-m", "Initial knowledge bundle")

    # -- profile ---------------------------------------------------------

    def profile(self) -> Profile:
        return Profile.from_markdown((self.root / "profile.md").read_text(encoding="utf-8"))

    def save_profile(self, p: Profile) -> None:
        (self.root / "profile.md").write_text(p.to_markdown(), encoding="utf-8")

    # -- channels ---------------------------------------------------------

    def channels(self) -> Channels:
        path = self.root / "channels.md"
        if not path.exists():
            return Channels()
        return Channels.from_markdown(path.read_text(encoding="utf-8"))

    def save_channels(self, channels: Channels) -> None:
        (self.root / "channels.md").write_text(channels.to_markdown(), encoding="utf-8")

    # -- listing (skip-and-log broken files) ------------------------------

    def _load_folder(self, folder: str, model, broken: list[str]) -> list:
        items = []
        for file in sorted((self.root / folder).glob("*.md")):
            if file.name == "index.md":
                continue
            rel = f"{folder}/{file.name}"
            try:
                items.append(model.from_markdown(rel, file.read_text(encoding="utf-8")))
            except Exception as exc:
                logger.warning("failed to parse %s: %s", rel, exc)
                broken.append(rel)
        return items

    def todos(self, include_backlog: bool = False) -> list[Todo]:
        broken: list[str] = []
        items = self._load_folder("todos", Todo, broken)
        if include_backlog:
            items += self._load_folder("backlog", Todo, broken)
        self.broken_files = broken
        return sorted(items, key=lambda t: t.path)

    def events(self) -> list[Event]:
        broken: list[str] = []
        items = self._load_folder("schedule", Event, broken)
        self.broken_files = broken
        return sorted(items, key=lambda e: e.start)

    def memories(self) -> list[Memory]:
        (self.root / "memories").mkdir(exist_ok=True)
        broken: list[str] = []
        items = self._load_folder("memories", Memory, broken)
        self.broken_files = broken
        return sorted(items, key=lambda m: (m.day, m.path))

    STATE_DAYS = 14

    def add_memory(self, text: str, kind: str = "fact") -> str:
        (self.root / "memories").mkdir(exist_ok=True)
        now = self.clock()
        stem = f"{now:%Y-%m-%d}-{slugify(text)[:40] or 'note'}"
        path = self._avoid_collision("memories", stem)
        expires = now.date() + timedelta(days=self.STATE_DAYS) if kind == "state" else None
        memory = Memory(path=path, text=text.strip(), day=now.date(), kind=kind, expires=expires, timestamp=now)
        (self.root / path).write_text(memory.to_markdown(), encoding="utf-8")
        self.log("add", path)
        return path

    def recall_corpus(self) -> list[str]:
        """Every line recall may return, for the vector index."""
        lines = [f"({m.day}) {m.text}" for m in self.memories()]
        log = self.root / "log.md"
        if log.exists():
            lines += [l[2:] for l in log.read_text(encoding="utf-8").splitlines() if l.startswith("- ")]
        lines += [f"source: {s.title} ({s.course}) — {s.summary[:200]}" for s in self.sources()]
        lines += [f"todo: {t.title} [{t.status}]" for t in self.todos()]
        lines += [f"event: {e.title} {e.start:%Y-%m-%d %H:%M}" for e in self.events()]
        return lines

    def recall(self, query: str, limit: int = 12) -> list[str]:
        """Lines from memories, the change log, and source titles that share words with the query."""
        words = {w.lower() for w in re.findall(r"[a-zA-Z0-9]{3,}", query)}
        if not words:
            return []
        hits: list[tuple[int, str]] = []
        for m in self.memories():
            score = sum(1 for w in words if w in m.text.lower())
            if score:
                hits.append((score, f"({m.day}) {m.text}"))
        log = self.root / "log.md"
        if log.exists():
            for line in log.read_text(encoding="utf-8").splitlines():
                score = sum(1 for w in words if w in line.lower())
                if score and line.startswith("- "):
                    hits.append((score, line[2:]))
        for src in self.sources():
            score = sum(1 for w in words if w in (src.title + " " + " ".join(src.topics)).lower())
            if score:
                hits.append((score, f"source: {src.title} ({src.course})"))
        hits.sort(key=lambda h: -h[0])
        return [h for _, h in hits[:limit]]

    def cards(self, course: str | None = None) -> list[Card]:
        root = self.root / "cards"
        if not root.exists():
            return []
        folders = [root / course] if course else sorted(p for p in root.iterdir() if p.is_dir())
        broken: list[str] = []
        items: list[Card] = []
        for folder in folders:
            if folder.exists():
                items += self._load_folder(f"cards/{folder.name}", Card, broken)
        self.broken_files = broken
        return items

    def add_card(self, card: Card) -> str:
        (self.root / "cards" / card.course).mkdir(parents=True, exist_ok=True)
        now = self.clock()
        card.timestamp = now
        stem = f"{now:%Y-%m-%d}-{slugify(card.question)[:40] or 'card'}"
        path = self._avoid_collision(f"cards/{card.course}", stem)
        (self.root / path).write_text(card.to_markdown(), encoding="utf-8")
        card.path = path
        self.log("add", path)
        return path

    def get_card(self, path: str) -> Card:
        return Card.from_markdown(path, self._read(path))

    def series(self) -> list[Event]:
        """Recurring templates live under schedule/series/ and are never events themselves."""
        broken: list[str] = []
        (self.root / "schedule" / "series").mkdir(parents=True, exist_ok=True)
        items = self._load_folder("schedule/series", Event, broken)
        self.broken_files = broken
        return sorted(items, key=lambda e: e.start)

    def add_series(self, template: Event) -> str:
        (self.root / "schedule" / "series").mkdir(parents=True, exist_ok=True)
        return self.add(template, folder="schedule/series")

    def materialize(self, now: datetime, days: int = 14) -> int:
        """Create concrete events for every series over the next `days`; idempotent."""
        existing = {(e.series, e.start.date()) for e in self.events() if e.series}
        made = 0
        for tpl in self.series():
            for offset in range(days + 1):
                day = now.date() + timedelta(days=offset)
                if _WEEKDAYS[day.weekday()] not in tpl.repeat_days:
                    continue
                if tpl.repeat_until and day > tpl.repeat_until:
                    continue
                if day < tpl.start.date() or (tpl.path, day) in existing:
                    continue
                start = tpl.start.replace(year=day.year, month=day.month, day=day.day)
                if start < now:
                    continue
                occurrence = Event(
                    path="", title=tpl.title, start=start,
                    end=tpl.end.replace(year=day.year, month=day.month, day=day.day) if tpl.end else None,
                    location=tpl.location, location_latlng=tpl.location_latlng,
                    travel_minutes=tpl.travel_minutes, prep_minutes=tpl.prep_minutes,
                    importance=tpl.importance, course=tpl.course, kind=tpl.kind, series=tpl.path,
                )
                self.add(occurrence)
                existing.add((tpl.path, day))
                made += 1
        return made

    def delete_series(self, path: str, now: datetime) -> int:
        """Remove a series and its future occurrences; the past stays as history."""
        removed = 0
        for ev in self.events():
            if ev.series == path and ev.start >= now:
                self.delete(ev.path)
                removed += 1
        self.delete(path)
        return removed

    def goals(self) -> list[Goal]:
        broken: list[str] = []
        items = self._load_folder("goals", Goal, broken)
        self.broken_files = broken
        return items

    def courses(self) -> list[Course]:
        broken: list[str] = []
        items = self._load_folder("courses", Course, broken)
        self.broken_files = broken
        return sorted(items, key=lambda c: c.path)

    def get_course(self, slug: str) -> Course:
        path = f"courses/{slug}.md"
        return Course.from_markdown(path, self._read(path))

    def add_course(self, course: Course) -> str:
        course.timestamp = self.clock()
        course.path = course.path or f"courses/{slugify(course.title)}.md"
        (self.root / course.path).write_text(course.to_markdown(), encoding="utf-8")
        self.log("add", course.path)
        return course.path

    def save_course(self, course: Course) -> None:
        course.timestamp = self.clock()
        (self.root / course.path).write_text(course.to_markdown(), encoding="utf-8")
        self.log("save", course.path)

    def _course_folders(self) -> list[str]:
        root = self.root / "sources"
        if not root.exists():
            return []
        return sorted(p.name for p in root.iterdir() if p.is_dir())

    def sources(self, course: str | None = None) -> list[Source]:
        broken: list[str] = []
        items: list[Source] = []
        for name in [course] if course is not None else self._course_folders():
            if not (self.root / "sources" / name).is_dir():
                continue
            items += self._load_folder(f"sources/{name}", Source, broken)
        self.broken_files = broken
        return sorted(items, key=lambda s: s.path)

    def get_source(self, path: str) -> Source:
        return Source.from_markdown(path, self._read(path))

    def add_source(self, source: Source) -> str:
        """Write a source under its course, splitting very long bodies into parts."""
        source.timestamp = self.clock()
        folder = f"sources/{source.course}"
        (self.root / folder).mkdir(parents=True, exist_ok=True)
        stem = f"{source.timestamp.date():%Y-%m-%d}-{slugify(source.title)}"
        chunks = _split_body(source.body, SOURCE_SPLIT_CHARS)

        if len(chunks) == 1:
            path = self._avoid_collision(folder, stem)
            source.path = path
            (self.root / path).write_text(source.to_markdown(), encoding="utf-8")
            self.log("add", path)
            return path

        source.group = self._free_group(folder, stem)
        for i, chunk in enumerate(chunks, 1):
            part = replace(source, path=f"{folder}/{source.group}-part-{i}.md", body=chunk)
            (self.root / part.path).write_text(part.to_markdown(), encoding="utf-8")
            self.log("add", part.path)
            if i == 1:
                source.path = part.path
        return source.path

    def _free_group(self, folder: str, stem: str) -> str:
        suffix = 1
        while True:
            group = stem if suffix == 1 else f"{stem}-{suffix}"
            if not (self.root / folder / f"{group}-part-1.md").exists():
                return group
            suffix += 1

    def keep_raw(self, course: str, filename: str, data: bytes) -> str:
        """Park a file we couldn't extract next to the course's sources."""
        folder = f"sources/{course}/raw"
        (self.root / folder).mkdir(parents=True, exist_ok=True)
        name = Path(filename).name
        stem = Path(name).stem or "file"
        path = self._avoid_collision(folder, stem, Path(name).suffix)
        (self.root / path).write_bytes(data)
        self.log("add", path)
        return path

    def move_source(self, path: str, course: str) -> str:
        """Move a source to another course — every part of it when it was split."""
        new_folder = f"sources/{course}"
        (self.root / new_folder).mkdir(parents=True, exist_ok=True)
        first = ""
        for old_path in self._group_of(path):
            name = old_path.rsplit("/", 1)[-1]
            stem = name[:-3] if name.endswith(".md") else name
            new_path = self._avoid_collision(new_folder, stem)
            self._git("mv", old_path, new_path)
            source = self.get_source(new_path)
            source.path = new_path
            source.course = course
            (self.root / new_path).write_text(source.to_markdown(), encoding="utf-8")
            self.log("move", f"{old_path} -> {new_path}")
            first = first or new_path
        return first

    def _group_of(self, path: str) -> list[str]:
        """Every file sharing this source's group, in part order; just it when unsplit."""
        group = self.get_source(path).group
        if group is None:
            return [path]
        folder = path.rsplit("/", 1)[0]
        files = (self.root / folder).glob(f"{group}-part-*.md")
        return [f"{folder}/{p.name}" for p in sorted(files, key=lambda p: _part_index(p.name))]

    def get_todo(self, path: str) -> Todo:
        return Todo.from_markdown(path, self._read(path))

    def get_event(self, path: str) -> Event:
        return Event.from_markdown(path, self._read(path))

    def get_goal(self, path: str) -> Goal:
        return Goal.from_markdown(path, self._read(path))

    def _read(self, path: str) -> str:
        full = self.root / path
        if not full.exists():
            raise KeyError(path)
        return full.read_text(encoding="utf-8")

    # -- write ------------------------------------------------------------

    def _avoid_collision(self, folder: str, stem: str, ext: str = ".md") -> str:
        suffix = 1
        while True:
            name = f"{stem}{ext}" if suffix == 1 else f"{stem}-{suffix}{ext}"
            path = f"{folder}/{name}"
            if not (self.root / path).exists():
                return path
            suffix += 1

    def add(self, item: Todo | Event | Goal, folder: str | None = None) -> str:
        now = self.clock()
        if folder is None:
            folder = _DEFAULT_FOLDER[type(item)]
        item.timestamp = now
        created = now.date()
        slug = slugify(item.title)
        stem = f"{created:%Y-%m-%d}-{slug}"
        path = self._avoid_collision(folder, stem)
        item.path = path
        (self.root / path).write_text(item.to_markdown(), encoding="utf-8")
        self.log("add", path)
        return path

    def save(self, item) -> None:
        item.timestamp = self.clock()
        (self.root / item.path).write_text(item.to_markdown(), encoding="utf-8")
        self.log("save", item.path)

    def move_todo(self, path: str, to: str) -> str:
        if to not in ("todos", "backlog"):
            raise ValueError(f"invalid destination folder: {to}")
        name = path.rsplit("/", 1)[-1]
        stem = name[:-3] if name.endswith(".md") else name
        new_path = self._avoid_collision(to, stem)
        (self.root / path).rename(self.root / new_path)
        self.log("move", f"{path} -> {new_path}")
        return new_path

    def delete(self, path: str) -> None:
        (self.root / path).unlink()
        self.log("delete", path)

    def add_inbox(self, text: str) -> str:
        now = self.clock()
        path = f"inbox/{now:%Y-%m-%d-%H%M%S}.md"
        content = dump_frontmatter({"type": "note", "timestamp": now}, text)
        (self.root / path).write_text(content, encoding="utf-8")
        self.log("add", path)
        return path

    # -- log ---------------------------------------------------------------

    def log(self, action: str, path: str) -> None:
        line = f"- {self.clock().isoformat()} {action} {path}\n"
        with (self.root / "log.md").open("a", encoding="utf-8") as f:
            f.write(line)

    # -- indexes -------------------------------------------------------------

    def regenerate_indexes(self) -> None:
        self._write_todo_index("todos")
        self._write_todo_index("backlog")
        self._write_schedule_index()
        self._write_goal_index()
        self._write_course_index()
        self._write_source_index()

    def _write_todo_index(self, folder: str) -> None:
        lines = [f"# {folder.capitalize()}\n"]
        for item in self._load_folder(folder, Todo, []):
            due = item.due.isoformat() if item.due else "—"
            name = item.path.rsplit("/", 1)[-1]
            lines.append(f"- [{item.title}]({name}) — P{item.priority}, due {due}, {item.status}")
        (self.root / folder / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_schedule_index(self) -> None:
        lines = ["# Schedule\n"]
        events = sorted(self._load_folder("schedule", Event, []), key=lambda e: e.start)
        for item in events:
            location = item.location or "no location"
            name = item.path.rsplit("/", 1)[-1]
            lines.append(f"- [{item.title}]({name}) — {item.start:%a %b %d %H:%M}, {location}, {item.status}")
        (self.root / "schedule" / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_goal_index(self) -> None:
        lines = ["# Goals\n"]
        for item in self._load_folder("goals", Goal, []):
            name = item.path.rsplit("/", 1)[-1]
            lines.append(f"- [{item.title}]({name}) — {item.period}, {item.status}")
        (self.root / "goals" / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_course_index(self) -> None:
        lines = ["# Courses\n"]
        for item in self._load_folder("courses", Course, []):
            name = item.path.rsplit("/", 1)[-1]
            term = item.term or "no term"
            lines.append(f"- [{item.title}]({name}) — {term}, {len(item.topics)} topics")
        (self.root / "courses" / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_source_index(self) -> None:
        folder = self.root / "sources"
        if not folder.exists():
            return
        lines = ["# Sources\n"]
        for course in self._course_folders():
            lines.append(f"\n## {course}\n")
            for item in self._load_folder(f"sources/{course}", Source, []):
                name = item.path.rsplit("/", 1)[-1]
                pages = f"{item.pages} pages" if item.pages else "no page count"
                lines.append(f"- [{item.title}]({course}/{name}) — {item.kind}, {pages}")
        (folder / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # -- commit / undo -----------------------------------------------------

    def commit(self, message: str) -> str | None:
        self.regenerate_indexes()
        self._git("add", "-A")
        status = self._git("status", "--porcelain")
        if not status.strip():
            return None
        self._git("commit", "-q", "-m", message)
        self._push_remotes()
        return self._git("rev-parse", "--short", "HEAD").strip()

    def undo(self) -> str | None:
        count = self._git("rev-list", "--count", "HEAD").strip()
        if count == "1":
            return None
        subject = self._git("log", "-1", "--format=%s").strip()
        self._git("revert", "--no-edit", "HEAD")
        self._push_remotes()
        return subject
