"""Round-2 performance / observability (audit API-01, OBS-02, BEQ-11, REL-06)."""
import logging
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import observability
from app.collectors import scheduler
from app.shared import ai_transport as transport


@pytest.fixture()
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_large_responses_are_gzipped_small_ones_are_not(client):
    r = client.get("/js/core.js", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers.get("content-encoding") == "gzip"
    assert int(r.headers.get("content-length", "0")) < len(r.content)        # decoded body is larger
    small = client.get("/api/health", headers={"Accept-Encoding": "gzip"})
    assert small.headers.get("content-encoding") is None                     # under 1 KB: sent as is
    assert small.headers.get("content-security-policy")                     # security headers still applied


def test_request_id_is_generated_echoed_or_replaced(client):
    a = client.get("/api/health").headers["x-request-id"]
    b = client.get("/api/health").headers["x-request-id"]
    assert len(a) == 16 and a != b
    assert client.get("/api/health", headers={"X-Request-ID": "proxy-abc12345"}).headers["x-request-id"] == "proxy-abc12345"
    bad = client.get("/api/health", headers={"X-Request-ID": "x\" injected=1 " + "a" * 10}).headers["x-request-id"]
    assert bad != "x\" injected=1 " + "a" * 10 and len(bad) == 16                # never echoes unsafe text


def test_log_filter_tags_records_with_the_job_id():
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, "m", None, None)
    f = observability.RequestIdLogFilter()
    f.filter(rec)
    assert rec.request_id == "-"
    with observability.job_context("poll") as job:
        rec2 = logging.LogRecord("t", logging.INFO, __file__, 1, "m", None, None)
        f.filter(rec2)
    assert rec2.request_id == job.id and job.id.startswith("job-poll-")


def test_round_summary_is_one_key_value_line():
    results = [{"ok": True, "vcenterKey": "vc1"}, {"ok": False, "vcenterKey": "vc2"}, {"ok": False, "skipped": True, "key": "nbu1"}]
    line = scheduler.round_summary(results, 4.21, {"new": 1, "resolved": 0, "sent": 1, "failed": 0}, {"runs": 3})
    assert line == ("job=poll sources=3 ok=1 failed=1 skipped=1 duration_ms=4210 failed_keys=vc2 skipped_keys=nbu1 "
                    "alerts_new=1 alerts_resolved=0 alerts_sent=1 alerts_failed=0 purged_runs=3")
    assert "alerts=error" in scheduler.round_summary([], 0.0, None, None)


def test_tool_loop_stops_at_the_total_deadline(monkeypatch):
    monkeypatch.setattr(transport, "_api_key", lambda cfg: "k")
    monkeypatch.setattr(transport, "TOOLS_DEADLINE_S", 1.0)
    calls = []

    def slow(cfg, key, system, messages, tools, max_tokens):
        calls.append(transport._request_timeout())      # each request gets only the time left
        time.sleep(0.3)
        return {"stop": "tool_use", "tokens": 10, "content": [{"type": "tool_use", "id": f"t{len(calls)}", "name": "x", "input": {}}]}
    monkeypatch.setattr(transport, "TOOLS_CALL", slow)
    with pytest.raises(transport.AiError, match="thời gian"):
        transport.run_tools(object(), "s", "q", [], lambda n, i: "ok", max_turns=10)
    assert len(calls) == 2 and calls[0] <= 1.0 and calls[1] < calls[0]
    assert transport._request_timeout() == transport.TIMEOUT_S             # deadline cleared after the loop


def test_concurrent_ai_requests_are_capped(monkeypatch):
    monkeypatch.setattr(transport, "SLOT_WAIT_S", 0.05)
    held = [transport._slots.acquire(timeout=0) for _ in range(transport.MAX_CONCURRENT)]
    try:
        assert all(held)
        import types
        fake = types.SimpleNamespace(Anthropic=lambda **kw: None)
        monkeypatch.setitem(__import__("sys").modules, "anthropic", fake)
        with pytest.raises(transport.AiError, match="bận"):
            transport._create(types.SimpleNamespace(model=None), "k", max_tokens=1, messages=[])
    finally:
        for _ in held:
            transport._slots.release()
