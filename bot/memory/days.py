"""'yesterday', 'last tuesday', 'sep 20' → the dates meant. For recall that should not depend on
the model deciding to look."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def dates_in(text: str, now: datetime) -> list[date]:
    """Every day the text refers to. Empty when it names none."""
    t = text.lower()
    today = now.date()
    out: set[date] = set()
    if re.search(r"\byesterday(?:'?s)?\b", t):
        out.add(today - timedelta(days=1))
    if re.search(r"\btoday\b|\bthis morning\b|\btonight\b", t):
        out.add(today)
    if re.search(r"\blast week\b", t):
        monday = today - timedelta(days=today.weekday() + 7)
        out.update(monday + timedelta(days=i) for i in range(7))
    if re.search(r"\bthis week\b", t):
        monday = today - timedelta(days=today.weekday())
        out.update(monday + timedelta(days=i) for i in range((today - monday).days + 1))
    for m in re.finditer(r"\b(?:last\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)(?:'?s)?\b", t):
        target = _WEEKDAYS.index(m.group(1))
        back = (today.weekday() - target) % 7 or 7
        out.add(today - timedelta(days=back))
    for m in re.finditer(r"\b(" + "|".join(_MONTHS) + r")[a-z]*\.?\s+(\d{1,2})\b", t):
        month = _MONTHS.index(m.group(1)) + 1
        try:
            d = date(today.year, month, int(m.group(2)))
        except ValueError:
            continue
        if d > today:
            d = d.replace(year=today.year - 1)
        out.add(d)
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", t):
        try:
            out.add(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            pass
    return sorted(out)
