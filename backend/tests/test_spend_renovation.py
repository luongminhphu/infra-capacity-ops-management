"""Office renovation (V2): KPIs (ROUNDUP like the company workbook), history comparison, import of the
"Summarize" layout, link with quotations, close, exports. The workbook is rebuilt synthetically with the
same structure and the figures of the design document (docs/design/spend-vendor-quotes.md §0)."""
import io
import json

import pytest
from openpyxl import Workbook, load_workbook

from app.spend import renovation as rv

from test_spend_opex import _login, admin, app_, editor  # noqa: F401  (fixtures)
from test_spend_quotes import _new_request, _upload, _xlsx


# ---------------------------------------------------------------- pure logic
def test_roundup_kpis_match_the_workbook():
    assert rv.roundup(2_832_016_800, 186) == 15_225_897
    assert rv.roundup(731_710_800, 62) == 11_801_788          # 11.801.787,1 → ROUNDUP
    assert rv.roundup(2_230_360_000, 220) == 10_138_000
    assert rv.roundup(913_109_394, 186) == 4_909_191
    assert rv.roundup(1_600_711_200, 2) == 800_355_600
    assert rv.roundup(None, 10) is None and rv.roundup(5, 0) is None and rv.roundup(5, None) is None


def _pv(pid, label, nodes, cfg, net=None, cab=None, vc=None, vc_rooms=None, kind="actual"):
    amounts = {}
    if net is not None:
        amounts["network"] = {10: {kind: net}}
    if cab is not None:
        amounts["cabling"] = {1: {kind: cab}}
    if vc is not None:
        amounts["vc"] = {20: {kind: vc}}
    return rv.ProjectView(id=pid, code=f"CTVP-{pid}", label=label, year=2020 + pid, nodes=nodes, headcount=None,
                          vc_rooms=vc_rooms, access_points=None, config=cfg, amounts=amounts)


GROUPS = {"cabling": [(1, "CABLING MATERIALS")], "network": [(10, "Switch")], "vc": [(20, "Phòng họp có VC")]}


def test_compare_marks_different_configuration_and_missing_data():
    cur = _pv(9, "FL6 (2026)", 186, "10G, Wifi7", net=2_832_016_800, vc=1_600_711_200, vc_rooms=2, kind="awarded")
    hist = [_pv(1, "FL12 (2024)", 62, "1G, Wifi6", net=731_710_800), _pv(2, "FL3 (2022)", 220, "1G & Wifi6", net=2_230_360_000)]
    c = rv.compare(cur, hist, GROUPS)
    net = c["network"]
    assert net["kpi"]["perNode"] == 15_225_897 and net["median"] == 10_969_894 and net["deviationPct"] == 38.8
    assert net["sameConfigCount"] == 0 and [h["kpi"]["perNode"] for h in net["history"]] == [11_801_788, 10_138_000]
    assert len(net["alerts"]) == 1 and "Khác cấu hình" in net["alerts"][0]["message"] and net["alerts"][0]["level"] == "info"
    # VC: no history → "chưa có số liệu", never 0 per room.
    assert c["vc"]["kpi"]["perRoom"] == 800_355_600 and c["vc"]["median"] is None
    assert [h["kpi"]["perRoom"] for h in c["vc"]["history"]] == [None, None]
    assert "chưa có số liệu lịch sử" in c["vc"]["alerts"][0]["message"]
    # Same configuration → a real warning; history prefers thực chi over báo giá trúng, never dự toán.
    h3 = _pv(3, "FL7 (2025)", 100, "10G & Wifi7", net=1_000_000_000)
    h3.amounts["network"][10]["awarded"] = 999
    h4 = _pv(4, "FL11 (2025)", 100, "10G/Wifi7", net=None)
    h4.amounts["network"] = {10: {"estimate": 1_500_000_000}}
    c2 = rv.compare(cur, [h3, h4], GROUPS)["network"]
    assert c2["sameConfigCount"] == 1 and c2["median"] == 10_000_000 and c2["deviationPct"] == 52.3
    assert c2["alerts"][0]["level"] == "crit" and "Khác cấu hình" not in c2["alerts"][0]["message"]
    assert c2["history"][1]["total"] is None                      # dự toán is not history
    row = c2["rows"][0]
    assert row["current"] == 2_832_016_800 and row["history"] == [1_000_000_000, None] and row["currentKind"] == "awarded"


def test_match_group_prefers_earliest_alias():
    vc = [(i, n, "\n".join(a)) for i, (n, a) in enumerate(rv.DEFAULT_GROUPS["vc"])]
    assert rv.match_group("Phòng họp nhỏ - phòng IS (VC) (nếu chỉ trình chiếu không dây thì như Research)", vc) == 0
    assert rv.match_group("Phòng họp nhỏ - phòng Research (trình chiếu dây - không dây)", vc) == 1
    net = [(i, n, "\n".join(a)) for i, (n, a) in enumerate(rv.DEFAULT_GROUPS["network"])]
    assert [rv.match_group(t, net) for t in ("Switch Layer 2 C9350-48HX", "Bản quyền Essential Access Switch", "SFP-10G-LR-S",
                                             "Cisco Meraki 9176I", "Quạt trần")] == [1, 1, 2, 0, None]
    cab = [(i, n, "\n".join(a)) for i, (n, a) in enumerate(rv.DEFAULT_GROUPS["cabling"])]
    assert rv.match_group("PHÁT SINH (CHI PHÍ THI CÔNG TẠM FL7)", cab) == 13


def test_parse_header():
    assert rv.parse_header("FL12 \n(62 nodes) - 24 NS") == {"floor": "FL12", "nodes": 62, "headcount": 24, "config": None}
    assert rv.parse_header("FL6 \n(10G & Wifi7)") == {"floor": "FL6", "nodes": None, "headcount": None, "config": "10G & Wifi7"}
    assert rv.parse_header("Components")["floor"] is None


# ---------------------------------------------------------------- synthetic Summarize workbook
CAB = [("CABLING MATERIALS", 257_597_280, 257_597_280, 112_334_530, 362_798_590),
       ("CÁP QUANG", 0, 0, 0, 32_727_740),
       ("PVC CONDUIT AND ACCESSORIES", 43_562_500, 43_562_500, 31_244_000, 96_980_870),
       ("RACK CABINET", None, 39_760_848, 0, 39_856_960),
       ("ACCESS CONTROL", 42_018_156, 42_018_156, 41_763_570, 130_014_584),
       ("CAMERA", 11_277_036, 11_277_036, 0, 22_440_600),
       ("RAISE FLOOR FOR HUBROOM", 25_372_764, 25_372_764, 29_458_630, 27_276_630),
       ("UPS", 77_623_920, 77_623_920, 77_623_920, 77_623_920),
       ("AIR CONDITION FOR HUB ROOM", 66_061_948, 66_061_948, 30_450_600, 88_415_840),
       ("INSTALL FEE", 161_794_800, 161_794_800, 128_514_600, 237_271_680),
       ("ĐIỆN NGUỒN UPS CHO VC VÀ TIVI PHÒNG HỌP", 83_975_724, 83_975_724, 0, 0),
       ("DỊCH VỤ DI DỜI MÁY TÍNH", 24_948_000, 24_948_000, 11_880_000, 36_720_000),
       ("PROJECT MANAGEMENT", 20_520_000, 20_520_000, 23_760_000, 40_500_000),
       ("PHÁT SINH (CHI PHÍ THI CÔNG TẠM FL7)", None, 58_596_418, 75_844_286, 41_741_944)]
NET = [("Access Point", 337_608_000, 68_247_360, 163_773_500), ("Switch", 2_221_600_800, 500_932_080, 1_791_146_500),
       ("SFP 10G", 256_608_000, 122_960_160, 164_128_800),
       ("Chi phí triển khai thiết bị IT & vật tư dây nhảy", 16_200_000, 39_571_200, 111_311_200)]


def summarize_file():
    wb = Workbook()
    ws = wb.active
    ws.title = "Summarize"
    put = lambda r, c, v: ws.cell(row=r, column=c, value=v)  # noqa: E731
    for c, v in zip(range(1, 5), ["#", "Components", "Estimate", "Vendor"]):
        put(2, c, v)
    # I — Facility
    put(8, 1, "I"); put(8, 2, "FL6 - IT FACILITY"); put(8, 11, "SO SÁNH CHI PHÍ THI CÔNG FL6 - FL12 - FL3")
    for c, v in zip(range(1, 10), ["#", "Components", "Model", "Qty", "Unit", "Price", "VNT", "TTC", "THRAKONE"]):
        put(9, c, v)
    for c, v in zip(range(11, 16), ["I", "Components", "FL6 \n(186 nodes) - 75 NS", "FL12 \n(62 nodes) - 24 NS", "FL3 \n(220 nodes)"]):
        put(9, c, v)
    put(10, 6, sum(x[1] or 0 for x in CAB)); put(10, 7, 913_109_394)
    for j, col in enumerate((13, 14, 15)):
        put(10, col, sum(x[2 + j] for x in CAB))
    for i, (name, price, fl6, fl12, fl3) in enumerate(CAB, 1):
        r = 10 + i
        put(r, 1, i); put(r, 2, name); put(r, 6, price); put(r, 11, i); put(r, 12, name)
        put(r, 13, fl6); put(r, 14, fl12); put(r, 15, fl3)
    put(25, 1, "Chi phí trung bình của 01 node mạng"); put(25, 11, "Chi phí trung bình của 01 node mạng"); put(25, 13, 4_909_191)
    # II — Network
    put(27, 1, "II"); put(27, 2, "FL6 - IT NETWORK"); put(27, 11, "SO SÁNH CHI PHÍ NETWORK FL6 - FL12 - FL3")
    for c, v in zip(range(1, 10), ["#", "Components", "Model", "Qty", "Unit", "Price", "CMC", "HPT", "SVT"]):
        put(28, c, v)
    for c, v in zip(range(11, 16), ["#", "Components", "FL6 \n(10G & Wifi7)", "FL12 \n(1G & Wifi6)", "FL3 \n(1G & Wifi6)"]):
        put(28, c, v)
    for j, col in enumerate((13, 14, 15)):
        put(29, col, sum(x[1 + j] for x in NET))
    for i, (name, a, b, c) in enumerate(NET, 1):
        r = 29 + i
        put(r, 1, i); put(r, 2, name); put(r, 6, a); put(r, 11, i); put(r, 12, name); put(r, 13, a); put(r, 14, b); put(r, 15, c)
    put(34, 11, "Chi phí trung bình của  01 node mạng"); put(34, 13, 15_225_897)
    # III — VC, with the copied "NETWORK" title of the original file
    put(39, 1, "III"); put(39, 2, "FL6 - VIDEO CONFERENCE SYSTEM"); put(39, 11, "SO SÁNH CHI PHÍ NETWORK FL6 - FL12 - FL3")
    for c, v in zip(range(1, 10), ["#", "Components", "Model", "Qty", "Unit", "Price", "CMC", "HPT", "SVT"]):
        put(40, c, v)
    for c, v in zip(range(11, 16), ["#", "Components", "FL6 ", "FL12 ", "FL3 "]):
        put(40, c, v)
    put(41, 13, 1_600_711_200); put(41, 14, 0); put(41, 15, 0)
    rooms = [("Phòng họp nhỏ - phòng A (VC)", 778_755_600), ("Phòng họp nhỏ - phòng B (VC)", 778_755_600),
             ("Phòng họp nhỏ - phòng C (trình chiếu dây - không dây)", 43_200_000)]
    for i, (name, v) in enumerate(rooms, 1):
        put(41 + i, 1, i); put(41 + i, 2, name); put(41 + i, 6, v); put(41 + i, 12, name); put(41 + i, 13, v)
    put(45, 11, "Chi phí trung bình của 01 phòng họp"); put(45, 13, 800_355_600); put(45, 14, 0)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_read_summarize_finds_projects_scale_and_discrepancies():
    r = rv.read_summarize(summarize_file())
    by = {p.floor: p for p in r.projects}
    assert set(by) == {"FL6", "FL12", "FL3"}
    fl6, fl12, fl3 = by["FL6"], by["FL12"], by["FL3"]
    assert (fl6.nodes, fl6.headcount, fl6.config, fl6.vc_rooms, fl6.meeting_rooms) == (186, 75, "10G & Wifi7", 2, 3)
    assert (fl12.nodes, fl12.headcount, fl12.config, fl12.vc_rooms) == (62, 24, "1G & Wifi6", None)
    assert sum(fl6.amounts["cabling"].values()) == 913_109_394 and sum(fl12.amounts["network"].values()) == 731_710_800
    assert fl3.stated["cabling"] == 1_234_369_358 and "vc" not in fl3.amounts
    codes = {(f["code"], f.get("floor"), f.get("workstream")) for f in r.findings}
    assert ("NODATA", "FL12", "vc") in codes and ("NODATA", "FL3", "vc") in codes and ("LABEL", None, None) in codes
    est = next(f for f in r.findings if f["code"] == "ESTIMATE")
    assert est["amount"] == 98_357_266 == 39_760_848 + 58_596_418
    assert "RACK CABINET 39.760.848" in est["message"] and "814.752.128" in est["message"]


# ---------------------------------------------------------------- API
def _import(admin, years):
    apply = json.dumps({"projects": [{"floor": f, "year": y, "kind": "actual"} for f, y in years.items()]})
    return admin.post("/api/spend/renovations/import", files={"file": ("summarize.xlsx", summarize_file())}, data={"apply": apply})


def test_import_preview_then_apply_creates_history(admin, editor):
    assert editor.post("/api/spend/renovations/import", files={"file": ("s.xlsx", summarize_file())}).status_code == 403
    prev = admin.post("/api/spend/renovations/import", files={"file": ("s.xlsx", summarize_file())}).json()
    fl6 = next(p for p in prev["projects"] if p["floor"] == "FL6")
    assert fl6["blocks"]["network"]["total"] == 2_832_016_800
    assert all(l["group"] for b in fl6["blocks"].values() for l in b["lines"])               # every label mapped to a group
    assert editor.get("/api/spend/renovations").json() == []                                 # preview writes nothing
    r = _import(admin, {"FL12": 2024, "FL3": 2022})
    assert r.status_code == 200, r.text
    assert sorted(r.json()["created"]) == ["FL12", "FL3"]
    lst = {p["floor"]: p for p in editor.get("/api/spend/renovations").json()}
    assert lst["FL12"]["isHistorical"] and lst["FL12"]["status"] == "closed" and lst["FL12"]["code"].startswith("CTVP-2024-")
    assert lst["FL12"]["workstreams"]["network"] == {"total": 731_710_800, "kpi": 11_801_788}
    assert lst["FL3"]["workstreams"]["cabling"]["kpi"] == 5_610_770 and lst["FL3"]["workstreams"]["vc"]["total"] is None
    # Re-import updates instead of duplicating.
    assert _import(admin, {"FL12": 2024}).json()["updated"] == ["FL12"]
    assert len(editor.get("/api/spend/renovations").json()) == 2


def test_project_quotes_comparison_close_and_exports(app_, admin, editor):
    approver = _login(app_, "approver1")
    _import(admin, {"FL12": 2024, "FL3": 2022})
    r = editor.post("/api/spend/renovations", json={"name": "Cải tạo FL9", "floor": "fl 9", "year": 2026, "nodes": 186, "headcount": 75,
                                                    "vcRooms": 2, "configTags": "10G, Wifi7"})
    assert r.status_code == 200, r.text
    p = r.json()
    code = p["code"]
    assert p["floor"] == "FL9" and code.startswith("CTVP-2026-") and [h["label"] for h in p["historyAvailable"]] == ["FL12 (2024)", "FL3 (2022)"]
    assert editor.post("/api/spend/renovations", json={"name": "x", "floor": "FL9", "year": 2026}).status_code == 409
    # Network quotation linked to the project; one line has no group yet.
    rq = _new_request(editor, title="Network FL9", workstream="network", renovationCode=code)
    assert rq["renovation"]["code"] == code
    content = _xlsx([["STT", "Hạng mục", "Model", "ĐVT", "SL", "Đơn giá", "Thành tiền"],
                     [1, "Access Point Wifi7", "CW9176I", "Cái", 40, 8_440_200, 337_608_000],
                     [2, "Switch Layer 2 10G", "C9350-48HX", "Cái", 4, 555_400_200, 2_221_600_800],
                     [3, "SFP-10G-LR-S", None, "Cái", 32, 8_019_000, 256_608_000],
                     [4, "Dịch vụ cấu hình & dây nhảy", None, "Gói", 1, 16_200_000, 16_200_000],
                     [5, "Giá treo thiết bị", None, "Bộ", 1, 1_000_000, 1_000_000]])
    q = _upload(editor, rq["code"], "Network Co", content).json()["quotations"][0]
    editor.post(f"/api/spend/quotes/{rq['code']}/quotations/{q['id']}/verify")
    approver.post(f"/api/spend/quotes/{rq['code']}/select", json={"quotationId": q["id"], "reason": "1 NCC — thử nghiệm"})
    r = editor.post(f"/api/spend/renovations/{code}/pull-quotes")
    assert r.status_code == 409 and "1 dòng" in r.json()["detail"]
    m = editor.get(f"/api/spend/renovations/{code}/quote-mapping").json()
    assert [u["description"] for u in m["unmatched"]] == ["Giá treo thiết bị"]
    groups = editor.get("/api/spend/renovation-groups").json()["network"]
    impl = next(g["id"] for g in groups if g["name"].startswith("Chi phí triển khai"))
    assert editor.put(f"/api/spend/renovations/{code}/line-group", json={"lineId": m["unmatched"][0]["lineId"], "groupId": impl}).json()["unmatched"] == []
    d = editor.post(f"/api/spend/renovations/{code}/pull-quotes").json()
    net = d["comparison"]["network"]
    assert net["total"] == 2_833_016_800 and net["kpi"]["perNode"] == 15_231_274 and net["kinds"] == ["awarded"]
    assert net["median"] == 10_969_894 and "Khác cấu hình" in net["alerts"][0]["message"]
    # Estimate entered by an editor; thực chi only by an approver.
    sw = next(g["id"] for g in groups if g["name"] == "Switch")
    pr = editor.get(f"/api/spend/renovations/{code}").json()
    assert editor.put(f"/api/spend/renovations/{code}/costs", json={"costs": [{"groupId": sw, "kind": "estimate", "amount": "2.000.000.000"}]}).status_code == 200
    assert editor.put(f"/api/spend/renovations/{code}/costs", json={"costs": [{"groupId": sw, "kind": "actual", "amount": 1}]}).status_code == 403
    d = editor.get(f"/api/spend/renovations/{code}").json()
    assert d["costs"][str(sw)]["estimate"]["amount"] == 2_000_000_000 and any("dự toán 2.000.000.000" in w for w in d["warnings"])
    assert pr["rowVersion"] < d["rowVersion"]
    # Close: thực chi = báo giá trúng where missing; then the project is history and locked for editors.
    assert editor.post(f"/api/spend/renovations/{code}/close").status_code == 403
    d = approver.post(f"/api/spend/renovations/{code}/close").json()
    assert d["status"] == "closed" and d["costs"][str(sw)]["actual"]["amount"] == 2_221_600_800
    assert editor.put(f"/api/spend/renovations/{code}/costs", json={"costs": []}).status_code == 403
    # Exports
    x = editor.get(f"/api/spend/renovations/{code}/export.xlsx")
    assert x.status_code == 200
    wb = load_workbook(io.BytesIO(x.content))
    ws = wb["Summarize"]
    cells = {c.value for row in ws.iter_rows() for c in row if isinstance(c.value, str)}
    assert "=SUM(C3:C5)" in cells and any(v.startswith("=ROUNDUP(M") and v.endswith("/186,0)") for v in cells)
    assert any(v.startswith("=ROUNDUP(N") and v.endswith("/62,0)") for v in cells) and "không có số liệu" in cells
    assert {"Chỉ số", "Cảnh báo"} <= set(wb.sheetnames)
    pdf = editor.get(f"/api/spend/renovations/{code}/export.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    # Delete: admin only, blocked while quote requests are linked.
    assert editor.delete(f"/api/spend/renovations/{code}").status_code == 403
    assert admin.delete(f"/api/spend/renovations/{code}").status_code == 409


def test_groups_admin_and_viewer_roles(app_, admin, editor):
    viewer, nobody = _login(app_, "viewer1"), _login(app_, "nobody1")
    g = viewer.get("/api/spend/renovation-groups").json()
    assert len(g["cabling"]) == 14 and len(g["network"]) == 4 and len(g["vc"]) == 3
    assert nobody.get("/api/spend/renovations").status_code == 403
    assert viewer.post("/api/spend/renovations", json={"name": "x", "floor": "FL1", "year": 2026}).status_code == 403
    body = {"network": [{**x, "aliases": x["aliases"]} for x in g["network"]] + [{"name": "Firewall", "aliases": ["fortigate"]}]}
    assert editor.put("/api/spend/renovation-groups", json=body).status_code == 403
    out = admin.put("/api/spend/renovation-groups", json=body).json()
    assert [x["name"] for x in out["network"]][-1] == "Firewall"
    dup = {"network": body["network"] + [{"name": "firewall"}]}
    assert admin.put("/api/spend/renovation-groups", json=dup).status_code == 400


def test_zero_cells_are_no_data_not_zero():
    fl12 = {p.floor: p for p in rv.read_summarize(summarize_file()).projects}["FL12"]
    assert "CÁP QUANG" not in fl12.amounts["cabling"] and "RACK CABINET" not in fl12.amounts["cabling"]
    assert fl12.amounts["cabling"]["UPS"] == 77_623_920


def test_hardening_floor_historical_flag_and_pdf_headers(app_, admin, editor):
    approver = _login(app_, "approver1")
    bad = editor.post("/api/spend/renovations", json={"name": "x", "floor": "<IMG SRC=file:///etc/passwd>", "year": 2026})
    assert bad.status_code == 400
    assert editor.post("/api/spend/renovations", json={"name": "x", "floor": "TẦNG6", "year": 2026}).status_code == 400
    # Only an approver can mark a project as history (it is then closed without "Chốt thực chi").
    assert editor.post("/api/spend/renovations", json={"name": "x", "floor": "FL1", "year": 2020, "isHistorical": True}).status_code == 403
    d = approver.post("/api/spend/renovations", json={"name": "Cũ <b>", "floor": "FL1", "year": 2020, "isHistorical": True}).json()
    assert d["isHistorical"] and d["status"] == "closed"
    p = editor.post("/api/spend/renovations", json={"name": "Mới", "floor": "FL2", "year": 2026}).json()
    assert editor.put(f"/api/spend/renovations/{p['code']}", json={**p, "isHistorical": True}).status_code == 403
    assert editor.put(f"/api/spend/renovations/{p['code']}", json={**p, "rowVersion": "x"}).status_code == 400
    from app.report_renovation import render_html
    from app.routers.spend_renovations import build_detail
    from app.database import SessionLocal
    from app.spend.models import RenovationProject
    db = SessionLocal()
    html = render_html(build_detail(db, db.query(RenovationProject).filter_by(code=p["code"]).one()), "<script>")
    db.close()
    assert "<script>" not in html and "&lt;script&gt;" in html
    # Import never overwrites a project managed in the app.
    r = admin.post("/api/spend/renovations/import", files={"file": ("s.xlsx", summarize_file())},
                   data={"apply": json.dumps({"projects": [{"floor": "FL2", "year": 2026}]})})
    assert r.status_code == 200 and r.json()["created"] == []      # FL2 not in the file: nothing
    editor.post("/api/spend/renovations", json={"name": "FL12 đang làm", "floor": "FL12", "year": 2027})
    r = admin.post("/api/spend/renovations/import", files={"file": ("s.xlsx", summarize_file())},
                   data={"apply": json.dumps({"projects": [{"floor": "FL12", "year": 2027}]})})
    assert r.status_code == 400 and "người dùng quản lý" in r.json()["detail"]
