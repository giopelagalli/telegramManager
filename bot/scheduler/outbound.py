from __future__ import annotations

from dataclasses import dataclass, field

# kind is one of: reminder, checkin, briefing, followup, critical, reply, edit
KINDS = {"reminder", "checkin", "briefing", "followup", "critical", "reply", "edit"}


@dataclass
class Outbound:
    text: str
    voice: bool = False
    buttons: list[tuple[str, str]] = field(default_factory=list)
    location_button: bool = False
    critical: bool = False
    call: bool = False  # also ring the phone (Twilio), when configured
    kind: str = ""
    silent: bool = False
    edit_message_id: int | None = None
    toast: str | None = None
    # life, review, assignments, exams or course:<slug>; the sender resolves it
    channel: str = "life"
    # (chat_id, thread_id) to answer verbatim, for a topic no channel maps to
    target: tuple[int, int | None] | None = None
