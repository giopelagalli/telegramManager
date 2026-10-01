"""Where an Outbound goes (0010). A connector is anything with `async send(out)`: the Telegram
Sender is one, the web conversation (bot/web/conversation.py) another. Replies go back on the
connector the message came in on; proactive messages go through a Fanout to all of them."""
from __future__ import annotations

import logging
from typing import Protocol

from bot.scheduler.outbound import Outbound

logger = logging.getLogger(__name__)


class Connector(Protocol):
    async def send(self, out: Outbound) -> None: ...


class Fanout:
    """The primary (Telegram) exactly as before, errors included; then every other connector,
    whose failures are logged and never reach the caller."""

    def __init__(self, primary: Connector, *others: Connector):
        self.primary = primary
        self.others = others

    async def send(self, out: Outbound) -> None:
        failure: Exception | None = None
        try:
            await self.primary.send(out)
        except Exception as exc:
            failure = exc
        for connector in self.others:
            try:
                await connector.send(out)
            except Exception:
                logger.exception("%s failed to deliver a proactive message", type(connector).__name__)
        if failure is not None:
            raise failure
