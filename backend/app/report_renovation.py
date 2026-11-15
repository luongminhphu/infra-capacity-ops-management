"""PDF "Cải tạo văn phòng — chi phí IT" (A4, same frame as the other spend reports).

  Tổng kết (cảnh báo → quyết định) → 1 Ba khối chi phí và NCC → 2 So sánh lịch sử theo chỉ số →
  3 Chi tiết theo nhóm chi phí
Numbers come from routers.spend_renovations.build_detail (the same dict as the web page and the Excel).
"""
from typing import Any, Dict, List, Optional

from .pdf_kit import _base_css, _exec_visual_css, _now_str, _stat_tiles_html, esc, html_to_pdf_bytes
from .report_spend import _css, _pill, _table, money_short
from .spend import renovation as rv
from .spend.service import money_vn

KIND = rv.KIND_LABELS


def _kpi_text(v: Optional[int]) -> str:
    return "không có số liệu" if v is None else money_vn(v)


def _summary(d: Dict[str, Any]) -> str:
    comp = d["comparison"]
    alerts = [a for ws in rv.WORKSTREAMS for a in comp[ws]["alerts"]]
    worst = "bad" if any(a["level"] == "crit" for a in alerts) else ("warn" if alerts or d["warnings"] else "ok")
    word = {"bad": "CẢNH BÁO", "warn": "THEO DÕI", "ok": "AN TOÀN"}[worst]
    total = sum(comp[ws]["total"] or 0 for ws in rv.WORKSTREAMS)
    items = [esc(a["message"]) for a in alerts] + [esc(w) for w in d["warnings"]]
    lst = "".join(f'<li class="rec-sum-item"><span class="rec-num">{i}</span><span class="rec-sum-text">{t}</span></li>'
                  for i, t in enumerate(items, 1)) or '<li class="rec-sum-item"><span class="rec-sum-text">Không có chênh lệch vượt ngưỡng ±15% so với lịch sử.</span></li>'
    tiles = _stat_tiles_html([{"label": rv.WS_LABELS[ws], "value": money_short(comp[ws]["total"]),
                               "sub": f"{_kpi_text(comp[ws]['kpi'][comp[ws]['mainKpi']])} {rv.KPI_LABELS[comp[ws]['mainKpi']]}"
                               if comp[ws]["kpi"][comp[ws]["mainKpi"]] is not None else "chưa tính được chỉ số",
                               "cls": "warn" if comp[ws]["alerts"] else "na"} for ws in rv.WORKSTREAMS] +
                             [{"label": "Tổng 3 khối", "value": money_short(total or None), "sub": "trước VAT"}])
    headline = (f"Dự án {d['floor']} ({d['year']}): tổng chi phí IT {money_vn(total)} trước VAT"
                f"{', ' + str(d['nodes']) + ' node' if d['nodes'] else ''}{', cấu hình ' + d['configTags'] if d['configTags'] else ''}.")
    return f"""
  <div class="exec-brief {worst}">
    <div class="exec-brief-head">{_pill(word, worst)}<span class="exec-brief-title">Tổng kết — chênh lệch &amp; quyết định</span></div>
    <div class="exec-brief-headline">{esc(headline)}</div>
    <ol class="rec-sum">{lst}</ol>
    <div class="stat-row" style="margin-bottom:0;">{tiles}</div>
  </div>"""


def _vendors(d: Dict[str, Any]) -> str:
    rows = []
    for ws in rv.WORKSTREAMS:
        v = d.get("vendors", {}).get(ws)
        if not v:
            rows.append([esc(rv.WS_LABELS[ws]), "—", "Chưa có gói báo giá", ""])
            continue
        for c in v["columns"]:
            rows.append([esc(rv.WS_LABELS[ws]), esc(v["request"]), esc(c["label"]) + (" " + _pill("Được chọn", "ok") if c["id"] == v["selected"] else ""),
                         esc(money_vn(c["subtotal"]))])
    return f"""
  <div class="section"><div class="section-title">1. Báo giá theo khối</div>
    <div class="section-sub">Tổng trước VAT của từng nhà cung cấp (tên viết tắt).</div>
    {_table(["Khối", "Gói", "Nhà cung cấp", "Trước VAT"], rows, (3,))}</div>"""


def _history(d: Dict[str, Any]) -> str:
    comp = d["comparison"]
    blocks = ""
    for ws in rv.WORKSTREAMS:
        c = comp[ws]
        main = c["mainKpi"]
        rows = [[f"<b>{esc(d['floor'])} ({d['year']}) — dự án này</b>", esc(d["configTags"] or "—"), esc(money_vn(c["total"])),
                 f"<b>{esc(_kpi_text(c['kpi'][main]))}</b>"]]
        for h in c["history"]:
            rows.append([esc(h["label"]), esc(h["config"] or "—") + ("" if h["sameConfig"] else " " + _pill("khác cấu hình", "na")),
                         esc(money_vn(h["total"])) if h["total"] is not None else "không có số liệu", esc(_kpi_text(h["kpi"][main]))])
        dev = f"{c['deviationPct']:+.1f}%".replace(".", ",") if c["deviationPct"] is not None else "—"
        blocks += (f'<div class="section-sub"><b>{esc(rv.WS_LABELS[ws])}</b> — trung vị lịch sử {esc(_kpi_text(c["median"]))} '
                   f'{esc(rv.KPI_LABELS[main])}, dự án này {esc(dev)}.</div>'
                   + _table(["Dự án", "Cấu hình", "Tổng", rv.KPI_LABELS[main]], rows, (2, 3)) + '<div style="height:8px"></div>')
    return f"""
  <div class="section"><div class="section-title">2. So sánh lịch sử theo chỉ số</div>
    <div class="section-sub">Lịch sử dùng thực chi (không có thì báo giá trúng). Chỉ số = ROUNDUP(tổng / số node hoặc số phòng họp VC).
    Ngưỡng cảnh báo ±15% so với trung vị các dự án cùng cấu hình.</div>{blocks}</div>"""


def _groups(d: Dict[str, Any]) -> str:
    comp = d["comparison"]
    out = ""
    for ws in rv.WORKSTREAMS:
        c = comp[ws]
        heads = [h["label"] for h in c["history"]]
        rows = []
        for r in c["rows"]:
            if r["current"] is None and not any(v is not None for v in r["history"]):
                continue
            dev = r["deviationPct"]
            rows.append([esc(r["name"]), esc(money_vn(r["current"])) if r["current"] is not None else "—"] +
                        [esc(money_vn(v)) if v is not None else "—" for v in r["history"]] +
                        [_pill(f"{dev:+.0f}%", "bad" if abs(dev) > 15 else "ok") if dev is not None else "—"])
        if rows:
            out += (f'<div class="section-sub"><b>{esc(rv.WS_LABELS[ws])}</b></div>' +
                    _table(["Nhóm chi phí", f"{d['floor']} ({d['year']})"] + heads + ["So trung vị"], rows,
                           tuple(range(1, len(heads) + 2))) + '<div style="height:8px"></div>')
    return f"""
  <div class="section"><div class="section-title">3. Chi tiết theo nhóm chi phí</div>{out or '<div class="small-note">Chưa có số liệu.</div>'}</div>"""


def render_html(d: Dict[str, Any], username: str) -> str:
    generated = _now_str()
    scale = " · ".join(x for x in [f"{d['nodes']} node" if d["nodes"] else "", f"{d['headcount']} NS" if d["headcount"] else "",
                                   f"{d['vcRooms']} phòng VC" if d["vcRooms"] else ""] if x) or "chưa nhập quy mô"
    body = _summary(d) + _vendors(d) + _history(d) + _groups(d)
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><style>{_base_css('Cải tạo văn phòng — chi phí IT', generated)}{_exec_visual_css()}{_css()}</style></head>
<body>
  <div class="cover-header"><div>
    <div class="eyebrow">Infrastructure Capacity Ops · Chi phí &amp; Hợp đồng · {esc(generated.split(' ')[-1])}</div>
    <div class="cover-title" style="margin-bottom:6px;">Cải tạo văn phòng {esc(d['floor'])} — chi phí IT</div>
    <div class="meta-row"><span>Dự án: <b>{esc(d['code'])}</b> · {esc(d['name'])}</span><span>Quy mô: <b>{esc(scale)}</b></span>
      <span>Số tiền: <b>VND, trước VAT</b></span><span>Lập bởi: <b>{esc(username)}</b></span></div>
  </div></div>
  {body}
</body></html>"""


def build_pdf(d: Dict[str, Any], username: str) -> bytes:
    return html_to_pdf_bytes(render_html(d, username))
