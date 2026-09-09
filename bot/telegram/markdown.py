from __future__ import annotations

import re

from bot.knowledge.views import esc

_FENCE_RE = re.compile(r"```(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_LANG_TAG_RE = re.compile(r"^[A-Za-z0-9_+-]+$")

_HEADING_RE = re.compile(r"^(#{1,3})[ \t]*(.+)$", re.MULTILINE)
_BULLET_RE = re.compile(r"^([ \t]*)[*\-+][ \t]+", re.MULTILINE)

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.DOTALL)
_STRIKE_RE = re.compile(r"~~(.+?)~~", re.DOTALL)
_ITALIC_STAR_RE = re.compile(r"\*(?!\s)([^*\n]+?)(?<!\s)\*")
_ITALIC_US_RE = re.compile(r"_(?!\s)([^_\n]+?)(?<!\s)_")

_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")

_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")

_PLACEHOLDER = "\x00{}\x00"


def md_to_html(text: str) -> str:
    """Convert model-written markdown into the HTML subset Telegram renders."""
    working = esc(text)
    stashed: list[str] = []

    def stash(html: str) -> str:
        stashed.append(html)
        return _PLACEHOLDER.format(len(stashed) - 1)

    def fence_sub(m: re.Match) -> str:
        content = m.group(1)
        if content.startswith("\n"):
            content = content[1:]
        else:
            first, sep, rest = content.partition("\n")
            if sep and _LANG_TAG_RE.match(first.strip()):
                content = rest
        return stash(f"<pre>{content.strip('\n')}</pre>")

    working = _FENCE_RE.sub(fence_sub, working)
    working = _INLINE_CODE_RE.sub(lambda m: stash(f"<code>{m.group(1)}</code>"), working)

    working = _HEADING_RE.sub(lambda m: f"<b>{m.group(2)}</b>", working)
    working = _BULLET_RE.sub(lambda m: f"{m.group(1)}• ", working)

    working = _BOLD_RE.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", working)
    working = _STRIKE_RE.sub(lambda m: f"<s>{m.group(1)}</s>", working)
    working = _ITALIC_STAR_RE.sub(lambda m: f"<i>{m.group(1)}</i>", working)
    working = _ITALIC_US_RE.sub(lambda m: f"<i>{m.group(1)}</i>", working)

    working = _LINK_RE.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', working)

    working = _MULTI_NEWLINE_RE.sub("\n\n", working)

    for i, html in enumerate(stashed):
        working = working.replace(_PLACEHOLDER.format(i), html)

    return working
