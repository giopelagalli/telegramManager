"""/projects: each AgentHub project as a button → its status and latest briefing, with Run turn,
Pause / Resume and Priority buttons. Callback prefix `proj:`; edits in place like /courses."""
from __future__ import annotations

from datetime import datetime

from bot.agenthub.projects import ORDER, ORDER_WORDS
from bot.knowledge.views import esc
from bot.scheduler.outbound import Outbound

UNREACHABLE = "AgentHub isn't answering right now."


def _out(text: str, buttons, message_id: int | None, toast: str | None = None) -> Outbound:
    return Outbound(text, buttons=buttons, kind="edit" if message_id else "reply",
                    edit_message_id=message_id, toast=toast)


def list_view(projects, message_id: int | None = None) -> Outbound:
    rows = projects.snapshot
    if rows is None:
        return _out(UNREACHABLE, [("↻ Retry", "proj:list")], message_id)
    lines = ["<b>Projects</b>"]
    for r in rows:
        progress = f" · {r['progress'][0]}/{r['progress'][1]}" if r["progress"] else ""
        lines.append(f"• <b>{esc(r['title'])}</b> — {esc(r['status'])} · "
                     f"{ORDER_WORDS.get(r['priority'], esc(r['priority']))}{progress}")
    if not rows:
        lines.append("None yet. Say “start a project: …”.")
    buttons = [(r["title"][:40], f"proj:view:{r['slug']}") for r in rows]
    return _out("\n".join(lines), buttons, message_id)


def project_view(projects, slug: str, message_id: int | None = None, note: str | None = None) -> Outbound:
    row = next((r for r in projects.snapshot or [] if r["slug"] == slug), None)
    if row is None:
        return list_view(projects, message_id)
    lines = [f"<i>{note}</i>"] if note else []  # `note` is already HTML from the service
    progress = f" · {row['progress'][0]}/{row['progress'][1]} milestones" if row["progress"] else ""
    lines += [f"<b>{esc(row['title'])}</b>",
              f"{esc(row['status'])} · {ORDER_WORDS.get(row['priority'], esc(row['priority']))}{progress}"]
    if row["summary"]:
        lines += ["", esc(row["summary"])]
    if row["blockers"]:
        lines += ["", "<b>Blocked on</b>"] + [f"• {esc(b)}" for b in row["blockers"][:3]]
    if row["next"]:
        lines += ["", "<b>Next</b>"] + [f"• {esc(n)}" for n in row["next"][:3]]
    toggle = ("▶ Resume", f"proj:resume:{slug}") if row["status"] == "paused" else ("⏸ Pause", f"proj:pause:{slug}")
    buttons = [("▶ Run turn", f"proj:turn:{slug}"), toggle, ("⚡ Priority", f"proj:prio:{slug}"),
               ("◀ Projects", "proj:list")]
    return _out("\n".join(lines), buttons, message_id)


def priority_view(projects, slug: str, message_id: int | None = None) -> Outbound:
    row = next((r for r in projects.snapshot or [] if r["slug"] == slug), None)
    if row is None:
        return list_view(projects, message_id)
    current = row["priority"]
    lines = [f"<b>{esc(row['title'])}</b>: how it queues for the models.",
             "Runs first: ahead of other projects. Normal: in turn. When idle: only when nothing else runs."]
    buttons = [(("✓ " if ORDER[word] == current else "") + ORDER_WORDS[ORDER[word]].capitalize(),
                f"proj:setprio:{slug}:{word}") for word in ("first", "normal", "idle")]
    buttons.append(("◀ Back", f"proj:view:{slug}"))
    return _out("\n".join(lines), buttons, message_id)


async def handle(arg: str, projects, state, now: datetime, message_id: int | None) -> list[Outbound]:
    if projects is None:
        return [Outbound("AgentHub isn't set up (AGENTHUB_URL and AGENTHUB_TOKEN).", kind="reply")]
    what, _, rest = arg.partition(":")
    slug, _, extra = rest.partition(":")
    if what == "prio" and slug:
        return [priority_view(projects, slug, message_id)]
    note = None
    if what == "turn" and slug:
        note = await projects.turn(state, slug, None, now)
    elif what == "pause" and slug:
        note = await projects.pause(slug)
    elif what == "resume" and slug:
        note = await projects.resume(slug)
    elif what == "setprio" and slug and extra:
        note = await projects.priority(slug, extra)
    else:
        await projects.refresh()
    if slug:
        return [project_view(projects, slug, message_id, note=note)]
    return [list_view(projects, message_id)]
