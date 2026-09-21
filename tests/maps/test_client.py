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

async def test_travel_minutes_uses_routes_api_with_traffic():
    seen = {}
    async def h(req):
        seen["url"] = str(req.url); seen["body"] = __import__("json").loads(req.content); seen["mask"] = req.headers.get("x-goog-fieldmask")
        assert req.headers.get("x-goog-api-key") == "k"
        return httpx.Response(200, json={"routes": [{"duration": "1501s", "distanceMeters": 4200}]})
    assert await client(h).travel_minutes((0, 0), (1, 1), datetime(2099, 9, 4, 17, 0, tzinfo=timezone.utc)) == 26
    assert seen["url"].endswith("directions/v2:computeRoutes") and seen["mask"] == "routes.duration,routes.distanceMeters"
    assert seen["body"]["travelMode"] == "DRIVE" and seen["body"]["routingPreference"] == "TRAFFIC_AWARE"
    assert seen["body"]["departureTime"] == "2099-09-04T17:00:00Z"
    async def boom(req): raise httpx.ConnectError("x")
    assert await client(boom).travel_minutes((0, 0), (1, 1), datetime.now(timezone.utc)) is None


async def test_walking_route_has_no_traffic_preference():
    bodies = []
    async def h(req):
        bodies.append(__import__("json").loads(req.content))
        return httpx.Response(200, json={"routes": [{"duration": "720s", "distanceMeters": 900}]})
    assert await client(h).route((0, 0), (1, 1), "walk") == (12, 900)
    assert bodies[0]["travelMode"] == "WALK" and "routingPreference" not in bodies[0] and "departureTime" not in bodies[0]


async def test_find_place_uses_places_new_text_search():
    seen = {}
    async def h(req):
        seen["url"] = str(req.url); seen["body"] = __import__("json").loads(req.content); seen["mask"] = req.headers.get("x-goog-fieldmask")
        return httpx.Response(200, json={"places": [{"displayName": {"text": "Magnolias"}, "formattedAddress": "312 E Broad St, Athens, GA 30601", "location": {"latitude": 33.958, "longitude": -83.376}}]})
    place = await client(h).find_place("mags", near=(33.95, -83.38))
    assert place == {"name": "Magnolias", "address": "312 E Broad St, Athens, GA 30601", "latlng": (33.958, -83.376)}
    assert seen["url"].endswith("v1/places:searchText") and "places.displayName" in seen["mask"]
    assert seen["body"]["textQuery"] == "mags" and seen["body"]["locationBias"]["circle"]["center"]["latitude"] == 33.95


async def test_non_json_body_returns_none():
    async def h(req): return httpx.Response(200, text="<html>oops</html>")
    assert await client(h).geocode("x") is None
    assert await client(h).travel_minutes((0, 0), (1, 1), datetime.now(timezone.utc)) is None

async def test_aclose_closes_owned_client_only():
    owned = MapsClient("k")
    await owned.aclose()
    assert owned._http.is_closed

    injected_http = httpx.AsyncClient()
    injected = MapsClient("k", http=injected_http)
    await injected.aclose()
    assert not injected_http.is_closed
    await injected_http.aclose()


async def test_find_place_logs_googles_error_message():
    import logging
    seen = []
    def handler(req):
        return httpx.Response(403, json={"error": {"message": "Places API (New) has not been used in project", "status": "PERMISSION_DENIED"}})
    c = MapsClient("k", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    class Grab(logging.Handler):
        def emit(self, record): seen.append(record.getMessage())
    logging.getLogger("bot.maps.client").addHandler(Grab())
    assert await c.find_place("mags", near=(33.9, -83.3)) is None
    assert any("HTTP 403" in m and "has not been used" in m for m in seen)
    empty = MapsClient("k", http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))))
    assert await empty.find_place("nowhere") is None
