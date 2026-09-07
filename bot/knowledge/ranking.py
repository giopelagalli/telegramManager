from __future__ import annotations

from datetime import date
from typing import Iterable

from bot.knowledge.models import Goal, Todo


def rank_todos(todos: Iterable[Todo], today: date) -> list[Todo]:
    open_todos = [t for t in todos if t.status == "open" and not t.in_backlog]

    def key(t: Todo):
        due_bucket = 0 if t.due and t.due < today else 1 if t.due == today else 2
        return (due_bucket, t.priority, t.due or date.max, t.created, t.path)

    return sorted(open_todos, key=key)


def top(todos: Iterable[Todo], today: date, n: int = 5) -> list[Todo]:
    return rank_todos(todos, today)[:n]


def _goal_key(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def goal_progress(goal: Goal, todos: list[Todo]) -> tuple[int, int]:
    matching = [t for t in todos if t.goal is not None and _goal_key(t.goal) == _goal_key(goal.path)]
    done = sum(1 for t in matching if t.status == "done")
    return done, len(matching)
