"""ICO v2 G7a: ServiceDesk Plus read-only client, pagination, cursor, backfill stop / resume, parser, DEMO, source form."""
import datetime as dt
import json

import pytest

from app.database import SessionLocal
from app.itsm import ItsmConfig, ItsmError, collector, demo_generator, sdp
from app.itsm.models import ItsmRequest, ItsmSourceState

CFG = ItsmConfig("sdp-hsc", "SDP HSC", "sdp.corp.local", 8443, api_key="TECH-KEY-0000", templates=["Yêu cầu cấp máy ảo"],
                 backfill_from=dt.date(2025, 10, 1), rate_per_min=600)


def test_client_refuses_every_method_but_get(monkeypatch):
    c = sdp.SdpClient(CFG, sleep=lambda s: None)
    sent = []
    monkeypatch.setattr(c.http.opener, "open", lambda req, timeout=None: sent.append(req) or (_ for _ in ()).throw(AssertionError))
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        with pytest.raises(ItsmError, match="chỉ đọc"):
            c.http.request(method, "/api/v3/requests/1")
    assert sent == []                                                       # nothing left the client
    assert c.http.headers["authtoken"] == "TECH-KEY-0000" and c.http.headers["Accept"] == sdp.ACCEPT


def test_list_params_and_retry_rate(monkeypatch):
    p = json.loads(sdp.list_params(["A", "B"], 1700000000000, 101)["input_data"])["list_info"]
    assert (p["row_count"], p["start_index"], p["sort_field"], p["sort_order"]) == (100, 101, "last_updated_time", "asc")
    assert p["search_criteria"] == [{"field": "template.name", "condition": "is", "values": ["A", "B"]},
                                    {"field": "last_updated_time", "condition": "greater than", "value": "1700000000000",
                                     "logical_operator": "AND"}]
    slept, calls, t = [], [], [0.0]
    c = sdp.SdpClient(ItsmConfig(**{**CFG.__dict__, "rate_per_min": 60}), sleep=lambda s: slept.append(s), clock=lambda: t[0])

    def get(path, params=None):
        calls.append(path)
        if len(calls) == 1:
            raise ItsmError("Hết thời gian chờ / lỗi mạng tới sdp")         # transient: retried
        return {"requests": [{"id": "1"}], "list_info": {"has_more_rows": False}}
    monkeypatch.setattr(c.http, "get", get)
    assert c.list_page(None, 1) == ([{"id": "1"}], False)
    assert len(calls) == 2 and slept[0] == 1.0                               # backoff 1 s, then ≥ 1 s between calls (60 / min)
    monkeypatch.setattr(c.http, "get", lambda path, params=None: (_ for _ in ()).throw(ItsmError("từ chối xác thực (401)")))
    calls.clear()
    with pytest.raises(ItsmError, match="401"):
        c.get("5")                                                           # 401: no retry


def test_parse_request_and_clean_html():
    r = demo_generator.requests()[1]
    p = sdp.parse_request(r)
    assert p["reqId"] == r["id"] and p["template"] in demo_generator.DEMO_TEMPLATES and p["requesterEmail"].endswith("@corp.example")
    assert p["createdAt"] < p["createdAt"] + dt.timedelta(seconds=1) and p["updatedMs"] == int(r["last_updated_time"]["value"])
    assert "<table" in p["description"] and "<script" not in p["description"] and "onclick" not in p["description"]
    assert sdp.kind_of("Yêu cầu tăng tài nguyên máy ảo", None) == "resize" and sdp.kind_of("Khác", "Nhờ tạo VM") == "new_vm"
    assert len(sdp.clean_html("é" * 50000).encode()) <= sdp.MAX_DESCRIPTION


def test_demo_has_60_table_requests_and_5_free_text():
    rs = demo_generator.requests()
    assert len(rs) == 65 and sum("<table" in r["description"] for r in rs) == 60
    assert any('rowspan="' in r["description"] for r in rs) and any("Bộ nhớ" in r["description"] for r in rs)
    assert min(int(r["created_time"]["value"]) for r in rs) >= sdp.dt_to_ms(dt.date(2025, 10, 1))


@pytest.fixture()
def db():
    from app.database import ensure_schema
    ensure_schema()
    s = SessionLocal()
    s.query(ItsmRequest).delete()
    s.query(ItsmSourceState).delete()
    s.commit()
    yield s
    s.query(ItsmRequest).delete()
    s.query(ItsmSourceState).delete()
    s.commit()
    s.close()


class FakeSdp:
    """SDP over a fixed set of requests, with list pages of `page` rows; counts the calls."""

    def __init__(self, reqs, page=3, fail_on_get=None):
        self.reqs, self.page, self.fail_on_get, self.calls = sorted(reqs, key=lambda r: r["upd"]), page, fail_on_get, []

    def list_page(self, after_ms, start_index):
        self.calls.append(("list", after_ms, start_index))
        rows = [r for r in self.reqs if after_ms is None or r["upd"] > after_ms]
        page = rows[start_index - 1:start_index - 1 + self.page]
        return [{"id": r["id"]} for r in page], start_index - 1 + self.page < len(rows)

    def get(self, req_id):
        if req_id == self.fail_on_get:
            raise ItsmError("Lỗi API 500 tại /api/v3/requests/x.")
        r = next(x for x in self.reqs if x["id"] == req_id)
        return {"id": r["id"], "subject": f"Cấp VM {r['id']}", "template": {"name": "Yêu cầu cấp máy ảo"},
                "created_time": {"value": str(r["upd"])}, "last_updated_time": {"value": str(r["upd"])}, "description": "<p>x</p>"}


BASE = sdp.dt_to_ms(dt.date(2025, 10, 1))
REQS = [{"id": str(100 + i), "upd": BASE + i * 3_600_000} for i in range(8)]


def test_backfill_pages_stops_and_resumes(db, monkeypatch):
    fake = FakeSdp(REQS, page=3)
    monkeypatch.setattr(collector, "make_client", lambda cfg: fake)
    now = dt.datetime(2026, 11, 21, 3, 0)
    r1 = collector.sync_itsm(db, CFG, max_requests=5, now=now)               # backfill stops after 5 (small batches)
    st = db.query(ItsmSourceState).one()
    assert (r1["read"], r1["backfillDone"], st.cursor_ms) == (5, False, REQS[4]["upd"])
    assert [c[2] for c in fake.calls if c[0] == "list"] == [1, 4]            # pages 1 and 2 (start_index 1, 4)
    r2 = collector.sync_itsm(db, CFG, max_requests=5, now=now + dt.timedelta(minutes=5))      # not due yet? backfill: always due
    assert (r2["read"], r2["backfillDone"], db.query(ItsmRequest).count()) == (3, True, 8)
    assert fake.calls[2] == ("list", REQS[4]["upd"], 1)                       # resumed from the cursor
    # after the backfill: incremental, every interval_minutes only
    assert collector.sync_itsm(db, CFG, now=now + dt.timedelta(minutes=10))["idle"] is True
    REQS2 = REQS + [{"id": "100", "upd": REQS[-1]["upd"] + 60_000}]          # request 100 edited later: read again
    monkeypatch.setattr(collector, "make_client", lambda cfg: FakeSdp(REQS2, page=3))
    r3 = collector.sync_itsm(db, CFG, now=now + dt.timedelta(minutes=40))
    assert r3["read"] == 1 and db.query(ItsmRequest).count() == 8


def test_error_half_way_keeps_the_pages_already_read(db, monkeypatch):
    monkeypatch.setattr(collector, "make_client", lambda cfg: FakeSdp(REQS, page=3, fail_on_get="104"))
    with pytest.raises(ItsmError):
        collector.sync_itsm(db, CFG, now=dt.datetime(2026, 11, 21))
    st = db.query(ItsmSourceState).one()
    assert db.query(ItsmRequest).count() == 3 and st.cursor_ms == REQS[2]["upd"] and "500" in st.last_error


def test_demo_source_backfills_everything(db):
    cfg = ItsmConfig("sdp-demo", "SDP demo", "sdp.demo.local", demo=True, templates=[], rate_per_min=600)
    for _ in range(3):
        collector.sync_itsm(db, cfg, force=True, max_requests=30)
    st = db.query(ItsmSourceState).filter_by(source_key="sdp-demo").one()
    assert st.backfill_done and db.query(ItsmRequest).filter_by(source_key="sdp-demo").count() == 65
    assert db.query(ItsmRequest).filter_by(source_key="sdp-demo", kind="resize").count() > 0


def test_source_form_validates_templates_dates_and_key():
    from app.models import DataSource
    from app.sources import SourceError, apply_input
    row = DataSource()
    base = {"kind": "sdp", "key": "sdp-hsc", "name": "SDP", "host": "sdp.corp.local", "port": 8443}
    with pytest.raises(SourceError, match="template"):
        apply_input(row, {**base, "apiKey": "k"}, True)
    with pytest.raises(SourceError, match="technician key"):
        apply_input(DataSource(), {**base, "templates": "Yêu cầu cấp máy ảo"}, True)
    with pytest.raises(SourceError, match="Ngày nạp lùi"):
        apply_input(DataSource(), {**base, "templates": "A", "apiKey": "k", "backfillFrom": "01/10/2025"}, True)
    row = DataSource()
    apply_input(row, {**base, "templates": "Yêu cầu cấp máy ảo, Yêu cầu tăng tài nguyên", "apiKey": "TECH-KEY-SECRET-42",
                      "backfillFrom": "2025-10-01"}, True)
    o = json.loads(row.options)
    assert o["templates"] == ["Yêu cầu cấp máy ảo", "Yêu cầu tăng tài nguyên"] and o["backfillFrom"] == "2025-10-01"
    assert row.api_key_enc and "TECH-KEY-SECRET-42" not in row.api_key_enc
