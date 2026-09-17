"""Morning weather, UV and pollen for the briefing. Open-Meteo needs no key; pollen uses Google."""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
GOOGLE_POLLEN = "https://pollen.googleapis.com/v1/forecast:lookup"

_CODES = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "foggy", 48: "foggy",
    51: "drizzle", 53: "drizzle", 55: "drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
    71: "snow", 73: "snow", 75: "heavy snow", 80: "showers", 81: "showers", 82: "heavy showers",
    95: "thunderstorms", 96: "thunderstorms", 99: "thunderstorms",
}


def _uv_word(uv: float) -> str:
    return "low" if uv < 3 else "moderate" if uv < 6 else "high" if uv < 8 else "very high"


class MorningWeather:
    def __init__(self, google_key: str | None = None, http: httpx.AsyncClient | None = None):
        self._key = google_key
        self._http = http or httpx.AsyncClient(timeout=8.0)

    async def line(self, latlng: tuple[float, float]) -> str | None:
        """One line like '72° and partly cloudy, high 84, UV high. Pollen: tree HIGH (oak).'"""
        parts = []
        weather = await self._weather(latlng)
        if weather:
            parts.append(weather)
        if self._key:
            pollen = await self._pollen(latlng)
            if pollen:
                parts.append(pollen)
        return " ".join(parts) or None

    async def _weather(self, latlng) -> str | None:
        try:
            resp = await self._http.get(OPEN_METEO, params={
                "latitude": latlng[0], "longitude": latlng[1],
                "current": "temperature_2m,weather_code",
                "daily": "temperature_2m_max,precipitation_probability_max,uv_index_max",
                "temperature_unit": "fahrenheit", "forecast_days": 1, "timezone": "auto",
            })
            resp.raise_for_status()
            data = resp.json()
            cur, daily = data["current"], data["daily"]
            temp = round(cur["temperature_2m"])
            desc = _CODES.get(cur.get("weather_code", -1), "")
            high = round(daily["temperature_2m_max"][0])
            rain = daily.get("precipitation_probability_max", [None])[0]
            uv = daily.get("uv_index_max", [None])[0]
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            logger.warning("weather failed: %s", type(exc).__name__)
            return None
        bits = [f"{temp}°" + (f" and {desc}" if desc else ""), f"high {high}"]
        if rain is not None and rain >= 30:
            bits.append(f"{rain}% rain")
        if uv is not None:
            bits.append(f"UV {_uv_word(uv)}")
        return ", ".join(bits) + "."

    async def _pollen(self, latlng) -> str | None:
        try:
            resp = await self._http.get(GOOGLE_POLLEN, params={
                "key": self._key, "location.latitude": latlng[0], "location.longitude": latlng[1], "days": 1,
            })
            resp.raise_for_status()
            day = resp.json()["dailyInfo"][0]
            types = [
                (t["displayName"], t["indexInfo"]["category"])
                for t in day.get("pollenTypeInfo", []) if t.get("indexInfo")
            ]
            plants = [
                p["displayName"] for p in day.get("plantInfo", [])
                if p.get("indexInfo", {}).get("value", 0) >= 3
            ]
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            logger.warning("pollen failed: %s", type(exc).__name__)
            return None
        if not types:
            return None
        worst = max(types, key=lambda t: _severity(t[1]))
        text = f"Pollen: {worst[0].lower()} {worst[1].upper()}"
        if plants:
            text += f" ({', '.join(plants[:2]).lower()})"
        return text + "."


def _severity(category: str) -> int:
    order = ["none", "very low", "low", "moderate", "high", "very high"]
    return order.index(category.lower()) if category.lower() in order else 0
