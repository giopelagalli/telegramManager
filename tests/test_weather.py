import httpx
from bot.weather import MorningWeather

def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))

async def test_weather_and_pollen_line():
    async def h(req):
        if "open-meteo" in str(req.url):
            return httpx.Response(200, json={"current": {"temperature_2m": 71.6, "weather_code": 2},
                "daily": {"temperature_2m_max": [84.2], "precipitation_probability_max": [40], "uv_index_max": [7.1]}})
        return httpx.Response(200, json={"dailyInfo": [{
            "pollenTypeInfo": [{"displayName": "Tree", "indexInfo": {"category": "High"}},
                               {"displayName": "Grass", "indexInfo": {"category": "Low"}}],
            "plantInfo": [{"displayName": "Oak", "indexInfo": {"value": 4}}, {"displayName": "Pine", "indexInfo": {"value": 1}}]}]})
    w = MorningWeather("k", http=_client(h))
    assert await w.line((33.7, -84.4)) == "72° and partly cloudy, high 84, 40% rain, UV high. Pollen: tree HIGH (oak)."

async def test_weather_only_without_key_and_survives_failure():
    async def h(req):
        return httpx.Response(200, json={"current": {"temperature_2m": 50, "weather_code": 0},
            "daily": {"temperature_2m_max": [60], "precipitation_probability_max": [0], "uv_index_max": [2]}})
    assert await MorningWeather(None, http=_client(h)).line((0, 0)) == "50° and clear, high 60, UV low."
    async def boom(req): raise httpx.ConnectError("x")
    assert await MorningWeather("k", http=_client(boom)).line((0, 0)) is None
