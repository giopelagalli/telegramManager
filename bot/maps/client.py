from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

import httpx

EARTH_RADIUS_M = 6371000

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
TIMEZONE_URL = "https://maps.googleapis.com/maps/api/timezone/json"

logger = logging.getLogger(__name__)


def directions_url(destination: str | None, latlng: tuple[float, float] | None = None, mode: str = "drive") -> str | None:
    """A Google Maps navigation link; opens turn-by-turn on a phone. No API key needed."""
    from urllib.parse import quote
    if latlng is not None:
        target = f"{latlng[0]},{latlng[1]}"
    elif destination:
        target = quote(destination)
    else:
        return None
    travelmode = "walking" if mode == "walk" else "driving"
    return f"https://www.google.com/maps/dir/?api=1&destination={target}&travelmode={travelmode}"


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

    async def _post_json(self, url: str, body: dict, field_mask: str, what: str) -> dict | None:
        """The newer Google APIs (Places New, Routes) are JSON POSTs with the key and a field mask in headers."""
        try:
            resp = await self._http.post(
                url, json=body,
                headers={"X-Goog-Api-Key": self._api_key, "X-Goog-FieldMask": field_mask, "Content-Type": "application/json"},
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            detail = ""
            try:
                detail = exc.response.json().get("error", {}).get("message", "")
            except ValueError:
                pass
            logger.warning("maps.%s failed: HTTP %s %s", what, exc.response.status_code, detail)
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
        """What "mags" means around here: {"name", "address", "latlng"} for the best match near `near`.
        Places API (New), text search."""
        body: dict = {"textQuery": query, "maxResultCount": 1}
        if near is not None:
            body["locationBias"] = {"circle": {"center": {"latitude": near[0], "longitude": near[1]}, "radius": 25000.0}}
        data = await self._post_json(
            PLACES_URL, body, "places.displayName,places.formattedAddress,places.location", "find_place"
        )
        if data is None:
            return None
        places = data.get("places") or []
        if not places:
            logger.warning("maps.find_place: no results for %r", query)
            return None
        try:
            hit = places[0]
            loc = hit["location"]
            name = hit.get("displayName", {}).get("text") or query
            return {"name": name, "address": hit.get("formattedAddress", ""), "latlng": (loc["latitude"], loc["longitude"])}
        except (KeyError, TypeError) as exc:
            logger.warning("maps.find_place failed: %s", type(exc).__name__)
            return None

    async def timezone(self, latlng: tuple[float, float], at: datetime) -> str | None:
        """IANA zone id for a point, e.g. 'America/Los_Angeles'."""
        data = await self._get_json(
            TIMEZONE_URL,
            {"location": f"{latlng[0]},{latlng[1]}", "timestamp": int(at.timestamp()), "key": self._api_key},
            "timezone",
        )
        if not data or data.get("status") != "OK":
            logger.warning("maps.timezone: %s", (data or {}).get("status"))
            return None
        return data.get("timeZoneId") or None

    async def route(
        self, origin: tuple[float, float], dest: tuple[float, float], mode: str = "drive", depart_at: datetime | None = None
    ) -> tuple[int, int] | None:
        """(minutes, meters) from origin to dest, walking or driving with live traffic. Routes API."""
        body: dict = {
            "origin": {"location": {"latLng": {"latitude": origin[0], "longitude": origin[1]}}},
            "destination": {"location": {"latLng": {"latitude": dest[0], "longitude": dest[1]}}},
            "travelMode": "WALK" if mode == "walk" else "DRIVE",
        }
        if mode != "walk":
            body["routingPreference"] = "TRAFFIC_AWARE"
            if depart_at is not None and depart_at > datetime.now(timezone.utc):
                body["departureTime"] = depart_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        data = await self._post_json(ROUTES_URL, body, "routes.duration,routes.distanceMeters", "route")
        if data is None:
            return None
        try:
            r = data["routes"][0]
            seconds = int(str(r["duration"]).rstrip("s"))
            return math.ceil(seconds / 60), int(r.get("distanceMeters", 0))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            logger.warning("maps.route failed: %s", type(exc).__name__)
            return None

    async def travel_minutes(
        self,
        origin: tuple[float, float],
        dest: tuple[float, float],
        depart_at: datetime,
        mode: str = "drive",
    ) -> int | None:
        """Minutes to get there at `depart_at` (driving with traffic, or walking); the leave-by refresh."""
        r = await self.route(origin, dest, mode, depart_at=depart_at)
        return None if r is None else r[0]
