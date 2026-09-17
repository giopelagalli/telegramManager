from datetime import date
from bot.knowledge.models import Card
from bot.study.srs import review, due_cards

def test_sm2_intervals_grow_and_lapse_resets():
    c = Card(path="x", question="q", answer="a", course="c")
    review(c, 5, date(2026, 9, 1)); assert (c.interval, c.reps, c.due) == (1, 1, date(2026, 9, 2))
    review(c, 4, date(2026, 9, 2)); assert (c.interval, c.reps) == (6, 2)
    review(c, 5, date(2026, 9, 8)); assert c.interval == round(6 * c.ease) and c.reps == 3
    review(c, 1, date(2026, 9, 20)); assert (c.interval, c.reps, c.lapses) == (1, 0, 1) and c.ease >= 1.3
    assert len(c.history) == 4

def test_due_cards_caps_and_prefers_focus_course():
    today = date(2026, 9, 10)
    cards = [Card(path=f"cards/a/{i}", question=str(i), answer="", course="a", due=date(2026, 9, 1 + i)) for i in range(5)]
    cards += [Card(path="cards/b/x", question="x", answer="", course="b", due=None)]
    cards += [Card(path="cards/a/future", question="f", answer="", course="a", due=date(2026, 9, 30))]
    got = due_cards(cards, today, cap=3, prefer_course="b")
    assert [c.path for c in got] == ["cards/b/x", "cards/a/0", "cards/a/1"]
