from __future__ import annotations

import logging
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

from bot.knowledge.models import (
    Channels,
    Course,
    Event,
    Goal,
    Profile,
    Todo,
    dump_frontmatter,
    slugify,
)

logger = logging.getLogger(__name__)

_FOLDERS = ["todos", "backlog", "goals", "schedule", "inbox", "courses"]
_DEFAULT_FOLDER = {Todo: "todos", Event: "schedule", Goal: "goals"}
_ROOT_INDEX = """# Knowledge Bundle

- [todos](todos/index.md) — active and completed to-dos
- [backlog](backlog/index.md) — deferred to-dos
- [goals](goals/index.md) — ongoing goals
- [schedule](schedule/index.md) — calendar events
- [courses](courses/index.md) — courses and their topics

See `profile.md` for personal settings, `channels.md` for topic bindings and
`log.md` for the action history.
"""


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
            profile_path.write_text(Profile().to_markdown())
        log_path = self.root / "log.md"
        if not log_path.exists():
            log_path.write_text("# Log\n")
        (self.root / "index.md").write_text(_ROOT_INDEX)
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
        return Profile.from_markdown((self.root / "profile.md").read_text())

    def save_profile(self, p: Profile) -> None:
        (self.root / "profile.md").write_text(p.to_markdown())

    # -- channels ---------------------------------------------------------

    def channels(self) -> Channels:
        path = self.root / "channels.md"
        if not path.exists():
            return Channels()
        return Channels.from_markdown(path.read_text())

    def save_channels(self, channels: Channels) -> None:
        (self.root / "channels.md").write_text(channels.to_markdown())

    # -- listing (skip-and-log broken files) ------------------------------

    def _load_folder(self, folder: str, model, broken: list[str]) -> list:
        items = []
        for file in sorted((self.root / folder).glob("*.md")):
            if file.name == "index.md":
                continue
            rel = f"{folder}/{file.name}"
            try:
                items.append(model.from_markdown(rel, file.read_text()))
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
        (self.root / course.path).write_text(course.to_markdown())
        self.log("add", course.path)
        return course.path

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
        return full.read_text()

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
        (self.root / path).write_text(item.to_markdown())
        self.log("add", path)
        return path

    def save(self, item: Todo | Event | Goal) -> None:
        item.timestamp = self.clock()
        (self.root / item.path).write_text(item.to_markdown())
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
        (self.root / path).write_text(content)
        self.log("add", path)
        return path

    # -- log ---------------------------------------------------------------

    def log(self, action: str, path: str) -> None:
        line = f"- {self.clock().isoformat()} {action} {path}\n"
        with (self.root / "log.md").open("a") as f:
            f.write(line)

    # -- indexes -------------------------------------------------------------

    def regenerate_indexes(self) -> None:
        self._write_todo_index("todos")
        self._write_todo_index("backlog")
        self._write_schedule_index()
        self._write_goal_index()
        self._write_course_index()

    def _write_todo_index(self, folder: str) -> None:
        lines = [f"# {folder.capitalize()}\n"]
        for item in self._load_folder(folder, Todo, []):
            due = item.due.isoformat() if item.due else "—"
            name = item.path.rsplit("/", 1)[-1]
            lines.append(f"- [{item.title}]({name}) — P{item.priority}, due {due}, {item.status}")
        (self.root / folder / "index.md").write_text("\n".join(lines) + "\n")

    def _write_schedule_index(self) -> None:
        lines = ["# Schedule\n"]
        events = sorted(self._load_folder("schedule", Event, []), key=lambda e: e.start)
        for item in events:
            location = item.location or "no location"
            name = item.path.rsplit("/", 1)[-1]
            lines.append(f"- [{item.title}]({name}) — {item.start:%a %b %d %H:%M}, {location}, {item.status}")
        (self.root / "schedule" / "index.md").write_text("\n".join(lines) + "\n")

    def _write_goal_index(self) -> None:
        lines = ["# Goals\n"]
        for item in self._load_folder("goals", Goal, []):
            name = item.path.rsplit("/", 1)[-1]
            lines.append(f"- [{item.title}]({name}) — {item.period}, {item.status}")
        (self.root / "goals" / "index.md").write_text("\n".join(lines) + "\n")

    def _write_course_index(self) -> None:
        lines = ["# Courses\n"]
        for item in self._load_folder("courses", Course, []):
            name = item.path.rsplit("/", 1)[-1]
            term = item.term or "no term"
            lines.append(f"- [{item.title}]({name}) — {term}, {len(item.topics)} topics")
        (self.root / "courses" / "index.md").write_text("\n".join(lines) + "\n")

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
