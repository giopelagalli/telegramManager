"""JD's hands on AgentHub: the project tools, the Projects block in the context, the /projects
data, and what gets reported back — one message per turn JD started, a roll-up line for the
briefings, one alert when a project blocks or a turn errors (FR-C1–C3).

The hub is polled, never pushed from: the engine calls `poll()` about once a minute and sends
what it returns. What has been reported lives in `RuntimeState.projects`, so a restart neither
repeats nor loses a report."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from bot.agenthub.client import AgentHubClient, AgentHubError
from bot.knowledge.models import slugify
from bot.knowledge.views import esc
from bot.scheduler.outbound import Outbound

logger = logging.getLogger(__name__)

# The hub's priorities in the owner's words, and back.
ORDER_WORDS = {"interactive": "runs first", "project": "normal", "batch": "when idle"}
ORDER = {"first": "interactive", "normal": "project", "idle": "batch"}
OUTCOME_WORDS = {"stop": "done", "budget-exhausted": "ran out of budget", "error": "failed", "aborted": "was stopped"}

CONTEXT_PROJECTS = 12
CONTEXT_SUMMARY_CHARS = 160
CONTEXT_CHARS = 2500
REPORT_SUMMARY_CHARS = 400
REPORTED_CAP = 200
WATCH_MAX_MS = 3 * 3600 * 1000  # a turn JD fired that never lands is dropped after this


def _ms(now: datetime) -> int:
    return int(now.timestamp() * 1000)


def _clip(text: str, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _bucket(state) -> dict:
    """JD's own bookkeeping in the runtime state: turns being watched, reported, statuses seen."""
    rec = state.projects
    rec.setdefault("watch", {})     # slug → ms the turn was fired
    rec.setdefault("reported", [])  # session ids already reported
    rec.setdefault("seen", {})      # slug → {"status", "error_at"}
    return rec


def merge(manifests: list[dict], briefings: list[dict]) -> list[dict]:
    """One row per project: status and priority from the manifest, the words from its briefing."""
    by_slug = {b.get("slug"): b for b in briefings or [] if isinstance(b, dict)}
    rows = []
    for m in manifests or []:
        slug = m.get("slug")
        if not slug:
            continue
        b = by_slug.get(slug) or {}
        progress = b.get("progress") or {}
        rows.append({
            "slug": slug,
            "title": m.get("title") or b.get("title") or slug,
            "status": m.get("status") or b.get("status") or "?",
            "priority": m.get("priority") or b.get("priority") or "project",
            "summary": b.get("summary") or "",
            "blockers": [str(x) for x in b.get("blockers") or []],
            "next": [str(x) for x in b.get("nextSteps") or []],
            "progress": (progress.get("done"), progress.get("total")) if progress.get("total") else None,
            "last_turn": m.get("lastTurn"),
        })
    return rows


def prd_title(markdown: str | None) -> str | None:
    for line in (markdown or "").splitlines():
        if line.startswith("# "):
            return line[2:].strip() or None
    return None


class Projects:
    def __init__(self, client: AgentHubClient, label: str = "JD"):
        self.client = client
        self.label = label
        self.snapshot: list[dict] | None = None  # the last good read; the context renders from it
        self.outbox: list[Outbound] = []  # finished background work, sent by the engine
        self._tasks: set[asyncio.Task] = set()

    # -- reads -----------------------------------------------------------

    async def refresh(self) -> list[dict] | None:
        try:
            state = await self.client.state()
            briefings = await self.client.briefings()
        except AgentHubError as exc:
            logger.warning("projects refresh failed: %s", exc)
            return None
        self.snapshot = merge(state.get("projects") or [], briefings if isinstance(briefings, list) else [])
        return self.snapshot

    def find(self, name: str) -> dict | None:
        """A project by slug or title, as said: "rosenroot", "the probability engine"."""
        rows = self.snapshot or []
        key = slugify(name or "")
        if key.startswith("the-"):
            key = key[4:]
        if not key:
            return None
        for row in rows:
            if key in (row["slug"], slugify(row["title"])):
                return row
        loose = [r for r in rows if key in r["slug"] or r["slug"] in key or key in slugify(r["title"])]
        return loose[0] if len(loose) == 1 else None

    async def _resolve(self, name: str) -> dict | None:
        if self.find(name) is None:
            await self.refresh()
        return self.find(name)

    def context_block(self) -> str | None:
        """The Projects block for the model: slug, status, order, one line of the briefing."""
        if self.snapshot is None:
            return None
        lines = ["Projects on AgentHub (slug, status, order — use the slug in project tools):"]
        if not self.snapshot:
            lines.append("- none yet")
        for row in self.snapshot[:CONTEXT_PROJECTS]:
            progress = f", {row['progress'][0]}/{row['progress'][1]} milestones" if row["progress"] else ""
            line = f"- {row['slug']} \"{row['title']}\" [{row['status']}, {ORDER_WORDS.get(row['priority'], row['priority'])}{progress}]"
            if row["summary"]:
                line += ": " + _clip(row["summary"], CONTEXT_SUMMARY_CHARS)
            if row["blockers"]:
                line += " Blocked on: " + _clip(row["blockers"][0], 120)
            lines.append(line)
        if len(self.snapshot) > CONTEXT_PROJECTS:
            lines.append(f"- and {len(self.snapshot) - CONTEXT_PROJECTS} more")
        block = "\n".join(lines)
        return block if len(block) <= CONTEXT_CHARS else block[: CONTEXT_CHARS - 1] + "…"

    # -- the tools ---------------------------------------------------------

    async def new(self, title: str, intent: str) -> str:
        """Create now; the PRD draft and the roadmap run in the background and report when done."""
        title = title.strip()
        slug = slugify(title)[:40].strip("-")
        if not slug:
            return "That needs a name."
        try:
            await self.client.create(slug, title, intent.strip() or title, idea=intent.strip() or None)
        except AgentHubError as exc:
            return f"Couldn't start {esc(title)}: {esc(str(exc))}"
        task = asyncio.create_task(self._plan(slug, title))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return f"Started {esc(title)} on AgentHub ({slug}). Drafting the PRD and roadmap; I'll tell you when it's planned."

    async def _plan(self, slug: str, title: str) -> None:
        stage = "the PRD draft"
        try:
            prd = await self.client.draft_prd(slug)
            stage = "the roadmap"
            roadmap = await self.client.generate_roadmap(slug)
        except AgentHubError as exc:
            self.outbox.append(Outbound(f"<b>{esc(title)}</b>: {stage} failed. {esc(str(exc))}", kind="reply"))
            return
        except Exception:
            logger.exception("planning %s failed", slug)
            self.outbox.append(Outbound(f"<b>{esc(title)}</b>: {stage} failed.", kind="reply"))
            return
        heading = prd_title(prd.get("full")) or title
        count = len(roadmap.get("milestones") or [])
        await self.refresh()
        self.outbox.append(Outbound(
            f"<b>{esc(title)}</b> is planned: PRD “{esc(heading)}”, {count} milestone{'s' if count != 1 else ''}. "
            f"Say “run a turn on {slug}” to start.", kind="reply"))

    async def turn(self, state, name: str, instruction: str | None, now: datetime) -> str:
        row = await self._resolve(name)
        if row is None:
            return f"No project called {esc(name)}."
        fired_at = _ms(now)
        try:
            await self.client.start_turn(row["slug"], (instruction or "").strip() or None)
        except AgentHubError as exc:
            return f"{esc(row['title'])}: {esc(str(exc))}"
        _bucket(state)["watch"][row["slug"]] = fired_at
        return f"Turn on {esc(row['title'])} started. I'll message you when it lands."

    async def pause(self, name: str) -> str:
        return await self._lifecycle(name, "pause", "Paused")

    async def resume(self, name: str) -> str:
        return await self._lifecycle(name, "resume", "Resumed")

    async def _lifecycle(self, name: str, action: str, done: str) -> str:
        row = await self._resolve(name)
        if row is None:
            return f"No project called {esc(name)}."
        try:
            await getattr(self.client, action)(row["slug"])
        except AgentHubError as exc:
            return f"{esc(row['title'])}: {esc(str(exc))}"
        await self.refresh()
        return f"{done} {esc(row['title'])}."

    async def priority(self, name: str, order: str) -> str:
        """`order` is the owner's word (first/normal/idle) or the hub's own value."""
        priority = ORDER.get(order, order)
        if priority not in ORDER_WORDS:
            return f"Order is first, normal or idle, not {esc(order)}."
        row = await self._resolve(name)
        if row is None:
            return f"No project called {esc(name)}."
        try:
            await self.client.set_priority(row["slug"], priority)
        except AgentHubError as exc:
            return f"{esc(row['title'])}: {esc(str(exc))}"
        await self.refresh()
        return f"{esc(row['title'])} {ORDER_WORDS[priority]} now."

    # -- reporting ---------------------------------------------------------

    def drain(self) -> list[Outbound]:
        out, self.outbox = self.outbox, []
        return out

    async def poll(self, state, now: datetime, budget_left: int) -> tuple[list[Outbound], list[Outbound]]:
        """(reports, alerts). Reports answer a turn JD started — once each, never budgeted.
        Alerts — a project newly blocked, a turn that errored — spend the proactive budget; one
        that doesn't fit is retried on a later poll."""
        rec = _bucket(state)
        snapshot = await self.refresh()
        reports: list[Outbound] = []
        for slug, fired_at in list(rec["watch"].items()):
            try:
                turns = (await self.client.turns(slug, fired_at)).get("turns") or []
            except AgentHubError as exc:
                logger.warning("turns for %s: %s", slug, exc)
                turns = []
            mine = sorted((t for t in turns if t.get("requestedBy") == self.label and t.get("endedAt") is not None),
                          key=lambda t: t.get("sessionId", 0))
            for t in mine:
                if t.get("sessionId") in rec["reported"]:
                    continue
                rec["reported"].append(t.get("sessionId"))
                reports.append(self._report(slug, t))
                if t.get("outcome") == "error":  # already said; the alert below must not repeat it
                    seen = rec["seen"].setdefault(slug, {"status": None, "error_at": 0})
                    seen["error_at"] = max(seen.get("error_at") or 0, int(t["endedAt"]))
            if mine or _ms(now) - int(fired_at) > WATCH_MAX_MS:
                del rec["watch"][slug]
        del rec["reported"][:-REPORTED_CAP]

        alerts: list[Outbound] = []
        for row in snapshot or []:
            slug = row["slug"]
            last = row["last_turn"] or {}
            errored_at = int(last.get("endedAt") or 0) if last.get("outcome") == "error" else 0
            seen = rec["seen"].get(slug)
            if seen is None or seen.get("status") is None:
                # First sight (a fresh install, a new project): nothing to compare against yet.
                rec["seen"][slug] = {"status": row["status"], "error_at": max(errored_at, (seen or {}).get("error_at") or 0)}
                continue
            if row["status"] == "blocked" and seen["status"] != "blocked":
                if len(alerts) < budget_left:
                    on = f": {esc(_clip(row['blockers'][0], 200))}" if row["blockers"] else "."
                    alerts.append(Outbound(f"<b>{esc(row['title'])}</b> is blocked{on}", kind="reply"))
                    seen["status"] = "blocked"
            elif row["status"] != "blocked":
                seen["status"] = row["status"]
            if errored_at > int(seen.get("error_at") or 0) and len(alerts) < budget_left:
                alerts.append(Outbound(f"<b>{esc(row['title'])}</b>: a turn failed.", kind="reply"))
                seen["error_at"] = errored_at
        return reports, alerts

    def _report(self, slug: str, turn: dict) -> Outbound:
        row = next((r for r in self.snapshot or [] if r["slug"] == slug), None)
        title = row["title"] if row else slug
        outcome = turn.get("outcome") or "stop"
        head = f"<b>{esc(title)}</b> turn {OUTCOME_WORDS.get(outcome, esc(outcome))}"
        summary = _clip(turn.get("summary") or "", REPORT_SUMMARY_CHARS)
        text = f"{head}: {esc(summary)}" if summary else f"{head}."
        if outcome == "stop" and row and row["next"]:
            text += f" Next: {esc(_clip(row['next'][0], 160))}"
        return Outbound(text, kind="reply")

    async def briefing_line(self, since: datetime) -> str | None:
        """"Projects: rosenroot 2 turns, probability-engine blocked on the odds API key." — the
        turns JD didn't start (auto-run, the owner's own) since `since`, and what is blocked."""
        snapshot = await self.refresh()
        if not snapshot:
            return None
        bits = []
        for row in snapshot:
            if row["status"] == "blocked":
                on = f" on {_clip(row['blockers'][0], 120)}" if row["blockers"] else ""
                bits.append(f"{row['slug']} blocked{on}")
                continue
            if row["status"] == "done":
                continue
            try:
                turns = (await self.client.turns(row["slug"], _ms(since))).get("turns") or []
            except AgentHubError:
                continue
            n = sum(1 for t in turns if t.get("requestedBy") != self.label)
            if n:
                bits.append(f"{row['slug']} {n} turn{'s' if n != 1 else ''}")
        return ("Projects: " + ", ".join(bits) + ".") if bits else None
