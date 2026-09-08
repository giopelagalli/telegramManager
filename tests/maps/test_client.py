from datetime import datetime, timezone
import httpx, pytest
from bot.maps.client import MapsClient, distance_m

def test_distance_haversine():
    assert abs(distance_m((40.7000, -74.0000), (40.7000, -74.0000))) < 0.01
    assert 1090 < distance_m((40.7000, -74.0000), (40.7100, -74.0000)) < 1120

def client(handler):
    return MapsClient("k", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

async def test_geocode_ok_and_error():
    async def h(req):
        assert "address=1+Main" in str(req.url) or "address=1%20Main" in str(req.url)
        return httpx.Response(200, json={"status": "OK", "results": [{"geometry": {"location": {"lat": 1.5, "lng": 2.5}}}]})
    assert await client(h).geocode("1 Main") == (1.5, 2.5)
    async def bad(req): return httpx.Response(500)
    assert await client(bad).geocode("x") is None

async def test_travel_minutes_prefers_traffic():
    async def h(req):
        assert "departure_time=" in str(req.url)
        return httpx.Response(200, json={"status": "OK", "routes": [{"legs": [{"duration": {"value": 600}, "duration_in_traffic": {"value": 1501}}]}]})
    assert await client(h).travel_minutes((0, 0), (1, 1), datetime(2026, 9, 4, 17, 0, tzinfo=timezone.utc)) == 26
    async def boom(req): raise httpx.ConnectError("x")
    assert await client(boom).travel_minutes((0, 0), (1, 1), datetime.now(timezone.utc)) is None
