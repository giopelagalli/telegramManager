from __future__ import annotations

import re
from dataclasses import replace

from bot.knowledge.models import Source

_WORD_RE = re.compile(r"[a-z0-9]+")


def _words(question: str) -> set[str]:
    return {w for w in _WORD_RE.findall(question.lower()) if len(w) >= 4}


def _score(source: Source, words: set[str]) -> int:
    haystack = " ".join([source.title, *source.topics]).lower()
    return sum(1 for w in words if w in haystack)


def select_sources(question: str, sources: list[Source], budget_chars: int) -> list[Source]:
    """Best-matching sources first, until the character budget is spent."""
    words = _words(question)
    ordered = sorted(
        sources,
        key=lambda s: (-_score(s, words), -(s.timestamp.timestamp() if s.timestamp else 0.0)),
    )

    selected: list[Source] = []
    used = 0
    for source in ordered:
        if used + len(source.body) > budget_chars:
            # The best match always goes in, truncated to what the budget allows.
            if not selected:
                selected.append(replace(source, body=source.body[:budget_chars]))
            break
        selected.append(source)
        used += len(source.body)
    return selected
