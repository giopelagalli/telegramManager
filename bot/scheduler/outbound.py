from __future__ import annotations

from dataclasses import dataclass, field

# kind is one of: reminder, checkin, briefing, followup, critical, wake, reply
KINDS = {"reminder", "checkin", "briefing", "followup", "critical", "wake", "reply"}


@dataclass
class Outbound:
    text: str
    voice: bool = False
    buttons: list[tuple[str, str]] = field(default_factory=list)
    location_button: bool = False
    critical: bool = False
    kind: str = ""
