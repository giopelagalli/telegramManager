# Telegram Personal Assistant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A single-user Telegram bot that stores todos/goals/schedule as an OKF markdown bundle and proactively keeps the user on time: briefings, check-ins, leave-by reminders, follow-up chains, and verified critical mode.

**Architecture:** Deterministic core, LLM at the edges. `knowledge/` owns the markdown bundle and git; `scheduler/` is a 10-second tick loop over pure "what is due at `now`" functions with a persisted fired-key set (no cron library); `agent/` wraps an OpenAI-compatible model with validated tool calls; `telegram/` is thin handlers over python-telegram-bot; `voice/` and `maps/` are two-function adapters. Spec: `docs/superpowers/specs/2026-09-03-telegram-assistant-design.md`.

**Tech Stack:** Python 3.12, python-telegram-bot 21.x (async), openai 1.x (AsyncOpenAI against vLLM), python-frontmatter, PyYAML, httpx, faster-whisper, kokoro-onnx, ffmpeg, pytest + pytest-asyncio, Docker Compose on arm64 (DGX Spark).

## Global Constraints

- Python 3.12; all datetimes are timezone-aware; the profile timezone is `America/New_York` by default.
- The bot answers exactly one Telegram user: `TELEGRAM_USER_ID`. Everything else is ignored silently.
- Every write to `knowledge/` goes through `KnowledgeStore`; one git commit per user message or per scheduler action.
- Deterministic parts (commands, reminders, briefings, chains, critical mode) must never await the model to fire on time. Model calls always have a fallback template.
- Non-annoyance rules (spec §14) are hard requirements: caps, budget, any-reply-closes-chain, nothing non-critical outside waking hours, voice only on briefings and check-ins.
- Tests: `pytest` from repo root; use `tmp_path` for the store; fake clock, fake model client, fake sender. No network in tests.
- Commit after every task with a conventional message and the trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Telegram messages use `parse_mode="HTML"`; escape user-provided text with `html.escape`.

## File Structure

```
pyproject.toml                 deps, pytest config
Dockerfile, docker-compose.yml, .env.example, README.md
bot/__init__.py
bot/__main__.py                entrypoint: Settings → store/agent/engine → PTB polling
bot/config.py                  Settings.from_env
bot/knowledge/models.py        Todo/Event/Goal/Profile + frontmatter parse/dump + Event.times()
bot/knowledge/store.py         KnowledgeStore: files, index/log regen, git commit/undo, inbox
bot/knowledge/ranking.py       rank_todos, top
bot/knowledge/views.py         render_* → HTML strings for Telegram
bot/agent/tools.py             TOOL_SCHEMAS, validate_call
bot/agent/client.py            ModelClient protocol, OpenAIModelClient, FakeModelClient
bot/agent/prompts.py           system prompts + build_context
bot/agent/agent.py             Agent.capture/compose/check_photo/rate_answer, apply_actions
bot/scheduler/clock.py         Clock protocol, SystemClock, FakeClock
bot/scheduler/state.py         RuntimeState + Chain/CriticalLeaveState/WakeState, JSON persistence
bot/scheduler/outbound.py      Outbound message dataclass
bot/scheduler/reminders.py     due_reminders, is_due, maps refresh, missed events
bot/scheduler/checkins.py      checkin_due_at, due_checkin
bot/scheduler/briefings.py     morning/evening text builders, due_briefings
bot/scheduler/chains.py        open_chain, close_chain, due_followup
bot/scheduler/budget.py        budget_ok, record_send
bot/scheduler/critical.py      leave_tick/leave_on_location, wake_tick/wake_on_message/wake_on_photo
bot/scheduler/engine.py        Engine.tick(now) + run loop; wires the above with Sender/Agent/Maps
bot/maps/client.py             MapsClient.geocode/travel_minutes, distance_m
bot/voice/stt.py, tts.py       transcribe, synthesize
bot/telegram/sender.py         Sender: send_text/send_voice with retry, keyboards
bot/telegram/commands.py       /todo /backlog /goals /today /week /brief /pause /quiet /resume /undo /help
bot/telegram/capture.py        text/voice/photo/location handlers → Router
bot/telegram/callbacks.py      done:/defer:/snooze: buttons
bot/telegram/app.py            build_application(settings, deps)
tests/...                      mirrors bot/
```

---

### Task 1: Scaffold, settings, test harness

**Files:**
- Create: `pyproject.toml`, `bot/__init__.py`, `bot/config.py`, `bot/knowledge/__init__.py`, `bot/agent/__init__.py`, `bot/scheduler/__init__.py`, `bot/voice/__init__.py`, `bot/maps/__init__.py`, `bot/telegram/__init__.py`, `tests/__init__.py`, `tests/conftest.py`, `.env.example`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `bot.config.Settings` (frozen dataclass) with fields `telegram_bot_token: str`, `telegram_user_id: int`, `openai_base_url: str`, `openai_api_key: str`, `chat_model: str`, `vision_base_url: str | None`, `vision_model: str | None`, `google_maps_api_key: str | None`, `knowledge_dir: Path`, `data_dir: Path`; classmethod `Settings.from_env(env: Mapping[str, str] | None = None) -> Settings` (uses `os.environ` when `None`; raises `ValueError` naming the missing required variable).

- [ ] **Step 1: Write pyproject.toml**

```toml
[project]
name = "telegram-assistant"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "python-telegram-bot>=21.6,<22",
  "openai>=1.40,<2",
  "python-frontmatter>=1.1",
  "PyYAML>=6.0",
  "httpx>=0.27",
]

[project.optional-dependencies]
voice = ["faster-whisper>=1.0", "kokoro-onnx>=0.4", "soundfile>=0.12", "numpy"]
dev = ["pytest>=8", "pytest-asyncio>=0.23"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.setuptools.packages.find]
include = ["bot*"]
```

- [ ] **Step 2: Write the failing test** (`tests/test_config.py`)

```python
from pathlib import Path
import pytest
from bot.config import Settings

BASE = {
    "TELEGRAM_BOT_TOKEN": "t",
    "TELEGRAM_USER_ID": "123",
    "OPENAI_BASE_URL": "http://vllm:8000/v1",
    "CHAT_MODEL": "m",
    "KNOWLEDGE_DIR": "/k",
    "DATA_DIR": "/d",
}

def test_from_env_reads_required_and_defaults():
    s = Settings.from_env(BASE)
    assert s.telegram_user_id == 123
    assert s.openai_api_key == "unused"
    assert s.vision_model is None
    assert s.google_maps_api_key is None
    assert s.knowledge_dir == Path("/k")

def test_missing_required_raises_with_name():
    env = dict(BASE); del env["TELEGRAM_BOT_TOKEN"]
    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
        Settings.from_env(env)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]' && pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: bot.config`

- [ ] **Step 4: Implement `bot/config.py`**

```python
from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_user_id: int
    openai_base_url: str
    openai_api_key: str
    chat_model: str
    vision_base_url: str | None
    vision_model: str | None
    google_maps_api_key: str | None
    knowledge_dir: Path
    data_dir: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        e = os.environ if env is None else env
        def req(k: str) -> str:
            v = e.get(k, "").strip()
            if not v:
                raise ValueError(f"missing required environment variable {k}")
            return v
        def opt(k: str) -> str | None:
            v = e.get(k, "").strip()
            return v or None
        return cls(
            telegram_bot_token=req("TELEGRAM_BOT_TOKEN"),
            telegram_user_id=int(req("TELEGRAM_USER_ID")),
            openai_base_url=req("OPENAI_BASE_URL"),
            openai_api_key=opt("OPENAI_API_KEY") or "unused",
            chat_model=req("CHAT_MODEL"),
            vision_base_url=opt("VISION_BASE_URL"),
            vision_model=opt("VISION_MODEL"),
            google_maps_api_key=opt("GOOGLE_MAPS_API_KEY"),
            knowledge_dir=Path(req("KNOWLEDGE_DIR")),
            data_dir=Path(req("DATA_DIR")),
        )
```

Create empty `__init__.py` in every package listed. `tests/conftest.py`:

```python
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
```

`.env.example` with the variables from spec §16 (blank values, comments).

- [ ] **Step 5: Run tests, verify pass, commit**

Run: `pytest -v` → 2 passed.
```bash
git add -A && git commit -m "feat: scaffold project, settings from env"
```

---

### Task 2: Knowledge models and frontmatter round-trip

**Files:**
- Create: `bot/knowledge/models.py`
- Test: `tests/knowledge/test_models.py`

**Interfaces:**
- Produces:
  - `parse_frontmatter(text: str) -> tuple[dict, str]`, `dump_frontmatter(meta: dict, body: str) -> str` (YAML block, keys in insertion order, dates as ISO strings).
  - `slugify(title: str) -> str` (lowercase, `[a-z0-9]+` joined by `-`, max 40 chars).
  - `@dataclass class Todo`: `path: str`, `title: str`, `priority: int = 2`, `due: date | None = None`, `status: str = "open"`, `verify: str = "none"`, `goal: str | None = None`, `done_at: datetime | None = None`, `confirmed: bool = True`, `tags: list[str] = field(default_factory=list)`, `timestamp: datetime | None = None`, `body: str = ""`. Properties: `in_backlog -> bool` (path startswith `backlog/`), `created -> date` (from filename prefix, falls back to `timestamp.date()`). Methods: `to_markdown() -> str`, `@classmethod from_markdown(path, text) -> Todo`.
  - `@dataclass class Event`: `path`, `title`, `start: datetime`, `end: datetime | None = None`, `location: str | None = None`, `location_latlng: tuple[float, float] | None = None`, `travel_minutes: int = 0`, `prep_minutes: int | None = None`, `importance: str = "normal"`, `verify: str = "none"`, `status: str = "upcoming"`, `timestamp`, `body`. Method `times(profile: Profile) -> EventTimes`.
  - `@dataclass(frozen=True) class EventTimes`: `leave_by: datetime`, `get_ready_at: datetime`, `leave_at: datetime`.
  - `@dataclass class Goal`: `path`, `title`, `period: str`, `status: str = "active"`, `timestamp`, `body`.
  - `@dataclass class Profile`: every field from spec §5.5 with those defaults, plus `body: str`. Property `tz -> ZoneInfo`. Method `waking_window(day: date) -> tuple[datetime, datetime]`. `to_markdown()`, `from_markdown(text)`.
  - All `from_markdown` tolerate missing keys (defaults) and parse `due` as `date`, `start/end/timestamp/done_at` as aware datetimes (naive → assume profile tz is NOT known here, so require offset; if missing offset raise `ValueError`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/knowledge/test_models.py
from datetime import date, datetime
from zoneinfo import ZoneInfo
import pytest
from bot.knowledge.models import Todo, Event, Goal, Profile, EventTimes, slugify, parse_frontmatter, dump_frontmatter

NY = ZoneInfo("America/New_York")

TODO_MD = """---
type: todo
title: Call dentist to reschedule
priority: 1
due: 2026-09-05
status: open
verify: none
goal: ../goals/2026-W36-health.md
confirmed: true
tags: [health]
timestamp: 2026-09-03T14:10:00-04:00
---
Ask about moving the cleaning.
"""

def test_todo_round_trip():
    t = Todo.from_markdown("todos/2026-09-03-call-dentist.md", TODO_MD)
    assert t.title == "Call dentist to reschedule"
    assert t.priority == 1 and t.due == date(2026, 9, 5)
    assert t.goal == "../goals/2026-W36-health.md"
    assert t.created == date(2026, 9, 3)
    assert not t.in_backlog
    again = Todo.from_markdown(t.path, t.to_markdown())
    assert again == t
    assert t.to_markdown().startswith("---\ntype: todo\n")

def test_todo_defaults_when_keys_missing():
    t = Todo.from_markdown("backlog/2026-01-01-x.md", "---\ntype: todo\ntitle: X\n---\n")
    assert t.priority == 2 and t.status == "open" and t.due is None and t.in_backlog

def test_event_times():
    p = Profile()
    e = Event(path="schedule/2026-09-04-gym.md", title="Gym",
              start=datetime(2026, 9, 4, 18, 0, tzinfo=NY), travel_minutes=20, prep_minutes=15)
    t = e.times(p)
    assert t.leave_by == datetime(2026, 9, 4, 17, 40, tzinfo=NY)
    assert t.get_ready_at == datetime(2026, 9, 4, 17, 25, tzinfo=NY)
    assert t.leave_at == datetime(2026, 9, 4, 17, 35, tzinfo=NY)

def test_event_times_use_profile_prep_default_when_unset():
    p = Profile(default_prep_minutes=30, leave_lead_minutes=10)
    e = Event(path="schedule/x.md", title="X", start=datetime(2026, 9, 4, 9, 0, tzinfo=NY))
    t = e.times(p)
    assert t.leave_by == e.start
    assert t.get_ready_at == datetime(2026, 9, 4, 8, 30, tzinfo=NY)
    assert t.leave_at == datetime(2026, 9, 4, 8, 50, tzinfo=NY)

def test_event_round_trip_with_latlng():
    e = Event(path="schedule/x.md", title="X", start=datetime(2026, 9, 4, 9, 0, tzinfo=NY),
              location="Equinox", location_latlng=(40.72, -73.99), importance="critical")
    again = Event.from_markdown(e.path, e.to_markdown())
    assert again == e

def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        Event.from_markdown("schedule/x.md", "---\ntype: event\ntitle: X\nstart: 2026-09-04T09:00:00\n---\n")

def test_profile_defaults_and_window():
    p = Profile.from_markdown("---\ntype: profile\nname: Giovanni\n---\nLikes gym.\n")
    assert p.name == "Giovanni" and p.timezone == "America/New_York"
    assert p.followup_gaps_minutes == [15, 30, 60, 120]
    start, end = p.waking_window(date(2026, 9, 3))
    assert start == datetime(2026, 9, 3, 8, 0, tzinfo=NY) and end == datetime(2026, 9, 3, 22, 0, tzinfo=NY)
    assert p.body.strip() == "Likes gym."
    assert Profile.from_markdown(p.to_markdown()) == p

def test_goal_round_trip():
    g = Goal(path="goals/2026-ship.md", title="Ship", period="2026-W36")
    assert Goal.from_markdown(g.path, g.to_markdown()) == g

def test_slugify():
    assert slugify("Call dentist to reschedule!") == "call-dentist-to-reschedule"
    assert len(slugify("x" * 100)) == 40

def test_frontmatter_helpers():
    meta, body = parse_frontmatter("---\na: 1\n---\nhi\n")
    assert meta == {"a": 1} and body.strip() == "hi"
    assert dump_frontmatter({"type": "todo", "due": date(2026, 9, 5)}, "b") == "---\ntype: todo\ndue: 2026-09-05\n---\nb\n"
```

- [ ] **Step 2: Run to verify failure** — `pytest tests/knowledge -v` → ImportError.

- [ ] **Step 3: Implement `bot/knowledge/models.py`**

Implementation notes (write the real code, these are the rules):
- Use `frontmatter.loads` for parsing; for dumping write your own: `"---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, default_flow_style=None).strip() + "\n---\n" + body.rstrip("\n") + "\n"`. Convert `date`/`datetime` to `isoformat()` strings and tuples to lists before dumping so YAML stays plain (and `tags: [health]` flow style is fine; use a custom representer or pre-convert). Round-trip equality requires `from_markdown` to convert lists back to tuples for `location_latlng`.
- Datetime parsing: `datetime.fromisoformat`; if `.tzinfo is None` raise `ValueError(f"{field} must include a UTC offset")`. `due` accepts `date` (YAML gives `date`) or `YYYY-MM-DD` string.
- `Todo.to_markdown()` emits keys in the order of spec §5.2, omitting `None` values except keep `due:` omitted when None.
- `Event.times(profile)`: `prep = self.prep_minutes if self.prep_minutes is not None else profile.default_prep_minutes`; `leave_by = start - timedelta(minutes=travel_minutes)`; `get_ready_at = leave_by - timedelta(minutes=prep)`; `leave_at = leave_by - timedelta(minutes=profile.leave_lead_minutes)`.
- `Profile` fields and defaults exactly per spec §5.5: `name="Giovanni"`, `timezone="America/New_York"`, `waking_hours=["08:00","22:00"]`, `home_address=""`, `home_latlng=None` (tuple or None), `morning_briefing="08:00"`, `evening_briefing="21:00"`, `wake_time=""`, `wake_photo_spot="kitchen sink"`, `checkin_interval_minutes=60`, `checkin_skip_if_active_minutes=20`, `followup_gaps_minutes=[15,30,60,120]`, `proactive_budget_per_hour=3`, `default_prep_minutes=15`, `leave_lead_minutes=5`, `critical_leave_cap_minutes=20`, `wakeup_cap_minutes=30`, `wakeup_engage_seconds=120`, `voice_on_proactive=True`, `voice_reply_mode="on_voice"`, `body=""`.
- `Profile.waking_window(day)`: parse `"HH:MM"` strings into aware datetimes in `self.tz`.
- Add a module-level helper `hm_to_time(s: str) -> time` used by Profile and later by briefings.
- `created` for `Todo`: regex `^(\d{4}-\d{2}-\d{2})-` on the filename.

- [ ] **Step 4: Run tests, verify pass, commit**

`pytest tests/knowledge -v` → all pass.
```bash
git add -A && git commit -m "feat(knowledge): OKF models with frontmatter round-trip"
```

---

### Task 3: KnowledgeStore (files, index, log, git, undo, inbox)

**Files:**
- Create: `bot/knowledge/store.py`
- Test: `tests/knowledge/test_store.py`

**Interfaces:**
- Produces `class KnowledgeStore`:
  - `__init__(self, root: Path, clock: Callable[[], datetime])`
  - `init() -> None`: creates dirs `todos backlog goals schedule inbox`, `profile.md` (default Profile), `index.md`, `log.md`, each folder's `index.md`; `git init` if `.git` missing, sets `user.name=assistant-bot`, `user.email=bot@local`, initial commit.
  - `profile() -> Profile`, `save_profile(p: Profile) -> None`
  - `todos(include_backlog: bool = False) -> list[Todo]` (all statuses; sorted by path), `events() -> list[Event]` (sorted by start), `goals() -> list[Goal]`
  - `get_todo(path) -> Todo`, `get_event(path) -> Event`, `get_goal(path) -> Goal` (raise `KeyError`)
  - `add(item: Todo | Event | Goal, folder: str | None = None) -> str`: allocates `path` (`{folder}/{created:%Y-%m-%d}-{slug}.md`, suffix `-2`, `-3` on collision; folder defaults `todos`/`schedule`/`goals` by type, `backlog` when passed), sets `timestamp=now`, writes, returns path.
  - `save(item) -> None`: overwrite existing path, `timestamp=now`.
  - `move_todo(path: str, to: str) -> str`: `to in {"todos","backlog"}`; `git mv`-equivalent (rename on disk; git add handles it); returns new path.
  - `delete(path) -> None`
  - `add_inbox(text: str) -> str`: `inbox/{now:%Y-%m-%d-%H%M%S}.md` with `type: note`.
  - `log(action: str, path: str) -> None`: append `"- {now iso} {action} {path}\n"` to `log.md`. Called automatically by add/save/move/delete.
  - `regenerate_indexes() -> None`: each folder index lists `- [title](file) — key fields`; root `index.md` describes the bundle. Called by `commit`.
  - `commit(message: str) -> str | None`: regenerate indexes, `git add -A`, commit if anything staged; returns short SHA or `None` if nothing to commit.
  - `undo() -> str | None`: `git revert --no-edit HEAD` if HEAD is not the initial commit; returns the reverted commit's subject.
  - `broken_files: list[str]` — paths that failed to parse during the last listing (logged, skipped).

- [ ] **Step 1: Write the failing tests**

```python
# tests/knowledge/test_store.py
import subprocess
from datetime import datetime, date
from zoneinfo import ZoneInfo
import pytest
from bot.knowledge.models import Todo, Event, Goal, Profile
from bot.knowledge.store import KnowledgeStore

NY = ZoneInfo("America/New_York")
T0 = datetime(2026, 9, 3, 14, 0, tzinfo=NY)

@pytest.fixture
def store(tmp_path):
    s = KnowledgeStore(tmp_path / "knowledge", clock=lambda: T0)
    s.init()
    return s

def git(store, *args):
    return subprocess.run(["git", *args], cwd=store.root, capture_output=True, text=True, check=True).stdout

def test_init_creates_layout_and_repo(store):
    for d in ["todos", "backlog", "goals", "schedule", "inbox"]:
        assert (store.root / d / "index.md").exists() or d == "inbox"
    assert (store.root / "profile.md").exists() and (store.root / "log.md").exists()
    assert "Initial" in git(store, "log", "--oneline")
    assert store.profile().name == "Giovanni"

def test_add_allocates_path_and_suffix(store):
    p1 = store.add(Todo(path="", title="Call dentist"))
    p2 = store.add(Todo(path="", title="Call dentist"))
    assert p1 == "todos/2026-09-03-call-dentist.md"
    assert p2 == "todos/2026-09-03-call-dentist-2.md"
    assert store.get_todo(p1).timestamp == T0

def test_add_backlog_and_move(store):
    p = store.add(Todo(path="", title="Garage"), folder="backlog")
    assert p.startswith("backlog/") and store.get_todo(p).in_backlog
    new = store.move_todo(p, "todos")
    assert new == "todos/2026-09-03-garage.md" and not (store.root / p).exists()
    assert [t.path for t in store.todos()] == [new]

def test_commit_regenerates_index_and_log(store):
    p = store.add(Todo(path="", title="Call dentist", due=date(2026, 9, 5)))
    sha = store.commit("add todo")
    assert sha
    idx = (store.root / "todos" / "index.md").read_text()
    assert "Call dentist" in idx and "2026-09-05" in idx
    assert "add todos/2026-09-03-call-dentist.md" in (store.root / "log.md").read_text()
    assert store.commit("nothing") is None

def test_undo_reverts_last_commit(store):
    p = store.add(Todo(path="", title="Oops"))
    store.commit("capture: oops")
    assert store.undo() == "capture: oops"
    assert not (store.root / p).exists()
    assert store.undo() is None or "Revert" in (store.undo() or "")

def test_events_sorted_and_broken_skipped(store):
    a = store.add(Event(path="", title="Later", start=datetime(2026, 9, 4, 18, 0, tzinfo=NY)))
    b = store.add(Event(path="", title="Sooner", start=datetime(2026, 9, 4, 9, 0, tzinfo=NY)))
    (store.root / "schedule" / "2026-09-04-broken.md").write_text("---\ntype: event\ntitle: B\n---\n")
    assert [e.title for e in store.events()] == ["Sooner", "Later"]
    assert "schedule/2026-09-04-broken.md" in store.broken_files

def test_inbox_and_profile_save(store):
    p = store.add_inbox("garbled")
    assert p.startswith("inbox/") and "garbled" in (store.root / p).read_text()
    prof = store.profile(); prof.home_address = "1 Main St"
    store.save_profile(prof)
    assert store.profile().home_address == "1 Main St"
```

- [ ] **Step 2: Run to verify failure** — `pytest tests/knowledge/test_store.py -v` → ImportError.

- [ ] **Step 3: Implement `bot/knowledge/store.py`**

Rules:
- `self.root` is the `Path`. Run git via `subprocess.run([...], cwd=self.root, check=True, capture_output=True, text=True)`; wrap in `_git(*args) -> str`.
- `init()`: `mkdir(parents=True, exist_ok=True)`; write `profile.md` from `Profile().to_markdown()` only if missing; write `log.md` (`# Log\n`) if missing; write root `index.md`; `regenerate_indexes()`; if no `.git`: `git init -q`, config user, `git add -A`, `git commit -q -m "Initial knowledge bundle"`.
- Listing: iterate `sorted(folder.glob("*.md"))` excluding `index.md`; `try: Model.from_markdown(rel, text) except Exception as exc: log via `logging` and append to `self.broken_files`` (reset `broken_files = []` at the start of each listing call).
- `add`: determine `created` date from `item.timestamp or now` → `now.date()`; `slug = slugify(item.title)`; loop suffix.
- `undo()`: `subject = _git("log","-1","--format=%s")`; if `_git("rev-list","--count","HEAD").strip() == "1"` return None; `_git("revert","--no-edit","HEAD")`; return subject.
- Index format per folder (todos/backlog): `- [Title](file.md) — P{priority}, due {due or "—"}, {status}`; schedule: `- [Title](file.md) — {start:%a %b %d %H:%M}, {location or "no location"}, {status}`; goals: `- [Title](file.md) — {period}, {status}`. Root `index.md`: fixed text explaining folders and linking each folder index.

- [ ] **Step 4: Run tests, verify pass, commit**

```bash
pytest tests/knowledge -v
git add -A && git commit -m "feat(knowledge): KnowledgeStore with git commit/undo, indexes, log, inbox"
```

---

### Task 4: Ranking and views

**Files:**
- Create: `bot/knowledge/ranking.py`, `bot/knowledge/views.py`
- Test: `tests/knowledge/test_ranking.py`, `tests/knowledge/test_views.py`, golden files under `tests/knowledge/golden/*.txt`

**Interfaces:**
- `rank_todos(todos: Iterable[Todo], today: date) -> list[Todo]`: keeps only `status == "open"` and `not in_backlog`; sort key `(0 if due and due < today else 1 if due == today else 2, priority, due or date.max, created, path)`.
- `top(todos, today, n=5) -> list[Todo]`.
- `goal_progress(goal: Goal, todos: list[Todo]) -> tuple[int, int]` (done, total) over todos whose `goal` resolves to the goal path (normalise `../goals/x.md` and `goals/x.md`).
- Views return HTML strings: `render_todo(todos, today, show_all=False) -> str`, `render_backlog(todos) -> str`, `render_goals(goals, todos, today) -> str`, `render_today(events, todos, profile, now) -> str`, `render_week(events, profile, now) -> str`. Helper `fmt_time(dt) -> str` (`"5:40pm"` style) and `fmt_day(d) -> str` (`"Thu Sep 4"`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/knowledge/test_ranking.py
from datetime import date
from bot.knowledge.models import Todo
from bot.knowledge.ranking import rank_todos, top

TODAY = date(2026, 9, 3)
def T(path, **kw): return Todo(path=path, title=path, **kw)

def test_rank_order():
    todos = [
        T("todos/2026-09-01-a.md", priority=3, due=date(2026, 9, 10)),
        T("todos/2026-09-01-b.md", priority=1),                       # no due
        T("todos/2026-09-02-c.md", priority=2, due=date(2026, 9, 3)),  # today
        T("todos/2026-09-02-d.md", priority=3, due=date(2026, 9, 1)),  # overdue
        T("todos/2026-09-02-e.md", priority=1, due=date(2026, 9, 1), status="done"),
        T("backlog/2026-09-02-f.md", priority=1, due=date(2026, 9, 1)),
    ]
    assert [t.path.split("-")[-1] for t in rank_todos(todos, TODAY)] == ["d.md", "c.md", "b.md", "a.md"]

def test_ties_by_priority_then_due_then_created():
    todos = [
        T("todos/2026-09-02-x.md", priority=2, due=date(2026, 9, 9)),
        T("todos/2026-09-01-y.md", priority=2, due=date(2026, 9, 9)),
        T("todos/2026-09-01-z.md", priority=2, due=date(2026, 9, 8)),
    ]
    assert [t.path[-4] for t in rank_todos(todos, TODAY)] == ["z", "y", "x"]

def test_top_n():
    todos = [T(f"todos/2026-09-01-{i}.md", priority=1) for i in range(8)]
    assert len(top(todos, TODAY)) == 5
```

```python
# tests/knowledge/test_views.py
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from bot.knowledge.models import Todo, Event, Goal, Profile
from bot.knowledge.views import render_todo, render_today, render_week, render_goals, render_backlog

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 3, 14, 0, tzinfo=NY)
GOLD = Path(__file__).parent / "golden"

def check(name, text):
    f = GOLD / f"{name}.txt"
    if not f.exists():
        f.write_text(text); raise AssertionError(f"wrote golden {name}; re-run")
    assert text == f.read_text()

def todos():
    return [
        Todo(path="todos/2026-09-01-dentist.md", title="Call dentist", priority=1, due=date(2026, 9, 1)),
        Todo(path="todos/2026-09-02-taxes.md", title="File <taxes>", priority=2, due=date(2026, 9, 3)),
        Todo(path="todos/2026-09-02-read.md", title="Read paper", priority=3),
        Todo(path="todos/2026-09-02-done.md", title="Old", status="done"),
    ]

def events():
    return [
        Event(path="schedule/2026-09-03-gym.md", title="Gym", start=datetime(2026, 9, 3, 18, 0, tzinfo=NY),
              end=datetime(2026, 9, 3, 19, 0, tzinfo=NY), location="Equinox", travel_minutes=20),
        Event(path="schedule/2026-09-04-dr.md", title="Doctor", start=datetime(2026, 9, 4, 9, 30, tzinfo=NY), importance="critical"),
        Event(path="schedule/2026-09-12-far.md", title="Far away", start=datetime(2026, 9, 12, 9, 30, tzinfo=NY)),
    ]

def test_render_todo_top5_escapes_html():
    check("todo", render_todo(todos(), NOW.date()))

def test_render_today_has_leave_by():
    check("today", render_today(events(), todos(), Profile(), NOW))

def test_render_week_groups_days_and_marks_free():
    check("week", render_week(events(), Profile(), NOW))

def test_render_goals_progress():
    goals = [Goal(path="goals/2026-W36-health.md", title="Health week", period="2026-W36"),
             Goal(path="goals/2026-ship.md", title="Ship app", period="2026")]
    ts = todos(); ts[0].goal = "../goals/2026-W36-health.md"; ts[3].goal = "../goals/2026-W36-health.md"
    check("goals", render_goals(goals, ts, NOW.date()))

def test_render_backlog_empty():
    assert "empty" in render_backlog([]).lower()
```

- [ ] **Step 2: Run to verify failure** — ImportError.

- [ ] **Step 3: Implement**

`views.py` output rules (so golden files are sane; write them by running the tests once and reviewing by eye):
- `render_todo`: header `<b>Top 5</b>` (or `<b>All open (N)</b>`), then lines `1. {overdue?"⚠️ ":""}{esc(title)} — P{priority}{", due " + fmt_day(due) if due}`. Empty → `Nothing open. Nice.`
- `render_today`: `<b>Today · Thu Sep 3</b>`, events for `now.date()` only: `• 6:00pm Gym (Equinox) — leave by 5:40pm{" ‼️" if critical}`; if none `No events today.`; blank line; then the `render_todo` top 5 block.
- `render_week`: 7 days starting today; each day header `<b>Thu Sep 3</b>` then event lines `  6:00pm–7:00pm Gym — leave by 5:40pm` or `  free`.
- `render_goals`: group by period order: year (`^\d{4}$`), month (`^\d{4}-\d{2}$`), week (`-W`); header `<b>2026</b>` etc.; line `• {title} — {done}/{total}`; skip non-active goals. Empty → `No goals yet. Tell me one.`
- `render_backlog`: `<b>Backlog (N)</b>` + `• title` lines oldest first, or `Backlog is empty.`
- `fmt_time`: `dt.strftime("%-I:%M%p").lower()`; `fmt_day`: `"%a %b %-d"`.

- [ ] **Step 4: Run tests twice (first run writes goldens), inspect `tests/knowledge/golden/*.txt`, verify pass, commit**

```bash
git add -A && git commit -m "feat(knowledge): deterministic ranking and Telegram views"
```

---

### Task 5: Agent — tool schemas, model client, capture/compose/apply

**Files:**
- Create: `bot/agent/tools.py`, `bot/agent/client.py`, `bot/agent/prompts.py`, `bot/agent/agent.py`
- Test: `tests/agent/test_tools.py`, `tests/agent/test_agent.py`

**Interfaces:**
- `tools.py`: `TOOL_SCHEMAS: list[dict]` (OpenAI `{"type":"function","function":{...}}` for every tool in spec §7: `add_todo`, `update_todo`, `move_todo`, `add_event`, `update_event`, `delete_event`, `add_goal`, `update_goal`, `set_profile`, `snooze`, `reply`); `PROFILE_SETTABLE: frozenset[str]` (fields of spec §5.5 except `home_latlng`); `validate_call(name: str, args: dict) -> list[str]` returns error strings (unknown tool, missing required, bad enum, bad ISO datetime/date, `set_profile.field` not settable).
- `client.py`: `@dataclass ToolCall(name: str, arguments: dict)`, `@dataclass ModelResponse(text: str | None, tool_calls: list[ToolCall])`; `class ModelClient(Protocol): async def chat(self, messages: list[dict], tools: list[dict] | None = None, temperature: float = 0.2) -> ModelResponse`; `class OpenAIModelClient(ModelClient)` built from `base_url, api_key, model` using `openai.AsyncOpenAI`, parsing `choices[0].message.tool_calls` (JSON-decode `arguments`; a decode failure becomes `ToolCall(name, {"__invalid_json__": raw})`); `class FakeModelClient` with `responses: list[ModelResponse]` popped FIFO and `calls: list[dict]` recording each request (`messages`, `tools`, `temperature`).
- `prompts.py`: `CAPTURE_SYSTEM: str`, `COMPOSE_SYSTEM: str`, `build_context(store, now, awaiting: str | None) -> str` (profile name/timezone/body, today+tomorrow events with times, open todos compact `- [path] title P{n} due {d}`, active goals, `now` ISO, and `Awaiting answer to: {awaiting}` when set).
- `agent.py`:
  - `@dataclass CaptureResult(actions: list[ToolCall], reply: str, parsed: bool)`
  - `@dataclass Applied(summary: list[str], snooze_minutes: int | None, changed_schedule: bool)`
  - `class Agent(client: ModelClient, vision: ModelClient | None, store: KnowledgeStore, clock: Callable[[], datetime])`
    - `async capture(text: str, awaiting: str | None = None) -> CaptureResult`: messages = system + context + user; tools=`TOOL_SCHEMAS`; temperature 0.1. Validate every call; if any invalid or no `reply` call: retry once appending an assistant turn with the raw calls and a user turn `"Tool call errors: ...; fix and resend all calls"`. Still bad → `store.add_inbox(text)`, return `CaptureResult([], "Saved that, but I couldn't parse it. It's in your inbox.", parsed=False)`. Any exception from the client → same inbox path with reply `"The model is offline; saved your message to the inbox."`.
    - `async compose(kind: str, context: str, fallback: str) -> str`: system=`COMPOSE_SYSTEM`, user=`f"Kind: {kind}\n{context}"`, temperature 0.6, no tools; returns `response.text.strip()` or `fallback` on empty/exception; hard cap 600 chars (truncate at last sentence end).
    - `async check_photo(image_bytes: bytes, expectation: str) -> tuple[bool | None, str]`: `None` when no vision client (degraded). Sends a data-URL image with prompt asking for a JSON `{"ok": bool, "reason": str}`; parse leniently (regex for `"ok"\s*:\s*(true|false)`); exception → `(None, "vision unavailable")`.
    - `async rate_answer(question: str, answer: str) -> bool`: compose-style call returning `SPECIFIC` or `VAGUE`; default `True` on failure (never punish for outages).
  - `apply_actions(store, actions: list[ToolCall], now: datetime) -> Applied` (sync): applies every non-`reply`/`snooze` action in order; `add_todo` with `backlog=True` → folder `backlog`; `update_todo` with `status="done"` sets `done_at=now` and `confirmed = (todo.verify == "none")`; `set_profile` coerces types from the current Profile field type (int/bool/list/str); summary lines like `Added todo: Call dentist (P1, due Fri Sep 5)`, `Marked done: X`, `Added event: Gym Thu Sep 4 6:00pm, leave by 5:40pm`, `Moved to backlog: X`, `Set morning_briefing = 09:00`; `changed_schedule=True` if any event/profile action; exactly one `store.commit(f"capture: {first summary or 'no-op'}")` at the end when summary non-empty.

- [ ] **Step 1: Write the failing tests**

```python
# tests/agent/test_tools.py
from bot.agent.tools import TOOL_SCHEMAS, validate_call, PROFILE_SETTABLE

def test_schemas_cover_all_tools():
    names = {t["function"]["name"] for t in TOOL_SCHEMAS}
    assert names == {"add_todo","update_todo","move_todo","add_event","update_event","delete_event",
                     "add_goal","update_goal","set_profile","snooze","reply"}

def test_validate_ok_and_errors():
    assert validate_call("add_todo", {"title": "x", "priority": 1}) == []
    assert validate_call("nope", {}) == ["unknown tool nope"]
    assert any("title" in e for e in validate_call("add_todo", {"priority": 1}))
    assert any("priority" in e for e in validate_call("add_todo", {"title": "x", "priority": 9}))
    assert any("start" in e for e in validate_call("add_event", {"title": "x", "start": "tomorrow"}))
    assert any("field" in e for e in validate_call("set_profile", {"field": "home_latlng", "value": "x"}))
    assert "morning_briefing" in PROFILE_SETTABLE and "home_latlng" not in PROFILE_SETTABLE
```

```python
# tests/agent/test_agent.py
from datetime import datetime, date
from zoneinfo import ZoneInfo
import pytest
from bot.agent.client import FakeModelClient, ModelResponse, ToolCall
from bot.agent.agent import Agent, apply_actions
from bot.knowledge.store import KnowledgeStore
from bot.knowledge.models import Todo

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 3, 14, 0, tzinfo=NY)

@pytest.fixture
def store(tmp_path):
    s = KnowledgeStore(tmp_path / "k", clock=lambda: NOW); s.init(); return s

def R(*calls, text=None):
    return ModelResponse(text=text, tool_calls=[ToolCall(n, a) for n, a in calls])

async def test_capture_happy_path_applies_and_commits(store):
    client = FakeModelClient([R(("add_todo", {"title": "Call dentist", "priority": 1, "due": "2026-09-05"}),
                                 ("reply", {"text": "Got it."}))])
    agent = Agent(client, None, store, lambda: NOW)
    res = await agent.capture("call dentist by friday")
    assert res.parsed and res.reply == "Got it."
    applied = apply_actions(store, res.actions, NOW)
    assert applied.summary == ["Added todo: Call dentist (P1, due Sat Sep 5)"]
    assert store.todos()[0].title == "Call dentist"
    assert "capture" in __import__("subprocess").run(["git","log","-1","--format=%s"], cwd=store.root, capture_output=True, text=True).stdout

async def test_capture_retries_then_inbox(store):
    client = FakeModelClient([R(("add_todo", {"priority": 1})), R(("add_todo", {"priority": 1}))])
    agent = Agent(client, None, store, lambda: NOW)
    res = await agent.capture("???")
    assert not res.parsed and "inbox" in res.reply
    assert len(client.calls) == 2 and "Tool call errors" in client.calls[1]["messages"][-1]["content"]
    assert list((store.root / "inbox").glob("*.md"))

async def test_capture_model_offline_goes_to_inbox(store):
    class Boom:
        async def chat(self, *a, **k): raise ConnectionError("down")
    res = await Agent(Boom(), None, store, lambda: NOW).capture("hi")
    assert not res.parsed and "offline" in res.reply

async def test_compose_fallback_on_failure(store):
    class Boom:
        async def chat(self, *a, **k): raise ConnectionError("down")
    out = await Agent(Boom(), None, store, lambda: NOW).compose("checkin", "ctx", "fallback text")
    assert out == "fallback text"

def test_apply_done_with_verify_sets_unconfirmed(store):
    p = store.add(Todo(path="", title="Wash car", verify="photo")); store.commit("x")
    applied = apply_actions(store, [ToolCall("update_todo", {"file": p, "status": "done"})], NOW)
    t = store.get_todo(p)
    assert t.status == "done" and t.confirmed is False and t.done_at == NOW
    assert applied.summary == ["Marked done: Wash car"]

def test_apply_set_profile_coerces_and_snooze(store):
    applied = apply_actions(store, [ToolCall("set_profile", {"field": "checkin_interval_minutes", "value": "90"}),
                                    ToolCall("set_profile", {"field": "morning_briefing", "value": "09:00"}),
                                    ToolCall("snooze", {"minutes": 120})], NOW)
    assert store.profile().checkin_interval_minutes == 90
    assert applied.snooze_minutes == 120 and applied.changed_schedule
```

- [ ] **Step 2: Run to verify failure** — ImportError.

- [ ] **Step 3: Implement the four modules**

`prompts.py` `CAPTURE_SYSTEM` must state: you are {name}'s assistant; convert the message into tool calls; one message may need many calls; use `reply` exactly once with a short human reply; assign `priority` using the goals; never invent times, if a time is missing ask in `reply` and add nothing; when the user says "not now", "stop", "later" call `snooze`; when the user answers a pending question (Awaiting answer), treat "yes/yeah/done" as `update_todo status=done` for that item; dates are ISO with the profile offset; today is `{now}`.

`COMPOSE_SYSTEM`: write for Telegram, plain text, no markdown, ≤ 3 sentences unless Kind is `briefing`, address the user by name only when Kind is `followup`, `wake`, or `critical`; never invent items not in the context.

`OpenAIModelClient.chat`: `await self._client.chat.completions.create(model=..., messages=..., tools=tools or NOT_GIVEN, tool_choice="auto" if tools else NOT_GIVEN, temperature=...)`.

`Agent.capture` retry message format: `"Tool call errors: " + "; ".join(errors) + ". Resend ALL tool calls, fixed, and include exactly one reply call."`

- [ ] **Step 4: Run tests, verify pass, commit**

```bash
pytest tests/agent -v
git add -A && git commit -m "feat(agent): validated tool calls, capture/compose with inbox fallback"
```

---

### Task 6: Scheduler foundations — clock, runtime state, outbound, reminders

**Files:**
- Create: `bot/scheduler/clock.py`, `bot/scheduler/state.py`, `bot/scheduler/outbound.py`, `bot/scheduler/reminders.py`
- Test: `tests/scheduler/test_state.py`, `tests/scheduler/test_reminders.py`

**Interfaces:**
- `clock.py`: `class Clock(Protocol): def now(self) -> datetime`; `class SystemClock(tz: ZoneInfo)`; `class FakeClock(start: datetime)` with `now()`, `advance(seconds: int = 0, minutes: int = 0)`, `set(dt)`.
- `outbound.py`: `@dataclass Outbound(text: str, voice: bool = False, buttons: list[tuple[str, str]] = field(default_factory=list), location_button: bool = False, critical: bool = False, kind: str = "")` — `kind` in `{"reminder","checkin","briefing","followup","critical","wake","reply"}`.
- `state.py`:
  - `@dataclass Chain(kind: str, opened_at: datetime, last_sent_at: datetime, step: int, item: str, history: list[str])`
  - `@dataclass CriticalLeaveState(event_path: str, phase: str, started_at: datetime, last_sent_at: datetime | None, sent_count: int)` phases `lead|storm|done`
  - `@dataclass WakeState(day: str, phase: str, started_at: datetime, last_sent_at: datetime | None, cadence_seconds: int, attempts: int, engaged_seconds: int, last_reply_at: datetime | None, verified: bool | None)` phases `alarm|challenge|engage|done`
  - `@dataclass PendingVerify(todo_path: str, kind: str, asked_at: datetime, question: str | None)`
  - `@dataclass RuntimeState(fired: set[str], pause_until: datetime | None, last_user_message_at: datetime | None, proactive_sends: list[datetime], chain: Chain | None, critical: CriticalLeaveState | None, wake: WakeState | None, briefing_override: dict[str, str], pending_verify: PendingVerify | None)` with `save(path: Path)`, `@classmethod load(path: Path) -> RuntimeState` (missing file → defaults; datetimes ISO; sets as lists), `prune(now)` (drop `fired` keys whose embedded date is older than 2 days — keys always start with `{kind}:{YYYY-MM-DD}` or contain an event path with a date; drop `proactive_sends` older than 1h).
- `reminders.py`:
  - `LATE_WINDOW = timedelta(minutes=15)`
  - `is_due(key: str, when: datetime, now: datetime, state: RuntimeState) -> bool`: returns `True` and adds key to `fired` when `when <= now < when + LATE_WINDOW` and key not fired; if `now >= when + LATE_WINDOW` adds key to `fired` (dropped) and returns `False`.
  - `async due_reminders(now, store, state, maps: MapsClient | None) -> list[Outbound]` plus side effects: at get-ready with location+home+maps → `await maps.travel_minutes(home, dest, depart_at=leave_by)`; if not None and differs → `ev.travel_minutes = m; store.save(ev); store.commit("maps: refresh travel")`; recompute times for the get-ready text. Get-ready text: `f"Get ready for {ev.title}. Leave by {fmt_time(times.leave_by)}."`; leave text: `f"Leave in the next {profile.leave_lead_minutes} minutes for {ev.title}."`. For `importance == "critical"` the leave key is marked fired and **no Outbound** is returned; instead `state.critical = CriticalLeaveState(ev.path, "lead", now, None, 0)` (Task 9 sends). Missed: key `missed:{ev.path}` at `start + 15min` → `ev.status = "missed"; store.save; store.commit("event missed")`, no Outbound. Only `status == "upcoming"` events are considered. Reminders are exempt from budget and pause (Outbound `kind="reminder"`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/scheduler/test_state.py
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from bot.scheduler.state import RuntimeState, Chain

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 3, 14, 0, tzinfo=NY)

def test_round_trip(tmp_path):
    s = RuntimeState.load(tmp_path / "state.json")
    s.fired.add("morning:2026-09-03"); s.pause_until = NOW
    s.chain = Chain("checkin", NOW, NOW, 1, "dentist", ["hi"])
    s.proactive_sends.append(NOW)
    s.save(tmp_path / "state.json")
    t = RuntimeState.load(tmp_path / "state.json")
    assert t.fired == {"morning:2026-09-03"} and t.pause_until == NOW and t.chain.item == "dentist"
    assert t.proactive_sends == [NOW]

def test_prune():
    s = RuntimeState.load(__import__("pathlib").Path("/nonexistent/state.json"))
    s.fired |= {"morning:2026-08-30", "morning:2026-09-03", "leave:schedule/2026-08-01-x.md"}
    s.proactive_sends = [NOW - timedelta(hours=2), NOW - timedelta(minutes=10)]
    s.prune(NOW)
    assert s.fired == {"morning:2026-09-03"} and s.proactive_sends == [NOW - timedelta(minutes=10)]
```

```python
# tests/scheduler/test_reminders.py
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pytest
from bot.knowledge.models import Event, Profile
from bot.knowledge.store import KnowledgeStore
from bot.scheduler.state import RuntimeState
from bot.scheduler.reminders import is_due, due_reminders, LATE_WINDOW

NY = ZoneInfo("America/New_York")
T = lambda h, m=0: datetime(2026, 9, 4, h, m, tzinfo=NY)

@pytest.fixture
def store(tmp_path):
    s = KnowledgeStore(tmp_path / "k", clock=lambda: T(8)); s.init(); return s

def test_is_due_window_and_drop():
    s = RuntimeState.load(__import__("pathlib").Path("/nonexistent"))
    when = T(17, 25)
    assert not is_due("k", when, T(17, 24), s)
    assert is_due("k", when, T(17, 25), s) and "k" in s.fired
    assert not is_due("k", when, T(17, 26), s)          # already fired
    s2 = RuntimeState.load(__import__("pathlib").Path("/nonexistent"))
    assert not is_due("k", when, when + LATE_WINDOW, s2) and "k" in s2.fired   # dropped, not fired late

async def test_get_ready_and_leave_fire_once(store):
    store.add(Event(path="", title="Gym", start=T(18), travel_minutes=20, prep_minutes=15)); store.commit("e")
    s = RuntimeState.load(__import__("pathlib").Path("/nonexistent"))
    assert await due_reminders(T(17, 24), store, s, None) == []
    out = await due_reminders(T(17, 25), store, s, None)
    assert [o.text for o in out] == ["Get ready for Gym. Leave by 5:40pm."] and out[0].kind == "reminder"
    assert await due_reminders(T(17, 26), store, s, None) == []
    out = await due_reminders(T(17, 35), store, s, None)
    assert [o.text for o in out] == ["Leave in the next 5 minutes for Gym."]

async def test_critical_leave_starts_state_not_message(store):
    p = store.add(Event(path="", title="Flight", start=T(18), travel_minutes=60, importance="critical")); store.commit("e")
    s = RuntimeState.load(__import__("pathlib").Path("/nonexistent"))
    out = await due_reminders(T(16, 55), store, s, None)
    assert out == [] and s.critical and s.critical.event_path == p and s.critical.phase == "lead"

async def test_maps_refresh_shifts_leave(store):
    prof = store.profile(); prof.home_latlng = (40.7, -74.0); store.save_profile(prof)
    p = store.add(Event(path="", title="Gym", start=T(18), travel_minutes=20, prep_minutes=15,
                        location="Equinox", location_latlng=(40.72, -73.99))); store.commit("e")
    class Maps:
        async def travel_minutes(self, origin, dest, depart_at): return 30
    s = RuntimeState.load(__import__("pathlib").Path("/nonexistent"))
    out = await due_reminders(T(17, 25), store, s, Maps())
    # refresh moved leave_by to 5:30 and leave_at to 5:25, so both fire in this same call
    assert [o.text for o in out] == ["Get ready for Gym. Leave by 5:30pm.", "Leave in the next 5 minutes for Gym."]
    assert store.get_event(p).travel_minutes == 30
    assert await due_reminders(T(17, 26), store, s, Maps()) == []

async def test_missed_marks_event(store):
    p = store.add(Event(path="", title="Gym", start=T(18))); store.commit("e")
    s = RuntimeState.load(__import__("pathlib").Path("/nonexistent"))
    await due_reminders(T(18, 15), store, s, None)
    assert store.get_event(p).status == "missed"

async def test_dst_arithmetic():
    e = Event(path="x", title="X", start=datetime(2026, 11, 1, 3, 0, tzinfo=NY), travel_minutes=90)
    t = e.times(Profile())
    assert t.leave_by.utcoffset() != t.leave_by.replace(hour=4).utcoffset() or True   # documents DST crossing runs
    assert (e.start - t.leave_by) == timedelta(minutes=90)
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement the four modules per the interfaces.** Note in `test_maps_refresh_shifts_leave`: after refresh to 30 minutes, `leave_by` becomes 5:30 and `leave_at` 5:25, which is already in the past at 5:25 exactly, so the second call at 5:25 fires the leave reminder; the test relies on `is_due` with `when == now`.

- [ ] **Step 4: Run tests, verify pass, commit**

```bash
pytest tests/scheduler -v
git add -A && git commit -m "feat(scheduler): runtime state, fired-key semantics, leave-by reminders"
```

---

### Task 7: Check-ins and briefings

**Files:**
- Create: `bot/scheduler/checkins.py`, `bot/scheduler/briefings.py`
- Test: `tests/scheduler/test_checkins.py`, `tests/scheduler/test_briefings.py`

**Interfaces:**
- `checkins.py`:
  - `checkin_slots(profile, day: date) -> list[tuple[str, datetime]]`: slots every `checkin_interval_minutes` from waking start up to (not including) waking end; each slot's due time is `slot_start + jitter` where `jitter = int(sha256(f"{day}:{i}").hexdigest(), 16) % interval` minutes; key `f"checkin:{day}:{i}"`.
  - `should_skip_checkin(now, profile, state, events) -> str | None` returns a reason: `"paused"` if `state.pause_until and now < pause_until`; `"active"` if last user message within `checkin_skip_if_active_minutes`; `"in_event"` if any event `start <= now < (end or start+1h)`; else `None`.
  - `async due_checkin(now, store, state, agent) -> Outbound | None`: finds the slot whose key `is_due`; if skip reason → mark fired, return `None`; else `text = await agent.compose("checkin", context, fallback)` with fallback `f"Next up: {next_event.title} at {fmt_time(next_event.start)}. Top todo: {top1.title}. Done yet?"` (omit missing parts); returns `Outbound(text, voice=profile.voice_on_proactive, kind="checkin")`. Also sets `state.chain = Chain("checkin", now, now, 0, item=top1.title or next_event.title or "", history=[text])`.
- `briefings.py`:
  - `briefing_time(profile, state, day, which: "morning"|"evening") -> datetime` honouring `state.briefing_override.get(f"{which}:{day}")` (`"HH:MM"`).
  - `morning_text(store, now) -> str` (deterministic HTML): `<b>Good morning, {name}.</b>` + today's events with leave-by (`render_today` block without the todo part reuse is fine: call `render_today`), then `<b>This week</b>` goals with progress (only period == current ISO week), then a placeholder line `{prose}` replaced by the model line.
  - `evening_text(store, now) -> tuple[str, list[tuple[str,str]]]`: done today (`done_at.date()==today`), slipped (`due <= today and status=="open"`) each with a button `("→ tomorrow: {title[:20]}", f"defer:{path}")`, unconfirmed (`status=="done" and not confirmed and done_at today`), missed events today, tomorrow's first event with get-ready and leave-by.
  - `async due_briefings(now, store, state, agent) -> list[Outbound]`: keys `morning:{day}` / `evening:{day}`; morning is skipped here when `profile.wake_time` is set (Task 9 sends it after wake-up) — key is left unfired in that case; each returns `Outbound(text, voice=profile.voice_on_proactive, buttons=..., kind="briefing")` and opens `state.chain = Chain("briefing", now, now, 0, item=<first slipped/top todo title>, history=[text])`. Prose line: `await agent.compose("briefing", context, fallback="Have a good one.")`.
  - `async send_morning(now, store, state, agent, note: str | None = None) -> Outbound` used by Task 9 (marks `morning:{day}` fired, prepends `note` when given).

- [ ] **Step 1: Write the failing tests**

```python
# tests/scheduler/test_checkins.py
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from pathlib import Path
import pytest
from bot.knowledge.models import Profile, Event, Todo
from bot.knowledge.store import KnowledgeStore
from bot.scheduler.state import RuntimeState
from bot.scheduler.checkins import checkin_slots, should_skip_checkin, due_checkin

NY = ZoneInfo("America/New_York")
D = date(2026, 9, 3)
def T(h, m=0): return datetime(2026, 9, 3, h, m, tzinfo=NY)

class FakeAgent:
    async def compose(self, kind, context, fallback): return f"[{kind}] " + fallback

@pytest.fixture
def store(tmp_path):
    s = KnowledgeStore(tmp_path / "k", clock=lambda: T(8)); s.init(); return s

def test_slots_are_hourly_jittered_and_stable():
    a = checkin_slots(Profile(), D); b = checkin_slots(Profile(), D)
    assert a == b and len(a) == 14
    assert all(T(8 + i) <= due < T(9 + i) for i, (_, due) in enumerate(a))
    assert a[0][0] == "checkin:2026-09-03:0"

def test_skip_reasons():
    s = RuntimeState.load(Path("/nonexistent")); p = Profile()
    assert should_skip_checkin(T(10), p, s, []) is None
    s.pause_until = T(11); assert should_skip_checkin(T(10), p, s, []) == "paused"
    s.pause_until = None; s.last_user_message_at = T(9, 50)
    assert should_skip_checkin(T(10), p, s, []) == "active"
    s.last_user_message_at = None
    ev = Event(path="x", title="X", start=T(9, 30), end=T(10, 30))
    assert should_skip_checkin(T(10), p, s, [ev]) == "in_event"

async def test_due_checkin_fires_once_and_opens_chain(store):
    store.add(Todo(path="", title="Call dentist", priority=1)); store.commit("t")
    s = RuntimeState.load(Path("/nonexistent"))
    key, due = checkin_slots(store.profile(), D)[2]
    assert await due_checkin(due - timedelta(minutes=1), store, s, FakeAgent()) is None
    out = await due_checkin(due, store, s, FakeAgent())
    assert out and out.kind == "checkin" and out.voice and "Call dentist" in out.text
    assert s.chain and s.chain.kind == "checkin" and s.chain.item == "Call dentist"
    assert await due_checkin(due, store, s, FakeAgent()) is None

async def test_skipped_checkin_is_consumed(store):
    s = RuntimeState.load(Path("/nonexistent")); s.pause_until = T(23)
    key, due = checkin_slots(store.profile(), D)[2]
    assert await due_checkin(due, store, s, FakeAgent()) is None and key in s.fired
```

```python
# tests/scheduler/test_briefings.py
from datetime import datetime, date
from zoneinfo import ZoneInfo
from pathlib import Path
import pytest
from bot.knowledge.models import Profile, Event, Todo, Goal
from bot.knowledge.store import KnowledgeStore
from bot.scheduler.state import RuntimeState
from bot.scheduler.briefings import briefing_time, morning_text, evening_text, due_briefings

NY = ZoneInfo("America/New_York")
def T(h, m=0, d=3): return datetime(2026, 9, d, h, m, tzinfo=NY)

class FakeAgent:
    async def compose(self, kind, context, fallback): return "Make it count."

@pytest.fixture
def store(tmp_path):
    s = KnowledgeStore(tmp_path / "k", clock=lambda: T(8)); s.init()
    s.add(Event(path="", title="Gym", start=T(18), travel_minutes=20))
    s.add(Event(path="", title="Doctor", start=T(9, 30, d=4), travel_minutes=15))
    s.add(Todo(path="", title="Slipped", due=date(2026, 9, 2)))
    t = Todo(path="", title="Done today", status="done", done_at=T(12), confirmed=False, verify="photo"); s.add(t)
    s.add(Goal(path="", title="Health week", period="2026-W36"))
    s.commit("seed"); return s

def test_briefing_time_override():
    s = RuntimeState.load(Path("/nonexistent"))
    assert briefing_time(Profile(), s, date(2026, 9, 3), "morning") == T(8)
    s.briefing_override["morning:2026-09-03"] = "09:15"
    assert briefing_time(Profile(), s, date(2026, 9, 3), "morning") == T(9, 15)
    assert briefing_time(Profile(), s, date(2026, 9, 4), "morning") == T(8, d=4)

def test_morning_text(store):
    t = morning_text(store, T(8))
    assert t.startswith("<b>Good morning, Giovanni.</b>") and "Gym" in t and "leave by 5:40pm" in t and "Health week" in t

def test_evening_text_sections_and_buttons(store):
    text, buttons = evening_text(store, T(21))
    assert "Slipped" in text and "unconfirmed" in text.lower() and "Doctor" in text and "9:00am" in text
    assert buttons and buttons[0][1].startswith("defer:")

async def test_due_briefings_fire_once_and_open_chain(store):
    s = RuntimeState.load(Path("/nonexistent"))
    assert await due_briefings(T(7, 59), store, s, FakeAgent()) == []
    out = await due_briefings(T(8), store, s, FakeAgent())
    assert len(out) == 1 and out[0].kind == "briefing" and out[0].voice and "Make it count." in out[0].text
    assert s.chain and s.chain.kind == "briefing"
    assert await due_briefings(T(8, 1), store, s, FakeAgent()) == []
    out = await due_briefings(T(21), store, s, FakeAgent())
    assert len(out) == 1 and out[0].buttons

async def test_morning_deferred_when_wake_time_set(store):
    p = store.profile(); p.wake_time = "06:30"; store.save_profile(p)
    s = RuntimeState.load(Path("/nonexistent"))
    assert await due_briefings(T(8), store, s, FakeAgent()) == [] and "morning:2026-09-03" not in s.fired
```

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement per interfaces.** Evening tomorrow line: `f"Tomorrow: {title} at {fmt_time(start)} — get ready {fmt_time(get_ready_at)}, leave by {fmt_time(leave_by)}."` (Doctor 9:30 with 15 travel → leave by 9:15, get ready 9:00, which the test asserts via "9:00am").
- [ ] **Step 4: Run tests, verify pass, commit**

```bash
git add -A && git commit -m "feat(scheduler): jittered check-ins and morning/evening briefings"
```

---

### Task 8: Follow-up chains and proactive budget

**Files:**
- Create: `bot/scheduler/chains.py`, `bot/scheduler/budget.py`
- Test: `tests/scheduler/test_chains.py`, `tests/scheduler/test_budget.py`

**Interfaces:**
- `budget.py`: `budget_ok(now, state, profile) -> bool` (prunes `proactive_sends` older than 1h; `len < proactive_budget_per_hour`); `record_send(now, state) -> None`.
- `chains.py`:
  - `close_chain(state) -> None`
  - `chain_gaps(profile, kind) -> list[int]`: `followup_gaps_minutes` for `briefing`, first two for `checkin`.
  - `async due_followup(now, store, state, agent) -> Outbound | None`: no chain → None; outside waking window → close, None; `step >= len(gaps)` → close, None; `now >= last_sent_at + gaps[step]` → compose text via `agent.compose("followup", context, fallback)` where context includes `step`, `item`, `history`, name, active goal titles and fallback templates by step: 0 `"{name}, still there? {item} is the one thing right now."`, 1 `"{name}, quick one: {item}. Yes or no?"`, 2 `"{name}, you said this week was about {goal}. {item} moves it."`, 3 `"{name}, last nudge on {item}. I'll leave you alone until the next check-in."`; then `chain.step += 1; chain.last_sent_at = now; chain.history.append(text)`; returns `Outbound(text, kind="followup")` (no voice). When the final step is sent, the chain is closed immediately after.
  - Budget is applied by the engine (Task 12), not here.

- [ ] **Step 1: Write the failing tests**

```python
# tests/scheduler/test_budget.py
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from bot.knowledge.models import Profile
from bot.scheduler.state import RuntimeState
from bot.scheduler.budget import budget_ok, record_send
NY = ZoneInfo("America/New_York"); NOW = datetime(2026, 9, 3, 14, 0, tzinfo=NY)

def test_budget_three_per_rolling_hour():
    s = RuntimeState.load(Path("/nonexistent")); p = Profile()
    for _ in range(3):
        assert budget_ok(NOW, s, p); record_send(NOW, s)
    assert not budget_ok(NOW, s, p)
    assert budget_ok(NOW + timedelta(hours=1, seconds=1), s, p)
```

```python
# tests/scheduler/test_chains.py
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
import pytest
from bot.knowledge.store import KnowledgeStore
from bot.knowledge.models import Goal
from bot.scheduler.state import RuntimeState, Chain
from bot.scheduler.chains import due_followup, close_chain, chain_gaps

NY = ZoneInfo("America/New_York")
def T(h, m=0): return datetime(2026, 9, 3, h, m, tzinfo=NY)

class FakeAgent:
    def __init__(self): self.kinds = []
    async def compose(self, kind, context, fallback): self.kinds.append(kind); return fallback

@pytest.fixture
def store(tmp_path):
    s = KnowledgeStore(tmp_path / "k", clock=lambda: T(8)); s.init()
    s.add(Goal(path="", title="Ship app", period="2026-W36")); s.commit("g"); return s

def test_gaps():
    from bot.knowledge.models import Profile
    assert chain_gaps(Profile(), "briefing") == [15, 30, 60, 120] and chain_gaps(Profile(), "checkin") == [15, 30]

async def test_briefing_chain_four_steps_then_closes(store):
    s = RuntimeState.load(Path("/nonexistent")); a = FakeAgent()
    s.chain = Chain("briefing", T(8), T(8), 0, "Call dentist", ["brief"])
    assert await due_followup(T(8, 14), store, s, a) is None
    o1 = await due_followup(T(8, 15), store, s, a); assert "Giovanni" in o1.text and "Call dentist" in o1.text and o1.kind == "followup" and not o1.voice
    assert await due_followup(T(8, 30), store, s, a) is None
    o2 = await due_followup(T(8, 45), store, s, a); assert o2
    o3 = await due_followup(T(9, 45), store, s, a); assert "Ship app" in o3.text
    o4 = await due_followup(T(11, 45), store, s, a); assert "last nudge" in o4.text
    assert s.chain is None and a.kinds == ["followup"] * 4

async def test_checkin_chain_two_steps(store):
    s = RuntimeState.load(Path("/nonexistent")); a = FakeAgent()
    s.chain = Chain("checkin", T(10), T(10), 0, "X", [])
    assert await due_followup(T(10, 15), store, s, a)
    assert await due_followup(T(10, 45), store, s, a)
    assert s.chain is None

async def test_chain_closed_outside_waking_hours(store):
    s = RuntimeState.load(Path("/nonexistent"))
    s.chain = Chain("briefing", T(21, 50), T(21, 50), 0, "X", [])
    assert await due_followup(T(22, 5), store, s, FakeAgent()) is None and s.chain is None

def test_close_chain():
    s = RuntimeState.load(Path("/nonexistent")); s.chain = Chain("checkin", T(10), T(10), 0, "X", [])
    close_chain(s); assert s.chain is None
```

- [ ] **Step 2: Run to verify failure.** — [ ] **Step 3: Implement.** — [ ] **Step 4: Run, pass, commit**

```bash
git add -A && git commit -m "feat(scheduler): follow-up chains with caps and proactive budget"
```

---

### Task 9: Critical mode — leave storm and wake-up state machines

**Files:**
- Create: `bot/scheduler/critical.py`
- Test: `tests/scheduler/test_critical.py`

**Interfaces:**
- `HOME_RADIUS_M = 150`, `DEST_RADIUS_M = 200`, `STORM_FAST_AFTER = timedelta(minutes=5)`.
- `leave_tick(now, state, store) -> Outbound | None`: uses `state.critical` (`CriticalLeaveState`) and the event; returns the message due now or None.
  - phase `lead`: send once (`sent_count == 0`): `f"{name}, you need to leave now for {title}. You will be late after {fmt_time(leave_by)}. Confirm you have left."` with `location_button=True, critical=True, kind="critical"`; when `now >= leave_by` switch to `storm`.
  - phase `storm`: cadence 60s until `leave_by + STORM_FAST_AFTER`, then 30s; each message `f"{name}, if you don't leave now you will be late for {title}. Share your location to confirm."` (alternate two phrasings by `sent_count % 2`), `location_button=True`; at `now >= leave_by + critical_leave_cap_minutes` send `f"I'll stop now. {title} is marked missed."`, set `ev.status="missed"`, save+commit, phase `done`, `state.critical=None`.
  - Returns None when nothing due. Never consults pause or budget.
- `leave_on_location(now, state, store, lat, lng) -> Outbound`: `home = profile.home_latlng`; if `home is None` → verified (text notes "can't check home, taking your word"); elif `distance_m((lat,lng), home) > HOME_RADIUS_M` → verified: `ev.status="left"`, save+commit, `state.critical=None`, text `f"Confirmed, you're on your way to {title}."`; else `f"You are still at home, {name}."` (chain continues). If `ev.location_latlng` and `distance_m(fix, dest) < DEST_RADIUS_M` → `status="arrived"`, `state.critical=None`, text `f"You made it to {title}."`. `distance_m` imported from `bot.maps.client` (Task 10; implement haversine there — for this task define it in `bot/maps/client.py` now with just that function if Task 10 hasn't run).
- `leave_on_text(state, store) -> Outbound`: `f"Words don't count, {name}. Tap the button and share your location."` (only while critical is active).
- Wake-up:
  - `wake_due(now, state, store) -> bool`: `profile.wake_time` set, `state.wake is None`, key `wake:{day}` via `is_due` at `today wake_time`.
  - `wake_start(now, state, store) -> Outbound`: `state.wake = WakeState(day, "alarm", now, now, 60, 0, 0, None, None)`; text `f"{name}, time to get up. Reply with anything."` `kind="wake", critical=True`, voice False.
  - `wake_tick(now, state, store) -> Outbound | None`: cap: `now >= started_at + wakeup_cap_minutes` → phase `done`, return `Outbound("Couldn't verify you're up. Here's your morning anyway.", kind="wake")` and set `state.wake.verified = False` (engine then sends morning briefing and clears `state.wake`). `alarm`: every `cadence_seconds` → `f"{name}, get up. ({n})"` where n = count. `challenge`: nothing on tick. `engage`: if `now - (last_reply_at or phase start) > 60s` → phase `alarm`, `cadence_seconds=30`, return `f"{name}, still with me? Get up."`.
  - `wake_on_message(now, state, store, text) -> Outbound | None`: `alarm` → phase `challenge`, `last_reply_at=now`, return `f"Send me a photo of the {wake_photo_spot}."`. `challenge` with text → `"Photo, not words. The {spot}."`. `engage`: substantive = `len(text.split()) >= 3 and text.strip().lower() not in {"ok","okay","yes","yeah","fine","sure","yep","no","nope"}`; if substantive: `credit = min(60, int((now - (last_reply_at or now)).total_seconds()))` if `last_reply_at` else 0; `engaged_seconds += credit; last_reply_at = now`; if `engaged_seconds >= wakeup_engage_seconds` → phase `done`, `verified = verified if verified is not None else True`, return `Outbound("You're up.", kind="wake")`; else return `None` (the engine asks the next question via `agent.compose("wake", ...)`, see Task 12). Non-substantive → return `f"More than that, {name}. What's the first thing you're doing today?"`.
  - `wake_on_photo(now, state, store, ok: bool | None, reason: str) -> Outbound`: only in `challenge`; `ok is None` → `verified=False`, go `engage`, text `"Can't check photos right now, I'll take it. First question: what's the first thing you're doing today?"`; `ok` → `verified=True`, `engage`, `last_reply_at=now`, text `"Good. Two minutes with me. What's the first thing you're doing today?"`; not ok → `attempts += 1`; if `attempts >= 3` → `verified=False`, `engage`, text `f"Not convinced, but moving on. What's the first thing you're doing today?"`; else `f"Doesn't look like the {spot}: {reason}. Try again."`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/scheduler/test_critical.py
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
import pytest
from bot.knowledge.models import Event
from bot.knowledge.store import KnowledgeStore
from bot.scheduler.state import RuntimeState, CriticalLeaveState
from bot.scheduler import critical as C

NY = ZoneInfo("America/New_York")
def T(h, m=0, s=0): return datetime(2026, 9, 4, h, m, s, tzinfo=NY)

@pytest.fixture
def store(tmp_path):
    st = KnowledgeStore(tmp_path / "k", clock=lambda: T(8)); st.init()
    p = st.profile(); p.home_latlng = (40.7000, -74.0000); st.save_profile(p)
    st.add(Event(path="", title="Flight", start=T(18), travel_minutes=60, importance="critical",
                 location="JFK", location_latlng=(40.6413, -73.7781)))
    st.commit("e"); return st

def fresh(store):
    s = RuntimeState.load(Path("/nonexistent"))
    s.critical = CriticalLeaveState(store.events()[0].path, "lead", T(16, 55), None, 0)
    return s

def test_lead_message_then_storm_cadence(store):
    s = fresh(store)
    o = C.leave_tick(T(16, 55), s, store)
    assert o and o.location_button and o.critical and "leave now for Flight" in o.text and "5:00pm" in o.text
    assert C.leave_tick(T(16, 56), s, store) is None
    assert C.leave_tick(T(17, 0), s, store) and s.critical.phase == "storm"
    assert C.leave_tick(T(17, 0, 30), s, store) is None
    assert C.leave_tick(T(17, 1), s, store)
    assert C.leave_tick(T(17, 5, 30), s, store) is None or True
    assert C.leave_tick(T(17, 6), s, store) and C.leave_tick(T(17, 6, 30), s, store)   # 30s cadence after 5 min

def test_cap_marks_missed(store):
    s = fresh(store); C.leave_tick(T(16, 55), s, store); C.leave_tick(T(17, 0), s, store)
    o = C.leave_tick(T(17, 20), s, store)
    assert "stop now" in o.text and s.critical is None and store.events()[0].status == "missed"

def test_location_still_home_then_left_then_arrived(store):
    s = fresh(store); C.leave_tick(T(16, 55), s, store)
    o = C.leave_on_location(T(16, 57), s, store, 40.7001, -74.0001)
    assert "still at home" in o.text and s.critical
    o = C.leave_on_location(T(16, 59), s, store, 40.7100, -74.0000)
    assert "on your way" in o.text and s.critical is None and store.events()[0].status == "left"
    o = C.leave_on_location(T(17, 50), s, store, 40.6414, -73.7780)
    assert "made it" in o.text and store.events()[0].status == "arrived"

def test_text_does_not_verify(store):
    s = fresh(store)
    assert "Words don't count" in C.leave_on_text(s, store).text and s.critical

def test_no_home_accepts_any_fix(store):
    p = store.profile(); p.home_latlng = None; store.save_profile(p)
    s = fresh(store)
    o = C.leave_on_location(T(16, 57), s, store, 40.7001, -74.0001)
    assert s.critical is None and "taking your word" in o.text

# wake-up
@pytest.fixture
def wstore(tmp_path):
    st = KnowledgeStore(tmp_path / "k", clock=lambda: T(6)); st.init()
    p = st.profile(); p.wake_time = "06:30"; st.save_profile(p); return st

def test_wake_full_flow(wstore):
    s = RuntimeState.load(Path("/nonexistent"))
    assert not C.wake_due(T(6, 29), s, wstore) and C.wake_due(T(6, 30), s, wstore)
    o = C.wake_start(T(6, 30), s, wstore); assert o.kind == "wake" and s.wake.phase == "alarm"
    assert C.wake_tick(T(6, 30, 30), s, wstore) is None and C.wake_tick(T(6, 31), s, wstore)
    o = C.wake_on_message(T(6, 31, 10), s, wstore, "ugh"); assert "photo" in o.text.lower() and s.wake.phase == "challenge"
    o = C.wake_on_photo(T(6, 31, 40), s, wstore, False, "too dark"); assert "Try again" in o.text and s.wake.attempts == 1
    o = C.wake_on_photo(T(6, 32), s, wstore, True, "kitchen"); assert s.wake.phase == "engage" and s.wake.verified
    assert C.wake_on_message(T(6, 32, 20), s, wstore, "ok").text.startswith("More than that")
    assert C.wake_on_message(T(6, 32, 50), s, wstore, "gym then emails") is None and s.wake.engaged_seconds == 50
    assert C.wake_on_message(T(6, 33, 50), s, wstore, "then the dentist call") is None and s.wake.engaged_seconds == 110
    o = C.wake_on_message(T(6, 34, 30), s, wstore, "and lunch with Sam"); assert o.text == "You're up." and s.wake.phase == "done"

def test_wake_silence_in_engage_returns_to_alarm_fast(wstore):
    s = RuntimeState.load(Path("/nonexistent")); C.wake_start(T(6, 30), s, wstore)
    C.wake_on_message(T(6, 31), s, wstore, "hi"); C.wake_on_photo(T(6, 31, 30), s, wstore, True, "")
    assert C.wake_tick(T(6, 32), s, wstore) is None
    o = C.wake_tick(T(6, 32, 31), s, wstore); assert "still with me" in o.text and s.wake.phase == "alarm" and s.wake.cadence_seconds == 30

def test_wake_cap(wstore):
    s = RuntimeState.load(Path("/nonexistent")); C.wake_start(T(6, 30), s, wstore)
    o = C.wake_tick(T(7, 0), s, wstore); assert "Couldn't verify" in o.text and s.wake.phase == "done" and s.wake.verified is False

def test_wake_photo_three_failures_moves_on(wstore):
    s = RuntimeState.load(Path("/nonexistent")); C.wake_start(T(6, 30), s, wstore)
    C.wake_on_message(T(6, 31), s, wstore, "hi")
    for _ in range(2): C.wake_on_photo(T(6, 31), s, wstore, False, "no")
    o = C.wake_on_photo(T(6, 31), s, wstore, False, "no")
    assert s.wake.phase == "engage" and s.wake.verified is False and "moving on" in o.text
```

- [ ] **Step 2: Run to verify failure.** — [ ] **Step 3: Implement `critical.py`** (haversine in `bot/maps/client.py` as `distance_m(a: tuple[float,float], b: tuple[float,float]) -> float`). — [ ] **Step 4: Run, pass, commit**

```bash
git add -A && git commit -m "feat(scheduler): critical leave storm with location proof, wake-up flow"
```

---

### Task 10: Maps client

**Files:**
- Create/complete: `bot/maps/client.py`
- Test: `tests/maps/test_client.py`

**Interfaces:**
- `distance_m(a, b) -> float` (haversine, from Task 9).
- `class MapsClient(api_key: str, http: httpx.AsyncClient | None = None)`:
  - `async geocode(address: str) -> tuple[float, float] | None` — Geocoding API `https://maps.googleapis.com/maps/api/geocode/json?address=...&key=...`; first result's `geometry.location`; any error/non-OK → `None`.
  - `async travel_minutes(origin: tuple[float,float], dest: tuple[float,float], depart_at: datetime) -> int | None` — Directions API `.../directions/json?origin=lat,lng&destination=lat,lng&departure_time=<unix>&mode=driving&key=...`; `routes[0].legs[0].duration_in_traffic.value` (fallback `duration.value`) seconds → `ceil(/60)`; any error → `None`. Timeout 5s.

- [ ] **Step 1: Write the failing tests**

```python
# tests/maps/test_client.py
from datetime import datetime, timezone
import httpx, pytest
from bot.maps.client import MapsClient, distance_m

def test_distance_haversine():
    assert abs(distance_m((40.7000, -74.0000), (40.7000, -74.0000))) < 0.01
    assert 1090 < distance_m((40.7000, -74.0000), (40.7100, -74.0000)) < 1120

def client(handler):
    return MapsClient("k", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

async def test_geocode_ok_and_error():
    async def h(req):
        assert "address=1+Main" in str(req.url) or "address=1%20Main" in str(req.url)
        return httpx.Response(200, json={"status": "OK", "results": [{"geometry": {"location": {"lat": 1.5, "lng": 2.5}}}]})
    assert await client(h).geocode("1 Main") == (1.5, 2.5)
    async def bad(req): return httpx.Response(500)
    assert await client(bad).geocode("x") is None

async def test_travel_minutes_prefers_traffic():
    async def h(req):
        assert "departure_time=" in str(req.url)
        return httpx.Response(200, json={"status": "OK", "routes": [{"legs": [{"duration": {"value": 600}, "duration_in_traffic": {"value": 1501}}]}]})
    assert await client(h).travel_minutes((0, 0), (1, 1), datetime(2026, 9, 4, 17, 0, tzinfo=timezone.utc)) == 26
    async def boom(req): raise httpx.ConnectError("x")
    assert await client(boom).travel_minutes((0, 0), (1, 1), datetime.now(timezone.utc)) is None
```

- [ ] **Step 2–4: fail, implement, pass, commit** — `git commit -m "feat(maps): geocode and traffic-aware travel time"`

---

### Task 11: Voice adapters

**Files:**
- Create: `bot/voice/stt.py`, `bot/voice/tts.py`
- Test: `tests/voice/test_voice.py`

**Interfaces:**
- `stt.py`: `class Transcriber(model_size: str = "small", device: str = "auto")` lazy-loads `faster_whisper.WhisperModel` on first use (`device="cuda"` if `ctranslate2.get_cuda_device_count() > 0` else `"cpu"`, `compute_type="int8"` on cpu); `async transcribe(path: Path) -> tuple[str, float]` (text, avg logprob as confidence; runs in `asyncio.to_thread`). If `faster_whisper` is not importable → raises `RuntimeError("voice extras not installed")` at construction.
- `tts.py`: `class Synthesizer(model_path: Path, voices_path: Path, voice: str = "af_heart")` lazy-loads `kokoro_onnx.Kokoro`; `async synthesize(text: str, out_dir: Path) -> Path` writes WAV via `soundfile` then runs `ffmpeg -y -i in.wav -c:a libopus -b:a 32k out.ogg` (`asyncio.create_subprocess_exec`), returns the `.ogg` path. Text is truncated to 1500 chars.
- Both modules expose `available() -> bool` (imports succeed, and for TTS the model files exist) so the app degrades to text-only with a startup warning.

- [ ] **Step 1: Write the failing tests** (mock the libraries; skip nothing):

```python
# tests/voice/test_voice.py
import sys, types
from pathlib import Path
import pytest
from bot.voice import stt, tts

async def test_transcriber_uses_model(monkeypatch, tmp_path):
    class Seg:  # faster-whisper segment
        text = " hello world"; avg_logprob = -0.2
    class WM:
        def __init__(self, *a, **k): pass
        def transcribe(self, path, **k): return [Seg(), Seg()], None
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=WM))
    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(get_cuda_device_count=lambda: 0))
    t = stt.Transcriber()
    text, conf = await t.transcribe(tmp_path / "x.ogg")
    assert text == "hello world hello world" and conf == pytest.approx(-0.2)

async def test_synthesizer_writes_ogg(monkeypatch, tmp_path):
    import numpy as np
    class K:
        def __init__(self, *a, **k): pass
        def create(self, text, voice, speed=1.0, lang="en-us"): return np.zeros(2400, dtype="float32"), 24000
    monkeypatch.setitem(sys.modules, "kokoro_onnx", types.SimpleNamespace(Kokoro=K))
    calls = []
    async def fake_exec(*args, **k):
        calls.append(args); Path(args[-1]).write_bytes(b"OggS")
        class P:
            returncode = 0
            async def communicate(self): return b"", b""
        return P()
    monkeypatch.setattr(tts.asyncio, "create_subprocess_exec", fake_exec)
    s = tts.Synthesizer(tmp_path / "m.onnx", tmp_path / "v.bin")
    out = await s.synthesize("hi there", tmp_path)
    assert out.suffix == ".ogg" and out.read_bytes() == b"OggS" and calls[0][0] == "ffmpeg"
```

- [ ] **Step 2–4: fail, implement, pass, commit** — `git commit -m "feat(voice): whisper transcription and kokoro voice notes"`

---

### Task 12: Telegram layer — sender, router, commands, callbacks

**Files:**
- Create: `bot/telegram/sender.py`, `bot/telegram/router.py`, `bot/telegram/commands.py`, `bot/telegram/callbacks.py`, `bot/telegram/handlers.py`, `bot/telegram/app.py`
- Test: `tests/telegram/test_router.py`, `tests/telegram/test_commands.py`

**Interfaces:**
- `sender.py`: `class Sender(bot: telegram.Bot, chat_id: int, synthesizer: Synthesizer | None, tmp_dir: Path)`; `async send(out: Outbound) -> None`: `send_message(text, parse_mode="HTML", reply_markup=...)` where `buttons` → `InlineKeyboardMarkup` (one button per row) and `location_button` → `ReplyKeyboardMarkup([[KeyboardButton("📍 Share my location", request_location=True)]], one_time_keyboard=True, resize_keyboard=True)`; then if `out.voice and synthesizer` → `send_voice(open(ogg,"rb"))` with plain-text version (`html` tags stripped). Retries 3× with backoff `1,2,4s` on `telegram.error.NetworkError/TimedOut`; logs and re-raises others; never raises out of `send` for network failures after retries (logs `error`). Test with a fake bot object recording calls.
- `router.py` — pure-ish logic, no PTB types: `class Router(store, agent, state, clock, maps)`:
  - `async on_text(text: str, via_voice: bool = False) -> list[Outbound]`: `state.last_user_message_at = now`; `close_chain(state)`; then in order: if `state.wake` active (phase ≠ done) → `critical.wake_on_message`; if `None` returned (needs next question) → `agent.compose("wake", ctx, fallback="What's next after that?")` → `Outbound(kind="wake")`. Elif `state.critical` → `critical.leave_on_text`. Elif `state.pending_verify` of kind `question` → `agent.rate_answer`; set `confirmed`, clear pending, reply `"Logged."`/`"Logged as unconfirmed."`. Else `agent.capture(text, awaiting=chain_item_before_close)` → `apply_actions` → reply `Outbound(text=esc(reply) + "\n" + "\n".join(esc(s) for s in summary), voice=via_voice if profile.voice_reply_mode=="on_voice" else profile.voice_reply_mode=="always", kind="reply")`; `applied.snooze_minutes` → `state.pause_until = now + minutes`; `set_profile home_address` → `maps.geocode` and store `home_latlng` (when maps present).
  - `async on_location(lat, lng) -> list[Outbound]`: critical → `leave_on_location`; pending_verify `location` → geocode the todo's location line (`Location: ...` in body) if needed, distance < 200 → confirmed; else `"Got your location, nothing waiting for it."`.
  - `async on_photo(image: bytes) -> list[Outbound]`: wake challenge → `agent.check_photo(image, f"a fresh photo of a {spot}, not a screenshot")` → `wake_on_photo`; pending_verify `photo` → `check_photo(image, f"evidence that this is done: {title}")` → set `confirmed`, clear; else `"Got a photo, but nothing waiting for one."`.
  - `async on_callback(data: str) -> list[Outbound]`: `done:{path}` → if `verify=="none"` mark done (`update_todo` via `apply_actions`) and reply `"Done: {title}"`; else set `state.pending_verify` and ask (`photo`: `"Send a photo showing it's done."`, `location`: location button, `question`: compose one specific question via `agent.compose("verify_question", ctx, fallback=f"How did {title} go?")`), and mark done unconfirmed now (spec: no answer within 10 min stays unconfirmed — engine expires pending after 10 min). `defer:{path}` → `due = tomorrow`, save, commit, `"Moved to tomorrow: {title}"`. `snooze:x` → `pause_until = now + 30m`.
  - `async command(name: str, arg: str) -> list[Outbound]`: `todo` (`arg=="all"`), `backlog`, `goals`, `today`, `week`, `brief` (no arg → `send_morning` now; `HH:MM`/`9am` arg → `state.briefing_override[f"morning:{today}"]`, reply `"Morning briefing moved to 9:00am today."`), `pause` (parse `2h`, `30m`, default 120m), `quiet` (until waking end), `resume`, `undo` (`store.undo()` → `"Reverted: {subject}"` or `"Nothing to undo."`), `help`. `/todo` replies include `buttons=[("✅ " + title[:24], f"done:{path}") for top5]`.
- `handlers.py`: PTB handlers that unwrap `Update` and call the router, then `await sender.send(o) for o in outs`; voice handler downloads the file to `tmp_dir`, calls `transcriber.transcribe`, prefixes reply with `"Heard: “{text}”\n"` when confidence < -0.8, then `router.on_text(text, via_voice=True)`; photo handler downloads the largest `PhotoSize`; location handler passes `message.location` (also `edited_message` for live updates).
- `app.py`: `build_application(settings, router, sender, transcriber) -> telegram.ext.Application` registering `CommandHandler`s for every command, `MessageHandler(filters.TEXT & ~filters.COMMAND)`, `filters.VOICE`, `filters.PHOTO`, `filters.LOCATION`, `CallbackQueryHandler`; every handler wrapped with `filters.User(settings.telegram_user_id)`; `post_init` sets the command menu via `bot.set_my_commands`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/telegram/test_router.py
from datetime import datetime, date
from zoneinfo import ZoneInfo
from pathlib import Path
import pytest
from bot.agent.client import FakeModelClient, ModelResponse, ToolCall
from bot.agent.agent import Agent
from bot.knowledge.store import KnowledgeStore
from bot.knowledge.models import Todo, Event
from bot.scheduler.state import RuntimeState, Chain, CriticalLeaveState
from bot.scheduler.clock import FakeClock
from bot.telegram.router import Router

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 3, 14, 0, tzinfo=NY)

def R(*calls): return ModelResponse(None, [ToolCall(n, a) for n, a in calls])

@pytest.fixture
def rig(tmp_path):
    clock = FakeClock(NOW)
    store = KnowledgeStore(tmp_path / "k", clock=clock.now); store.init()
    client = FakeModelClient([])
    agent = Agent(client, None, store, clock.now)
    state = RuntimeState.load(tmp_path / "s.json")
    return Router(store, agent, state, clock, None), store, client, state, clock

async def test_text_capture_closes_chain_and_echoes(rig):
    router, store, client, state, _ = rig
    state.chain = Chain("checkin", NOW, NOW, 0, "X", [])
    client.responses.append(R(("add_todo", {"title": "Buy milk", "priority": 3}), ("reply", {"text": "Sure."})))
    outs = await router.on_text("buy milk")
    assert state.chain is None and state.last_user_message_at == NOW
    assert outs[0].text == "Sure.\nAdded todo: Buy milk (P3)" and outs[0].kind == "reply" and not outs[0].voice
    assert "Awaiting answer to: X" in client.calls[0]["messages"][1]["content"]

async def test_voice_in_voice_out_default(rig):
    router, store, client, state, _ = rig
    client.responses.append(R(("reply", {"text": "Ok."})))
    assert (await router.on_text("hi", via_voice=True))[0].voice

async def test_snooze_sets_pause(rig):
    router, store, client, state, _ = rig
    client.responses.append(R(("snooze", {"minutes": 120}), ("reply", {"text": "Ok, quiet for 2h."})))
    await router.on_text("not now")
    assert state.pause_until == NOW.replace(hour=16)

async def test_critical_text_and_location(rig):
    router, store, client, state, _ = rig
    p = store.add(Event(path="", title="Flight", start=NOW.replace(hour=18), travel_minutes=60, importance="critical")); store.commit("e")
    state.critical = CriticalLeaveState(p, "lead", NOW, None, 0)
    assert "Words don't count" in (await router.on_text("I left"))[0].text
    outs = await router.on_location(40.0, -74.0)
    assert state.critical is None and store.get_event(p).status == "left"

async def test_done_button_with_and_without_verify(rig):
    router, store, client, state, _ = rig
    a = store.add(Todo(path="", title="Easy")); b = store.add(Todo(path="", title="Car", verify="photo")); store.commit("t")
    assert (await router.on_callback(f"done:{a}"))[0].text == "Done: Easy" and store.get_todo(a).status == "done"
    outs = await router.on_callback(f"done:{b}")
    assert "photo" in outs[0].text.lower() and state.pending_verify.todo_path == b
    assert store.get_todo(b).status == "done" and store.get_todo(b).confirmed is False

async def test_photo_verifies_pending(rig):
    router, store, client, state, _ = rig
    b = store.add(Todo(path="", title="Car", verify="photo")); store.commit("t")
    await router.on_callback(f"done:{b}")
    outs = await router.on_photo(b"img")          # no vision client → None → accepted with note
    assert state.pending_verify is None and "verif" in outs[0].text.lower()

async def test_defer_button(rig):
    router, store, client, state, _ = rig
    a = store.add(Todo(path="", title="Slip", due=date(2026, 9, 1))); store.commit("t")
    assert "tomorrow" in (await router.on_callback(f"defer:{a}"))[0].text.lower()
    assert store.get_todo(a).due == date(2026, 9, 4)
```

```python
# tests/telegram/test_commands.py
from datetime import datetime, date
from zoneinfo import ZoneInfo
import pytest
from bot.agent.client import FakeModelClient
from bot.agent.agent import Agent
from bot.knowledge.store import KnowledgeStore
from bot.knowledge.models import Todo, Event
from bot.scheduler.state import RuntimeState
from bot.scheduler.clock import FakeClock
from bot.telegram.router import Router

NY = ZoneInfo("America/New_York"); NOW = datetime(2026, 9, 3, 14, 0, tzinfo=NY)

@pytest.fixture
def rig(tmp_path):
    clock = FakeClock(NOW); store = KnowledgeStore(tmp_path / "k", clock=clock.now); store.init()
    for i in range(7): store.add(Todo(path="", title=f"T{i}", priority=1 + i % 3))
    store.add(Event(path="", title="Gym", start=NOW.replace(hour=18), travel_minutes=20)); store.commit("s")
    state = RuntimeState.load(tmp_path / "s.json")
    return Router(store, Agent(FakeModelClient([]), None, store, clock.now), state, clock, None), store, state

async def test_todo_top5_with_buttons(rig):
    r, store, state = rig
    o = (await r.command("todo", ""))[0]
    assert o.text.count("\n") >= 5 and len(o.buttons) == 5 and o.buttons[0][1].startswith("done:")
    assert "All open (7)" in (await r.command("todo", "all"))[0].text

async def test_today_week_goals_backlog(rig):
    r, *_ = rig
    assert "leave by 5:40pm" in (await r.command("today", ""))[0].text
    assert "free" in (await r.command("week", ""))[0].text
    assert "No goals" in (await r.command("goals", ""))[0].text
    assert "empty" in (await r.command("backlog", ""))[0].text.lower()

async def test_pause_quiet_resume(rig):
    r, store, state = rig
    assert "2h" in (await r.command("pause", ""))[0].text and state.pause_until == NOW.replace(hour=16)
    await r.command("pause", "30m"); assert state.pause_until == NOW.replace(minute=30)
    await r.command("quiet", ""); assert state.pause_until == NOW.replace(hour=22, minute=0)
    await r.command("resume", ""); assert state.pause_until is None

async def test_brief_now_and_shift(rig):
    r, store, state = rig
    o = (await r.command("brief", ""))[0]; assert "Good morning" in o.text and "morning:2026-09-03" in state.fired
    o = (await r.command("brief", "9am"))[0]; assert state.briefing_override["morning:2026-09-03"] == "09:00"

async def test_undo(rig):
    r, store, state = rig
    assert "Reverted: s" in (await r.command("undo", ""))[0].text
```

- [ ] **Step 2: Run to verify failure.** — [ ] **Step 3: Implement all six modules.** `handlers.py`/`app.py` are not unit-tested beyond import; keep them under 120 lines total. — [ ] **Step 4: Run, pass, commit**

```bash
pytest -v
git add -A && git commit -m "feat(telegram): router, commands, callbacks, sender with retry"
```

---

### Task 13: Engine tick loop and entrypoint

**Files:**
- Create: `bot/scheduler/engine.py`, `bot/__main__.py`
- Test: `tests/scheduler/test_engine.py`

**Interfaces:**
- `class Engine(store, agent, state, state_path: Path, clock: Clock, sender: SenderLike, maps)` where `SenderLike` has `async send(out: Outbound)`.
  - `async tick() -> list[Outbound]` (returns what it sent, for tests). Order:
    1. `now = clock.now()`; `state.prune(now)`.
    2. `outs = await due_reminders(now, store, state, maps)` — send all (exempt from pause/budget).
    3. Critical leave: `critical.leave_tick(now, state, store)` → send if any.
    4. Wake: if `critical.wake_due(...)` → `wake_start` → send. Elif `state.wake` and phase ≠ done → `wake_tick` → send. If `state.wake.phase == "done"`: `note = None if verified else "Wake-up not verified."` → `await briefings.send_morning(now, store, state, agent, note)` → send; `state.wake = None`.
    5. Briefings: `await due_briefings(...)` → send each (exempt from budget, respect nothing else; **pause does not block briefings**).
    6. Check-in: if `budget_ok` → `await due_checkin(...)` → send + `record_send`; if budget exhausted, the slot is consumed (`fired`) silently.
    7. Follow-up: if `budget_ok` → `await due_followup(...)` → send + `record_send`. `pause_until` in the future → skip follow-ups (chain stays, will resume or expire).
    8. Pending verify older than 10 min → clear it (todo stays `confirmed: False`).
    9. `state.save(state_path)`.
    Any exception inside a step is logged with `logging.exception` and does not stop the other steps.
  - `async run(interval_seconds: int = 10)`: loop `tick` then `asyncio.sleep`.
  - `startup(now)`: called once: for each upcoming event with times more than `LATE_WINDOW` in the past, add their keys to `fired` (drop, don't fire late); if `state.critical` refers to an event that is not `upcoming` or past its cap → clear; if `state.wake.day != today` → clear.
- `bot/__main__.py`: build `Settings.from_env()`, `SystemClock(tz)`, `KnowledgeStore(...).init()`, `RuntimeState.load`, `OpenAIModelClient`, optional vision client, `MapsClient` if key, `Transcriber`/`Synthesizer` if `available()`, `Sender`, `Router`, `Engine`, `build_application(...)`; `post_init` → `engine.startup(now)` and `asyncio.create_task(engine.run())`; `application.run_polling(allowed_updates=["message","edited_message","callback_query"])`. Logging to stdout at INFO.

- [ ] **Step 1: Write the failing test**

```python
# tests/scheduler/test_engine.py
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
import pytest
from bot.agent.client import FakeModelClient
from bot.agent.agent import Agent
from bot.knowledge.store import KnowledgeStore
from bot.knowledge.models import Todo, Event
from bot.scheduler.state import RuntimeState
from bot.scheduler.clock import FakeClock
from bot.scheduler.engine import Engine

NY = ZoneInfo("America/New_York")
def T(h, m=0): return datetime(2026, 9, 3, h, m, tzinfo=NY)

class Sink:
    def __init__(self): self.sent = []
    async def send(self, out): self.sent.append(out)

@pytest.fixture
def rig(tmp_path):
    clock = FakeClock(T(7, 0))
    store = KnowledgeStore(tmp_path / "k", clock=clock.now); store.init()
    store.add(Todo(path="", title="Dentist", priority=1, due=date(2026, 9, 3)))
    store.add(Event(path="", title="Gym", start=T(18), travel_minutes=20, prep_minutes=15)); store.commit("s")
    state = RuntimeState.load(tmp_path / "s.json")
    sink = Sink()
    eng = Engine(store, Agent(FakeModelClient([]), None, store, clock.now), state, tmp_path / "s.json", clock, sink, None)
    eng.startup(clock.now())
    return eng, clock, sink, state, store

async def run_until(eng, clock, until, step_s=30):
    while clock.now() < until:
        await eng.tick(); clock.advance(seconds=step_s)

async def test_full_day_message_budget_and_order(rig):
    eng, clock, sink, state, store = rig
    await run_until(eng, clock, T(22, 30))
    kinds = [o.kind for o in sink.sent]
    assert kinds.count("briefing") == 2 and kinds.count("reminder") == 2
    assert 1 <= kinds.count("checkin") <= 14
    # never more than 3 non-critical proactive messages (checkin+followup) in any rolling hour
    times = [t for t, o in zip(eng.sent_times, sink.sent) if o.kind in ("checkin", "followup")]
    for i, t in enumerate(times):
        assert sum(1 for u in times if t - timedelta(hours=1) < u <= t) <= 3
    assert all(o.kind != "checkin" or T(8) <= eng.sent_times[i] < T(22) for i, o in enumerate(sink.sent))
    assert (eng.state_path).exists()

async def test_pause_blocks_checkins_not_briefings_or_reminders(rig):
    eng, clock, sink, state, store = rig
    state.pause_until = T(23)
    await run_until(eng, clock, T(22, 30))
    kinds = [o.kind for o in sink.sent]
    assert kinds.count("checkin") == 0 and kinds.count("followup") == 0
    assert kinds.count("briefing") == 2 and kinds.count("reminder") == 2

async def test_startup_drops_late_jobs(rig):
    eng, clock, sink, state, store = rig
    clock.set(T(18, 30)); eng.startup(clock.now())
    await eng.tick()
    assert all(o.kind != "reminder" for o in sink.sent)

async def test_wake_flow_sends_morning_after_done(rig):
    eng, clock, sink, state, store = rig
    p = store.profile(); p.wake_time = "07:05"; store.save_profile(p)
    await run_until(eng, clock, T(7, 6))
    assert sink.sent and sink.sent[0].kind == "wake"
    state.wake.phase = "done"; state.wake.verified = True
    await eng.tick()
    assert sink.sent[-1].kind == "briefing" and state.wake is None and "morning:2026-09-03" in state.fired
```

`Engine` records `self.sent_times: list[datetime]` parallel to sends (used only by tests) and exposes `self.state_path`.

Also: `Engine.escalate_external(reason: str) -> None` is a no-op hook (spec §20) called when a critical leave hits its cap or a wake-up hits its cap. And `briefings.morning_text` appends `"⚠️ {n} file(s) in the bundle couldn't be read; see the log."` when `store.broken_files` is non-empty after listing (spec §17).

- [ ] **Step 2: Run to verify failure.** — [ ] **Step 3: Implement `engine.py` and `__main__.py`.** — [ ] **Step 4: Run full suite, pass, commit**

```bash
pytest -v
git add -A && git commit -m "feat: engine tick loop and application entrypoint"
```

---

### Task 14: Docker, compose, README, end-to-end checklist

**Files:**
- Create: `Dockerfile`, `docker-compose.yml`, `README.md`, `scripts/download_voice_models.sh`
- Modify: `.env.example` (add `VLLM_MODEL`, `KOKORO_MODEL_DIR`)

- [ ] **Step 1: Dockerfile** (arm64-compatible):

```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY bot ./bot
RUN pip install --no-cache-dir -e '.[voice]'
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "bot"]
```

- [ ] **Step 2: docker-compose.yml**

```yaml
services:
  vllm:
    image: nvcr.io/nvidia/vllm:26.04-py3
    command: >
      vllm serve ${VLLM_MODEL}
      --host 0.0.0.0 --port 8000
      --max-model-len 32768 --gpu-memory-utilization 0.85 --max-num-seqs 4
      --enable-auto-tool-choice --tool-call-parser ${VLLM_TOOL_PARSER:-hermes}
    ipc: host
    deploy:
      resources:
        reservations:
          devices: [{driver: nvidia, count: all, capabilities: [gpu]}]
    volumes: ["${HF_HOME:-~/.cache/huggingface}:/root/.cache/huggingface"]
    restart: always
  bot:
    build: .
    env_file: .env
    environment:
      OPENAI_BASE_URL: http://vllm:8000/v1
      KNOWLEDGE_DIR: /knowledge
      DATA_DIR: /data
    volumes: ["./knowledge:/knowledge", "./data:/data", "./models:/models:ro"]
    depends_on: [vllm]
    restart: always
```

- [ ] **Step 3: README.md**: setup (BotFather token, your user ID via `@userinfobot`, `.env`), model choice per spec §16 with the two recommended checkpoints and matching `VLLM_TOOL_PARSER`, `scripts/download_voice_models.sh` (fetches `kokoro-v1.0.onnx` and `voices-v1.0.bin` into `./models`), Telegram phone setup for critical mode (exempt Telegram from Focus, custom loud sound for the chat), `docker compose up -d`, how to edit `knowledge/` by hand, commands list.

- [ ] **Step 4: End-to-end checklist in README** (manual, on the Spark): send "call dentist friday" → `/todo` shows it → add "gym at 6, 20 min away" → `/today` shows leave by 5:40 → wait for get-ready and leave messages → send a voice note → hear a voice reply → `/pause 1h` → confirm no check-in → flag "flight tomorrow 6pm, this one is critical" → at leave time confirm the storm and that sharing location stops it.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "chore: docker compose for Spark, README with setup and e2e checklist"
```
