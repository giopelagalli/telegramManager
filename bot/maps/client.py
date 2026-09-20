from __future__ import annotations

import logging
import math
from datetime import datetime

import httpx

EARTH_RADIUS_M = 6371000

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
DIRECTIONS_URL = "https://maps.googleapis.com/maps/api/directions/json"
PLACES_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"

logger = logging.getLogger(__name__)


def directions_url(destination: str | None, latlng: tuple[float, float] | None = None) -> str | None:
    """A Google Maps navigation link; opens turn-by-turn on a phone. No API key needed."""
    from urllib.parse import quote
    if latlng is not None:
        target = f"{latlng[0]},{latlng[1]}"
    elif destination:
        target = quote(destination)
    else:
        return None
    return f"https://www.google.com/maps/dir/?api=1&destination={target}&travelmode=driving"


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
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(timeout=5.0)

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def _get_json(self, url: str, params: dict, what: str) -> dict | None:
        try:
            resp = await self._http.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("maps.%s failed: HTTP %s", what, exc.response.status_code)
            return None
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("maps.%s failed: %s", what, type(exc).__name__)
            return None

    async def geocode(self, address: str) -> tuple[float, float] | None:
        data = await self._get_json(
            GEOCODE_URL, {"address": address, "key": self._api_key}, "geocode"
        )
        if data is None:
            return None
        try:
            location = data["results"][0]["geometry"]["location"]
            return (location["lat"], location["lng"])
        except (KeyError, IndexError, TypeError) as exc:
            logger.warning("maps.geocode failed: %s", type(exc).__name__)
            return None

    async def find_place(self, query: str, near: tuple[float, float] | None = None) -> dict | None:
        """What "mags" means around here: {"name", "address", "latlng"} for the best match near `near`."""
        params = {"query": query, "key": self._api_key}
        if near is not None:
            params["location"] = f"{near[0]},{near[1]}"
            params["radius"] = 25000
        data = await self._get_json(PLACES_URL, params, "find_place")
        if data is None:
            return None
        if not data.get("results"):
            # ZERO_RESULTS is a real miss; REQUEST_DENIED means the Places API is not enabled on the key.
            logger.warning("maps.find_place: no results for %r: %s %s", query, data.get("status"), data.get("error_message", ""))
            return None
        try:
            hit = data["results"][0]
            loc = hit["geometry"]["location"]
            return {"name": hit["name"], "address": hit.get("formatted_address", ""), "latlng": (loc["lat"], loc["lng"])}
        except (KeyError, IndexError, TypeError) as exc:
            logger.warning("maps.find_place failed: %s", type(exc).__name__)
            return None

    async def route(
        self, origin: tuple[float, float], dest: tuple[float, float], mode: str = "drive"
    ) -> tuple[int, int] | None:
        """(minutes, meters) from origin to dest, walking or driving with live traffic."""
        params = {
            "origin": f"{origin[0]},{origin[1]}",
            "destination": f"{dest[0]},{dest[1]}",
            "mode": "walking" if mode == "walk" else "driving",
            "key": self._api_key,
        }
        if mode != "walk":
            params["departure_time"] = "now"
        data = await self._get_json(DIRECTIONS_URL, params, "route")
        if data is None:
            return None
        try:
            leg = data["routes"][0]["legs"][0]
            duration = leg.get("duration_in_traffic", leg["duration"])
            return math.ceil(duration["value"] / 60), int(leg["distance"]["value"])
        except (KeyError, IndexError, TypeError) as exc:
            logger.warning("maps.route failed: %s", type(exc).__name__)
            return None

    async def travel_minutes(
        self,
        origin: tuple[float, float],
        dest: tuple[float, float],
        depart_at: datetime,
    ) -> int | None:
        data = await self._get_json(
            DIRECTIONS_URL,
            {
                "origin": f"{origin[0]},{origin[1]}",
                "destination": f"{dest[0]},{dest[1]}",
                "departure_time": int(depart_at.timestamp()),
                "mode": "driving",
                "key": self._api_key,
            },
            "travel_minutes",
        )
        if data is None:
            return None
        try:
            leg = data["routes"][0]["legs"][0]
            duration = leg.get("duration_in_traffic", leg["duration"])
            seconds = duration["value"]
            return math.ceil(seconds / 60)
        except (KeyError, IndexError, TypeError) as exc:
            logger.warning("maps.travel_minutes failed: %s", type(exc).__name__)
            return None
