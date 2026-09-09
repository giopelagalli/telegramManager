from __future__ import annotations

from dataclasses import dataclass, field

# kind is one of: reminder, checkin, briefing, followup, critical, wake, reply, edit
KINDS = {"reminder", "checkin", "briefing", "followup", "critical", "wake", "reply", "edit"}


@dataclass
class Outbound:
    text: str
    voice: bool = False
    buttons: list[tuple[str, str]] = field(default_factory=list)
    location_button: bool = False
    critical: bool = False
    kind: str = ""
    silent: bool = False
    edit_message_id: int | None = None
    toast: str | None = None
    # life, review, assignments, exams or course:<slug>; the sender resolves it
    channel: str = "life"
