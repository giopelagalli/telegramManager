"""JD's Telegram HTML as the web contract's `text` + `format`.

The wire allows b i u s code pre a[href] and newlines. Telegram's synonyms map onto that subset,
its other formatting (spoilers, quotes, custom emoji) keeps only the text, and anything Telegram
itself would refuse (an unknown tag, unbalanced tags) goes out plain, the same fallback the
Telegram sender takes.
"""
from __future__ import annotations

from html import escape
from html.parser import HTMLParser

from bot.telegram.sender import plain_text

_SAME = {"b", "i", "u", "s", "code", "pre", "a"}
_SYNONYM = {"strong": "b", "em": "i", "ins": "u", "strike": "s", "del": "s"}
_TEXT_ONLY = {"span", "tg-spoiler", "tg-emoji", "blockquote"}


class _Unparsable(Exception):
    pass


class _Subset(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.open: list[str] = []  # source tag names, to check balance

    def handle_starttag(self, tag, attrs):
        self.open.append(tag)
        if tag in _TEXT_ONLY:
            return
        name = _SYNONYM.get(tag, tag)
        if name not in _SAME:
            raise _Unparsable(tag)
        if name == "a":
            href = dict(attrs).get("href")
            if not href:
                raise _Unparsable("a without href")
            self.out.append(f'<a href="{escape(href, quote=True)}">')
        else:
            self.out.append(f"<{name}>")

    def handle_endtag(self, tag):
        if not self.open or self.open.pop() != tag:
            raise _Unparsable(f"unbalanced </{tag}>")
        if tag not in _TEXT_ONLY:
            self.out.append(f"</{_SYNONYM.get(tag, tag)}>")

    def handle_startendtag(self, tag, attrs):
        raise _Unparsable(tag)

    def handle_data(self, data):
        self.out.append(escape(data, quote=False))


def to_wire(text: str) -> tuple[str, str]:
    """(text, format) for a message JD wrote as Telegram HTML."""
    parser = _Subset()
    try:
        parser.feed(text)
        parser.close()
    except _Unparsable:
        return plain_text(text), "plain"
    if parser.open:
        return plain_text(text), "plain"
    rendered = "".join(parser.out)
    if rendered == escape(plain_text(text), quote=False):
        return plain_text(text), "plain"  # no markup at all: say so
    return rendered, "html"
