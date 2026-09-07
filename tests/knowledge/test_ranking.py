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
