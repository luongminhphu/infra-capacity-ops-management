"""Review phase B: MCP tools follow the mcp-builder checklist (prefix, annotations, outputSchema + structuredContent,
pagination, actionable errors) and the 6 new tools work on the test database."""
import json
import logging

from app import jobruns, observability as obs
from app.database import SessionLocal
from app.models import AiRun, JobRun
from test_mcp import _rpc, _token, client  # noqa: F401  (fixture)

TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict,
         "null": type(None)}


def _conforms(schema, value, path="$"):
    """Small JSON-schema check (type, required, properties one level deep) — enough for the tool output contracts."""
    types = schema.get("type")
    if types:
        types = types if isinstance(types, list) else [types]
        ok = any(isinstance(value, TYPES[t]) and not (t in ("integer", "number") and isinstance(value, bool)) for t in types)
        assert ok, f"{path}: {type(value).__name__} is not {types}"
    if isinstance(value, dict):
        for r in schema.get("required", []):
            assert r in value, f"{path}: missing {r}"
        for k, sub in (schema.get("properties") or {}).items():
            if k in value:
                _conforms(sub, value[k], f"{path}.{k}")


def _result(client, tok, name, args=None):  # noqa: F811
    r = _rpc(client, tok, "tools/call", {"name": name, "arguments": args or {}})
    assert r.status_code == 200, r.text
    return r.json()["result"]


def test_tools_list_follows_the_checklist(client):  # noqa: F811
    tok = _token(client)["token"]
    tools = _rpc(client, tok, "tools/list").json()["result"]["tools"]
    assert len(tools) == 14
    for t in tools:
        assert t["name"].startswith("ico_") and t["title"] and len(t["description"]) > 40
        assert t["annotations"] == {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
                                    "openWorldHint": False, "title": t["title"]}
        assert t["inputSchema"]["type"] == "object" and t["outputSchema"]["type"] == "object"
    assert {t["name"] for t in tools} >= {"ico_source_status", "ico_job_runs", "ico_data_freshness", "ico_ai_usage",
                                          "ico_config_check", "ico_trace_request"}
    prompts = {p["name"] for p in _rpc(client, tok, "prompts/list").json()["result"]["prompts"]}
    assert prompts == {"health_review", "error_triage", "sync_triage", "ai_module_review", "release_regression"}


def test_every_tool_runs_and_its_structured_content_matches_the_output_schema(client):  # noqa: F811
    tok = _token(client)["token"]
    with obs.job_context("poll") as job:
        logging.getLogger("vco.zzb").error("trace me zz-b")
        jobruns.timed("cmdb", lambda: {"cis": 5})
    tools = {t["name"]: t for t in _rpc(client, tok, "tools/list").json()["result"]["tools"]}
    args = {"ico_trace_request": {"request_id": job.id}}
    for name, t in tools.items():
        res = _result(client, tok, name, args.get(name))
        assert res["isError"] is False, (name, res)
        assert res["structuredContent"] == json.loads(res["content"][0]["text"])
        _conforms(t["outputSchema"], res["structuredContent"], name)
    trace = _result(client, tok, "ico_trace_request", {"request_id": job.id})["structuredContent"]
    kinds = [e["kind"] for e in trace["events"]]
    assert "log" in kinds and "run" in kinds
    assert any(e.get("job") == "cmdb" for e in trace["events"])


def test_actionable_errors_and_aliases(client):  # noqa: F811
    tok = _token(client)["token"]
    res = _result(client, tok, "no_such_tool")
    assert res["isError"] and "ico_app_overview" in res["content"][0]["text"]               # result, not a protocol error
    res = _result(client, tok, "ico_search_logs", {"level": "LOUD"})
    assert res["isError"] and "level must be one of DEBUG" in res["content"][0]["text"]
    res = _result(client, tok, "ico_search_logs", {"since_minutes": 999999})
    assert res["isError"] and "from 1 to 43200" in res["content"][0]["text"]
    res = _result(client, tok, "ico_error_summary", {"window": 5})
    assert res["isError"] and "Unknown argument(s) window" in res["content"][0]["text"] and "since_minutes" in res["content"][0]["text"]
    res = _result(client, tok, "ico_trace_request", {})
    assert res["isError"] and "request_id is required" in res["content"][0]["text"]
    assert _result(client, tok, "search_logs", {"level": "ERROR"})["isError"] is False      # old name still works


def test_pagination(client):  # noqa: F811
    tok = _token(client)["token"]
    db = SessionLocal()
    db.query(JobRun).delete()
    db.commit()
    db.close()
    for i in range(5):
        jobruns.skipped("reviews", f"zz page {i}")
    p1 = _result(client, tok, "ico_sync_history", {"source": "reviews", "limit": 2})["structuredContent"]
    assert len(p1["runs"]) == 2 and p1["hasMore"] is True and p1["nextOffset"] == 2
    p3 = _result(client, tok, "ico_sync_history", {"source": "reviews", "limit": 2, "offset": 4})["structuredContent"]
    assert len(p3["runs"]) == 1 and p3["hasMore"] is False and p3["nextOffset"] is None
    seen = {r["error"] for r in p1["runs"]} | {r["error"] for r in p3["runs"]}
    assert len(seen) == 3                                                                    # pages do not overlap
    lg = logging.getLogger("vco.zzpage")
    for i in range(3):
        lg.error("zz-page-log %d", i)
    a = _result(client, tok, "ico_search_logs", {"level": "ERROR", "contains": "zz-page-log", "limit": 2})["structuredContent"]
    b = _result(client, tok, "ico_search_logs", {"level": "ERROR", "contains": "zz-page-log", "limit": 2, "offset": 2})["structuredContent"]
    assert a["hasMore"] and not b["hasMore"] and len(a["logs"]) + len(b["logs"]) == 3


def test_ai_usage_hides_users_and_content_and_config_check_has_no_secrets(client):  # noqa: F811
    tok = _token(client)["token"]
    db = SessionLocal()
    db.add_all([AiRun(task="ask_ico", username="someone@corp.example", model="claude-opus-5-5", status="ok", latency_ms=1200,
                      cost_usd_micros=12000, input_tokens=1000, output_tokens=200),
                AiRun(task="ask_ico", username="someone@corp.example", status="blocked",
                      message="Đã gọi AI 30 lần trong 1 giờ (giới hạn 30) — thử lại sau.")])
    db.commit()
    db.close()
    u = _result(client, tok, "ico_ai_usage", {"since_hours": 1})["structuredContent"]
    ask = next(t for t in u["tasks"] if t["task"] == "ask_ico")
    assert ask["ok"] >= 1 and ask["blocked"] >= 1 and ask["p50Ms"] is not None
    text = json.dumps(u)
    assert "someone@corp.example" not in text and "user:" in text
    cfg = _result(client, tok, "ico_config_check")["structuredContent"]
    assert {c["check"] for c in cfg["checks"]} >= {"public_url", "secure_cookie", "scheduler", "pdf_harfbuzz", "smtp"}
    assert "Admin12345" not in json.dumps(cfg)
