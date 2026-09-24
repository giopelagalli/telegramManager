# 0007 — Owner rules for proactive messages
Date: 2026-09-18, 2026-09-23, 2026-09-24
Decided by: owner
Status: accepted

## Context
JD "texted way too much" and felt botty; later the wrap-up shamed unconfirmed classes and check-ins nagged about calories. These are product rules, not implementation choices; they override any default a model or skill would pick.

## Options
Recommendations at the time (wake-up chains, follow-ups, calorie target in context) were overridden by the owner.

## Decision
- No wake-up alarms. Leave-by reminders cover getting to class.
- Check-ins at 11:00, 15:00, 19:00, text only, no follow-up chains; `set_profile checkin_times` changes them.
- An event whose start passed without a word is not "missed" and is never listed as such.
- Food is mentioned only when something was logged that day; trackers only while set up.
- A todo with a due time gets a reminder an hour and 30 minutes before, with the due time in the text and a Done button, while it is still open.
- Every composed message has the current time in its context; reminders are computed, never guessed.
- Terse voice (`docs/jd-personality.md`): answer, then one instruction, no greetings, no needy check-ins.
- Proactive messages are text only: no voice note on the briefing, the wrap-up or the check-ins (2026-09-24; the `voice_on_proactive` profile flag is gone). Voice stays for replies, when he sent voice or asked for one.

## Consequences
New proactive features must fit these before they ship; the owner decides volume.
