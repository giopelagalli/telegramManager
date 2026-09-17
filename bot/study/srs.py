"""SM-2 spaced repetition. Grades are 0-5; 3 and up count as remembered."""
from __future__ import annotations

from datetime import date, timedelta

from bot.knowledge.models import Card


def review(card: Card, grade: int, today: date) -> Card:
    grade = max(0, min(5, int(grade)))
    if grade < 3:
        card.reps = 0
        card.lapses += 1
        card.interval = 1
    else:
        if card.reps == 0:
            card.interval = 1
        elif card.reps == 1:
            card.interval = 6
        else:
            card.interval = round(card.interval * card.ease)
        card.reps += 1
    card.ease = max(1.3, card.ease + 0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02))
    card.due = today + timedelta(days=card.interval)
    card.history.append((today, grade))
    return card


def due_cards(cards: list[Card], today: date, cap: int, prefer_course: str | None = None) -> list[Card]:
    """Due or never-seen cards, oldest due first; a focus course goes to the front."""
    ready = [c for c in cards if c.due is None or c.due <= today]
    ready.sort(key=lambda c: (0 if prefer_course and c.course == prefer_course else 1, c.due or date.min, c.path))
    return ready[:cap]
