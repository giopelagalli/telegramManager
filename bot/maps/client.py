from __future__ import annotations

import logging
import math
from datetime import datetime

import httpx

EARTH_RADIUS_M = 6371000

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
DIRECTIONS_URL = "https://maps.googleapis.com/maps/api/directions/json"

logger = logging.getLogger(__name__)


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1 = math.radians(a[0]), math.radians(a[1])
    lat2, lng2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


class MapsClient:
    def __init__(self, api_key: str, http: httpx.AsyncClient | None = None) -> None:
        self._api_key = api_key
        self._http = http or httpx.AsyncClient(timeout=5.0)

    async def geocode(self, address: str) -> tuple[float, float] | None:
        try:
            resp = await self._http.get(
                GEOCODE_URL, params={"address": address, "key": self._api_key}
            )
            resp.raise_for_status()
            data = resp.json()
            location = data["results"][0]["geometry"]["location"]
            return (location["lat"], location["lng"])
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "maps.geocode failed for %r: HTTP %s", address, exc.response.status_code
            )
            return None
        except (httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
            logger.warning("maps.geocode failed for %r: %s", address, type(exc).__name__)
            return None

    async def travel_minutes(
        self,
        origin: tuple[float, float],
        dest: tuple[float, float],
        depart_at: datetime,
    ) -> int | None:
        try:
            resp = await self._http.get(
                DIRECTIONS_URL,
                params={
                    "origin": f"{origin[0]},{origin[1]}",
                    "destination": f"{dest[0]},{dest[1]}",
                    "departure_time": int(depart_at.timestamp()),
                    "mode": "driving",
                    "key": self._api_key,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            leg = data["routes"][0]["legs"][0]
            duration = leg.get("duration_in_traffic", leg["duration"])
            seconds = duration["value"]
            return math.ceil(seconds / 60)
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "maps.travel_minutes failed for %r -> %r: HTTP %s",
                origin, dest, exc.response.status_code,
            )
            return None
        except (httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
            logger.warning(
                "maps.travel_minutes failed for %r -> %r: %s",
                origin, dest, type(exc).__name__,
            )
            return None
