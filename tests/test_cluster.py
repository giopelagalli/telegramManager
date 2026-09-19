from datetime import datetime, timezone

import httpx

from bot.cluster import AgentHubStatus, ClusterStatus, SparkStatus

METRICS = """# HELP vllm:num_requests_running Number of requests currently running on GPU.
vllm:num_requests_running{model_name="qwen3.8-flash-next"} 2.0
vllm:num_requests_waiting{model_name="qwen3.8-flash-next"} 5.0
vllm:kv_cache_usage_perc{model_name="qwen3.8-flash-next"} 0.41
vllm:generation_tokens_total{model_name="qwen3.8-flash-next"} 123456.0
"""


def _http(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_spark_status_reads_vllm_metrics():
    seen = {}
    def handler(req):
        seen["url"] = str(req.url)
        return httpx.Response(200, text=METRICS)
    s = SparkStatus("http://localhost:8888/v1", http=_http(handler))
    assert await s.snapshot() == {"running": 2, "waiting": 5, "kv_pct": 41}
    assert seen["url"] == "http://localhost:8888/metrics"
    down = SparkStatus("http://localhost:8888/v1", http=_http(lambda r: httpx.Response(503)))
    assert await down.snapshot() is None


async def test_agenthub_status_logs_in_once_and_reads_state():
    calls = []
    def handler(req):
        calls.append((req.method, req.url.path))
        if req.url.path == "/api/login":
            return httpx.Response(200, json={"owner": True}, headers={"set-cookie": "hub_session=abc; Path=/"})
        return httpx.Response(200, json={
            "projects": [
                {"slug": "prob", "title": "Probability engine", "status": "active", "priority": 1, "updatedAt": 1789000000000},
                {"slug": "rosen", "title": "Rosenroot", "status": "paused", "priority": 0, "updatedAt": 1789000000000},
            ],
            "jobs": [{"status": "running"}, {"status": "queued"}, {"status": "queued"}],
            "streams": {"orchestrator": 1, "worker": 3},
        })
    h = AgentHubStatus("http://hub.internal:4000", "pw", http=_http(handler))
    snap = await h.snapshot()
    assert snap["jobs"] == {"queued": 2, "running": 1} and snap["streams"]["worker"] == 3
    assert [p["slug"] for p in snap["projects"]] == ["prob", "rosen"]
    await h.snapshot()
    assert calls.count(("POST", "/api/login")) == 1 and calls.count(("GET", "/api/state")) == 2


async def test_report_reads_like_a_status_line():
    spark = SparkStatus("http://s/v1", http=_http(lambda r: httpx.Response(200, text=METRICS)))
    def hub_handler(req):
        return httpx.Response(200, json={
            "projects": [{"slug": "prob", "title": "Probability engine", "status": "active", "priority": 1,
                          "updatedAt": 1789000000000}],
            "jobs": [], "streams": {"worker": 3},
        })
    hub = AgentHubStatus("http://h", http=_http(hub_handler))
    now = datetime.fromtimestamp(1789000000 + 90 * 60, tz=timezone.utc)
    text = await ClusterStatus(spark, hub).report(now)
    assert text.splitlines() == [
        "Spark: 2 running, 5 waiting, KV cache 41%.",
        "AgentHub: 3 model streams open, no jobs.",
        "• Probability engine — active, priority 1, touched 1h ago",
    ]
    idle = SparkStatus("http://s/v1", http=_http(lambda r: httpx.Response(200, text=METRICS.replace(" 2.0", " 0.0").replace(" 5.0", " 0.0"))))
    assert (await ClusterStatus(idle).report(now)).startswith("Spark: idle")
    assert (await ClusterStatus().report(now)).startswith("Nothing to report")
