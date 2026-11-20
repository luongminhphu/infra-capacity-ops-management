"""Review phase A (docs/design/ico-ai-mcp-review.md): quiet library logs, request id + pod on persisted logs,
job_runs registry for every background run, interpolated percentiles, MCP reading them."""
import json
import logging

from app import jobruns, observability as obs
from app.collectors import scheduler
from app.collectors.locks import SourceBusy
from app.database import SessionLocal
from app.models import AppLog, JobRun
from test_mcp import _call, _token, client  # noqa: F401  (fixture)


def _ring_has(text):
    with obs._lock:
        return [e for e in obs._ring if text in e["message"]]


def test_library_chatter_is_quiet_and_harfbuzz_notice_once(client):  # noqa: F811
    logging.getLogger("fontTools.subset").info("glyf pruned zz-quiet-test")
    assert not _ring_has("zz-quiet-test")                                   # INFO of fontTools no longer captured
    wp = logging.getLogger("weasyprint")
    for f in wp.filters:                                                    # fresh process state for the test
        if isinstance(f, obs._OnceFilter):
            f.seen.clear()
    for _ in range(4):
        wp.warning('Using fontTools instead of HarfBuzz-Subset for font "Inter". zz-once')
    wp.warning("Ignored `foo` zz-css-1")
    wp.warning("Ignored `foo` zz-css-2")
    assert len(_ring_has("zz-once")) == 1                                   # once per process, not per font / PDF
    assert len(_ring_has("zz-css")) == 2                                    # other WeasyPrint warnings stay


def test_persisted_logs_carry_request_id_and_pod_and_mcp_filters_by_it(client):  # noqa: F811
    tok = _token(client)["token"]
    with obs.job_context("unit") as job:
        logging.getLogger("vco.zztest").error("round failed zz-rid 100% done")
    logging.getLogger("vco.zztest").error("other zz-rid line 1000 done")
    obs.flush()
    db = SessionLocal()
    row = db.query(AppLog).filter(AppLog.message.like("%round failed zz-rid%")).one()
    assert row.request_id == job.id and row.pod == obs.POD
    db.close()
    found = _call(client, tok, "search_logs", {"level": "ERROR", "request_id": job.id, "since_minutes": 5})
    assert [x["message"] for x in found["logs"]] == ["round failed zz-rid 100% done"]
    assert found["logs"][0]["requestId"] == job.id and found["logs"][0]["pod"] == obs.POD
    # contains is literal: "100%" must not match "1000"
    lit = _call(client, tok, "search_logs", {"level": "ERROR", "contains": "100%", "since_minutes": 5})
    assert [x["message"] for x in lit["logs"]] == ["round failed zz-rid 100% done"]
    groups = _call(client, tok, "error_summary", {"since_minutes": 5})["groups"]
    g = next(x for x in groups if "round failed" in x["sampleMessage"])
    assert g["pods"] == [obs.POD] and g["sampleRequestId"] == job.id
    # INFO from memory can be filtered by request id too
    lg = logging.getLogger("vco.zztest")
    lg.setLevel(logging.INFO)                                               # pytest leaves the root logger at WARNING
    with obs.job_context("unit2") as j2:
        lg.info("info line zz-mem")
    mem = _call(client, tok, "search_logs", {"level": "INFO", "request_id": j2.id, "since_minutes": 5})
    assert [x["message"] for x in mem["logs"]] == ["info line zz-mem"] and mem["pod"] == obs.POD


def test_percentile_is_interpolated_inside_the_bucket():
    hist = [0] * (len(obs.LATENCY_BUCKETS_MS) + 1)
    hist[obs.LATENCY_BUCKETS_MS.index(5000)] = 10                          # 10 requests between 2 500 and 5 000 ms
    assert obs.percentile(hist, 0.5, 6000) == 3750.0                       # was 5 000 (bucket upper bound)
    assert obs.percentile(hist, 0.5, 3000) == 2750.0                       # capped by the slowest request seen
    hist2 = [0] * len(hist)
    hist2[0] = 4                                                            # 0–5 ms
    assert obs.percentile(hist2, 0.5, 4) == 2.0
    assert obs.percentile([0] * len(hist), 0.5, 0) is None


def _wipe_runs():
    db = SessionLocal()
    db.query(JobRun).delete()
    db.commit()
    db.close()


def test_run_syncs_records_every_source_with_status_counters_and_trigger(client):  # noqa: F811
    _wipe_runs()

    def ok():
        return {"vcenterKey": "vc-zz", "ok": True, "runId": 7, "hostCount": 3, "vmCount": 40, "finishedAt": None}

    def boom():
        raise RuntimeError("SELECT secret internals")

    def busy():
        raise SourceBusy("netbackup", "nbu-zz")

    with obs.job_context("poll") as job:
        res = scheduler.run_syncs([ok, boom, busy], [{"vcenterKey": "vc-zz"}, {"key": "pure-zz"}, {"key": "nbu-zz"}], 2, 30)
    assert [r["ok"] for r in res] == [True, False, False]
    db = SessionLocal()
    rows = {r.source_key: r for r in db.query(JobRun).filter(JobRun.job == "sync")}
    db.close()
    a, b, c = rows["vc-zz"], rows["pure-zz"], rows["nbu-zz"]
    assert (a.status, a.family, a.trigger, a.run_id, a.pod) == ("ok", "virtualization", "schedule", job.id, obs.POD)
    assert json.loads(a.counters) == {"hostCount": 3, "vmCount": 40}            # runId / ok left out
    assert b.status == "error" and "SELECT" not in b.error and b.error.startswith("Đồng bộ thất bại")
    assert c.status == "skipped" and a.duration_ms is not None and a.duration_ms >= 0

    # a manual sync (inside an HTTP request) is recorded as such
    tok = obs._request_ctx.set({"db_ms": 0.0, "db_queries": 0, "request_id": "req-zz-12345678"})
    try:
        scheduler.run_syncs([ok], [{"vcenterKey": "vc-zz"}], 1, 30)
    finally:
        obs._request_ctx.reset(tok)
    db = SessionLocal()
    manual = db.query(JobRun).filter(JobRun.run_id == "req-zz-12345678").one()
    db.close()
    assert manual.trigger == "manual"


def test_timed_steps_and_round_and_mcp_sync_history(client):  # noqa: F811
    _wipe_runs()
    with obs.job_context("poll"):
        assert jobruns.timed("cmdb", lambda: {"seconds": 0.5, "cis": 352, "links": 464, "retired": 0}) == {"seconds": 0.5, "cis": 352,
                                                                                                         "links": 464, "retired": 0}
        assert jobruns.timed("owners", lambda: None) is None                # helper logged its own exception
        assert jobruns.timed("retention", lambda: None, none_is="skipped") is None
        jobruns.skipped("reviews", "Bỏ qua: dựng sổ CI lỗi.")
        jobruns.record_round([{"ok": True}, {"ok": False, "error": "x"}, {"ok": False, "skipped": True}], 4.2)
    db = SessionLocal()
    by_job = {r.job: r for r in db.query(JobRun)}
    db.close()
    assert by_job["cmdb"].status == "ok" and json.loads(by_job["cmdb"].counters)["cis"] == 352
    assert by_job["owners"].status == "error" and by_job["retention"].status == "skipped"
    assert by_job["poll"].status == "error" and json.loads(by_job["poll"].counters) == {"sources": 3, "ok": 1, "failed": 1, "skipped": 1}
    assert 4100 <= by_job["poll"].duration_ms <= 4300

    tok = _token(client)["token"]
    runs = _call(client, tok, "sync_history", {"limit": 50})
    assert runs["source"].startswith("job_runs") and {r["job"] for r in runs["runs"]} >= {"cmdb", "owners", "retention", "reviews", "poll"}
    only = _call(client, tok, "sync_history", {"source": "owners"})["runs"]
    assert [r["status"] for r in only] == ["error"] and only[0]["runId"].startswith("job-poll-")
    assert _call(client, tok, "sync_history", {"status": "skipped"})["runs"][0]["job"] in ("retention", "reviews")
    _wipe_runs()
