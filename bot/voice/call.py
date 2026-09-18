"""Phone calls through Twilio, for the moments a buzz is not enough (critical leave-now)."""
from __future__ import annotations

import logging
from xml.sax.saxutils import escape

import httpx

logger = logging.getLogger(__name__)

TWILIO_API = "https://api.twilio.com/2010-04-01"
VOICE = "Polly.Matthew-Generative"  # a natural male voice Twilio serves without any hosting on our side


class TwilioCaller:
    def __init__(self, account_sid: str, auth_token: str, from_number: str, to_number: str, transport=None):
        self._sid = account_sid
        self._from = from_number
        self._to = to_number
        self._client = httpx.AsyncClient(auth=(account_sid, auth_token), timeout=20.0, transport=transport)

    async def call(self, text: str) -> str | None:
        """Ring the user's phone and read `text` twice. Returns the call SID, or None on failure."""
        twiml = (
            f'<Response><Say voice="{VOICE}">{escape(text)}</Say><Pause length="1"/>'
            f'<Say voice="{VOICE}">{escape(text)}</Say></Response>'
        )
        try:
            r = await self._client.post(
                f"{TWILIO_API}/Accounts/{self._sid}/Calls.json",
                data={"To": self._to, "From": self._from, "Twiml": twiml},
            )
            r.raise_for_status()
            return r.json().get("sid")
        except Exception as exc:
            logger.error("twilio call failed: %s", exc)
            return None
