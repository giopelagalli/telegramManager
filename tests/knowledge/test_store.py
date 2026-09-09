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

def test_move_todo_avoids_collision(store):
    (store.root / "todos" / "2026-09-03-x.md").write_text(
        Todo(path="todos/2026-09-03-x.md", title="X").to_markdown()
    )
    backlog_path = "backlog/2026-09-03-x.md"
    (store.root / backlog_path).write_text(
        Todo(path=backlog_path, title="X").to_markdown()
    )
    new = store.move_todo(backlog_path, "todos")
    assert new == "todos/2026-09-03-x-2.md"
    assert (store.root / "todos" / "2026-09-03-x.md").exists()
    assert (store.root / "todos" / "2026-09-03-x-2.md").exists()
    assert not (store.root / backlog_path).exists()

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
    result = store.undo()
    assert result is None or "Revert" in result or "Reapply" in result

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


def test_init_sets_identity_on_a_pre_existing_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "absent-global"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(tmp_path / "absent-system"))
    root = tmp_path / "knowledge"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    s = KnowledgeStore(root, clock=lambda: T0)
    s.init()
    s.add(Todo(path="", title="Call dentist"))
    assert s.commit("first commit") is not None

def _bare(tmp_path, name):
    path = tmp_path / name
    subprocess.run(["git", "init", "--bare", "-q", str(path)], check=True)
    return path

def _head(path, ref="HEAD"):
    return subprocess.run(
        ["git", "rev-parse", ref], cwd=path, capture_output=True, text=True, check=True
    ).stdout.strip()

def test_commit_pushes_to_every_remote(tmp_path):
    remotes = [_bare(tmp_path, "a.git"), _bare(tmp_path, "b.git")]
    s = KnowledgeStore(tmp_path / "knowledge", clock=lambda: T0, remotes=[str(r) for r in remotes])
    s.init()
    s.add(Todo(path="", title="Call dentist"))
    s.commit("add todo")
    s.wait_for_pushes()
    for remote in remotes:
        assert _head(remote, "main") == _head(s.root)

def test_bad_remote_does_not_block_the_good_one(tmp_path):
    good = _bare(tmp_path, "good.git")
    bogus = tmp_path / "nope.git"
    s = KnowledgeStore(tmp_path / "knowledge", clock=lambda: T0, remotes=[str(bogus), str(good)])
    s.init()
    s.add(Todo(path="", title="Call dentist"))
    s.commit("add todo")
    s.wait_for_pushes()
    assert _head(good, "main") == _head(s.root)
