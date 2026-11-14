"""PDF "Kế hoạch ngân sách IT" (module Chi phí & Hợp đồng).

Same format as the operations reports: A4 portrait, short, opening with the box
"Tổng kết — vấn đề & quyết định đề nghị". Every number comes from
spend.report.build_budget_report — the same dict the web dashboard and the Excel use.

  Tổng kết → 1 So sánh năm nay / năm sau → 2 Theo loại chi phí → 3 Hết hạn theo quý →
  4 Opex phát sinh từ Capex → 5 Nhà cung cấp lớn nhất
"""
from typing import Any, Dict, List, Optional

from .report_pdf import (COLORS, STATUS_COLOR, _base_css, _exec_visual_css, _now_str, _stat_tiles_html, bar_cell, esc,
                         html_to_pdf_bytes, svg_bar_chart)
from .spend.service import date_vn, money_vn

MAX_EXPIRING_ROWS = 25
MAX_CAPEX_ROWS = 15


def _millions(v: int) -> int:
    # svg_bar_chart prints whole numbers: millions keep 3-4 significant digits for budget-size values.
    return int(round(v / 1e6))


def money_short(v: Optional[int]) -> str:
    """1.234.567.891 -> "1,23 tỷ"; under a billion -> "456,8 tr"."""
    if v is None:
        return "—"
    if v == 0:
        return "0"
    if abs(v) >= 1e9:
        return f"{v / 1e9:,.2f} tỷ".replace(",", "§").replace(".", ",").replace("§", ".")
    return f"{v / 1e6:,.1f} tr".replace(",", "§").replace(".", ",").replace("§", ".")


def _pill(text: str, cls: str) -> str:
    fg, bg = STATUS_COLOR.get(cls, STATUS_COLOR["na"])
    return (f'<span class="pill" style="color:{COLORS[fg]};background:{COLORS[bg]};border-color:{COLORS[fg]}55">'
            f'{esc(text)}</span>')


def _table(head: List[str], rows: List[List[str]], num_cols=()) -> str:
    if not rows:
        return '<div class="small-note">Không có dòng nào theo bộ lọc.</div>'
    ths = "".join(f'<th{" class=\"num\"" if i in num_cols else ""}>{h}</th>' for i, h in enumerate(head))
    trs = "".join("<tr>" + "".join(f'<td{" class=\"num\"" if i in num_cols else ""}>{c}</td>' for i, c in enumerate(r)) + "</tr>"
                  for r in rows)
    return f"<table><thead><tr>{ths}</tr></thead><tbody>{trs}</tbody></table>"


def _summary(r: Dict[str, Any]) -> str:
    t, cmp_ = r["totals"], r["compare"]
    decisions = r["decisions"]
    worst = "crit" if any(d["cls"] == "crit" for d in decisions) else ("bad" if any(d["cls"] == "bad" for d in decisions)
                                                                         else ("warn" if decisions else "ok"))
    word = {"crit": "VƯỢT NGƯỠNG", "bad": "CẢNH BÁO", "warn": "THEO DÕI", "ok": "AN TOÀN"}[worst]
    items = "".join(
        f'<li class="rec-sum-item"><span class="rec-num">{i}</span>{_pill({"crit": "Ngay", "bad": "Cao", "warn": "Kế hoạch"}.get(d["cls"], ""), d["cls"])}'
        f'<span class="rec-sum-text"><b>{esc(d["title"])}</b> ({esc(money_vn(d["amount"]))}) → {esc(d["decision"])}'
        f' <span class="rec-sum-time">· {esc(d["timeframe"])}</span></span></li>'
        for i, d in enumerate(decisions, 1))
    rec = f'<ol class="rec-sum">{items}</ol>' if items else '<div class="small-note">Không có khoản nào cần quyết định trong phạm vi lọc.</div>'
    soon_total = sum(g["total"] for g in r["expiringByQuarter"])
    tiles = _stat_tiles_html([
        {"label": f"NTC {cmp_['current']['fiscalYear']}", "value": money_short(cmp_["current"]["total"]),
         "sub": f"Opex {money_short(cmp_['current']['opex'])} · Capex {money_short(cmp_['current']['capex'])}"},
        {"label": f"NTC {cmp_['next']['fiscalYear']}", "value": money_short(cmp_["next"]["total"]),
         "sub": "so với năm nay: " + (f"{cmp_['deltaPct']:+.1f}%".replace(".", ",") if cmp_["deltaPct"] is not None else "—"),
         "cls": "warn" if (cmp_["deltaPct"] or 0) > 10 else "na"},
        {"label": "Hết hạn 12 tháng", "value": money_short(soon_total),
         "sub": f"{sum(g['count'] for g in r['expiringByQuarter'])} khoản Opex", "cls": "bad" if soon_total else "na"},
        {"label": "Opex từ Capex", "value": money_short(r["fromCapex"]["total"]), "sub": f"{r['fromCapex']['count']} khoản gia hạn"},
        {"label": "Tổng theo bộ lọc", "value": money_short(t["total"]), "sub": f"{t['count']} dòng · {t['vendorCount']} NCC"},
    ])
    return f"""
  <div class="exec-brief {worst}">
    <div class="exec-brief-head">{_pill(word, worst)}<span class="exec-brief-title">Tổng kết — vấn đề &amp; quyết định đề nghị</span></div>
    <div class="exec-brief-headline">{esc(r['headline'])}</div>
    {rec}
    <div class="stat-row" style="margin-bottom:0;">{tiles}</div>
  </div>"""


def _years(r: Dict[str, Any]) -> str:
    ys = r["byYear"]
    chart = svg_bar_chart([f"NTC {y['fiscalYear']}" for y in ys], [
        {"name": "Opex", "color": COLORS["primary"], "values": [_millions(y["opex"]) for y in ys]},
        {"name": "Capex", "color": COLORS["accent"], "values": [_millions(y["capex"]) for y in ys]},
    ], width=330, height=170, unit="")
    rows = [[f"NTC {y['fiscalYear']}", esc(money_vn(y["opex"])), esc(money_vn(y["capex"])), f"<b>{esc(money_vn(y['total']))}</b>"]
            for y in ys]
    rows.append(["<b>Tổng</b>", esc(money_vn(r["totals"]["opex"])), esc(money_vn(r["totals"]["capex"])),
                 f"<b>{esc(money_vn(r['totals']['total']))}</b>"])
    legend = (f'<div class="legend"><span><span class="dot" style="background:{COLORS["primary"]}"></span>Opex</span>'
              f'<span><span class="dot" style="background:{COLORS["accent"]}"></span>Capex</span><span>đơn vị: triệu đồng, sau VAT</span></div>')
    return f"""
  <div class="section">
    <div class="section-title">1. Ngân sách theo năm tài chính</div>
    <div class="two-col"><div>{chart}{legend}</div><div>{_table(["Năm", "Opex", "Capex", "Tổng"], rows, (1, 2, 3))}</div></div>
  </div>"""


def _categories(r: Dict[str, Any]) -> str:
    rows = [[esc(c["category"]), esc(money_vn(c["opex"])), esc(money_vn(c["capex"])), f"<b>{esc(money_vn(c['total']))}</b>",
             bar_cell(c["sharePct"], "ok", width_px=90)] for c in r["byCategory"]]
    return f"""
  <div class="section">
    <div class="section-title">2. Theo loại chi phí</div>
    {_table(["Loại", "Opex", "Capex", "Tổng", "Tỷ trọng"], rows, (1, 2, 3))}
  </div>"""


def _expiring(r: Dict[str, Any]) -> str:
    groups = r["expiringByQuarter"]
    if not groups:
        body = '<div class="small-note">Không có khoản Opex nào hết hạn trong 12 tháng tới theo bộ lọc.</div>'
    else:
        summary = _table(["Quý (theo NTC)", "Số khoản", "Tổng sau VAT"],
                         [[esc(g["label"]), str(g["count"]), esc(money_vn(g["total"]))] for g in groups], (1, 2))
        items = [i for g in groups for i in g["items"]][:MAX_EXPIRING_ROWS]
        detail = _table(["Mã", "Dịch vụ", "Nhà cung cấp", "Hết hạn", "Còn", "Sau VAT", "Phụ trách"], [
            [esc(i["code"]), esc(i["title"]), esc(i["vendor"]), esc(date_vn(_d(i["endDate"]))),
             _pill(f"{i['daysLeft']} ngày" if i["daysLeft"] >= 0 else f"quá {-i['daysLeft']} ngày",
                   "crit" if i["daysLeft"] < 0 else "bad" if i["daysLeft"] <= 30 else "warn" if i["daysLeft"] <= 90 else "ok"),
             esc(money_vn(i["amountGross"])), esc(i["owner"])] for i in items], (5,))
        more = sum(g["count"] for g in groups) - len(items)
        body = summary + '<div style="height:8px"></div>' + detail + (
            f'<div class="small-note">… và {more} khoản khác — xem file Excel.</div>' if more > 0 else "")
    return f"""
  <div class="section">
    <div class="section-title">3. Opex hết hạn theo quý</div>
    <div class="section-sub">Khoản dự kiến / đang hiệu lực thuộc các năm tài chính đã lọc, hết hạn từ 90 ngày trước đến 12 tháng tới (hoặc theo khoảng ngày đã lọc).</div>
    {body}
  </div>"""


def _d(iso: str):
    import datetime as dt
    return dt.date.fromisoformat(iso)


def _from_capex(r: Dict[str, Any]) -> str:
    fc = r["fromCapex"]
    items = fc["items"][:MAX_CAPEX_ROWS]
    by_year = " · ".join(f"NTC {y['fiscalYear']}: <b>{esc(money_vn(y['total']))}</b>" for y in fc["byYear"])
    table = _table(["Mã Opex", "Gia hạn", "NTC", "Dự án", "Trạng thái", "Sau VAT"], [
        [esc(i["code"]), esc(i["title"]), str(i["fiscalYear"]), esc(i["capexCode"] or "—"),
         _pill(i["statusLabel"], "na" if i["status"] == "planned" else "ok"), esc(money_vn(i["amountGross"]))] for i in items], (5,))
    more = fc["count"] - len(items)
    return f"""
  <div class="section">
    <div class="section-title">4. Opex phát sinh từ Capex</div>
    <div class="section-sub">Gia hạn bảo hành / license của các dự án đầu tư đã duyệt. {by_year}</div>
    {table}{f'<div class="small-note">… và {more} khoản khác — xem file Excel.</div>' if more > 0 else ''}
  </div>"""


def _vendors(r: Dict[str, Any]) -> str:
    rows = [[str(i), esc(v["vendor"]), str(v["count"]), esc(money_vn(v["opex"])), esc(money_vn(v["capex"])),
             f"<b>{esc(money_vn(v['total']))}</b>", bar_cell(v["sharePct"], "ok", width_px=80)]
            for i, v in enumerate(r["topVendors"], 1)]
    return f"""
  <div class="section">
    <div class="section-title">5. Nhà cung cấp lớn nhất</div>
    {_table(["#", "Nhà cung cấp", "Dòng", "Opex", "Capex", "Tổng", "Tỷ trọng"], rows, (2, 3, 4, 5))}
  </div>"""


def _css() -> str:
    return f"""
td.num, th.num {{ text-align:right; white-space:nowrap; }}
"""


def render_budget_html(r: Dict[str, Any], scope_label: str, logo_data_url: Optional[str] = None) -> str:
    generated = _now_str()
    logo = f'<img class="cover-logo" src="{logo_data_url}" alt="logo"/>' if logo_data_url else ""
    body = _summary(r) + _years(r) + _categories(r) + _expiring(r) + _from_capex(r) + _vendors(r)
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><style>{_base_css('Kế hoạch ngân sách IT', generated)}{_exec_visual_css()}{_css()}</style></head>
<body>
  <div class="cover-header">
    <div>
      <div class="eyebrow">Infrastructure Capacity Ops · Chi phí &amp; Hợp đồng · {esc(generated.split(' ')[-1])}</div>
      <div class="cover-title" style="margin-bottom:6px;">Kế hoạch ngân sách IT</div>
      <div class="meta-row"><span>Phạm vi: <b>{esc(scope_label)}</b></span><span>Số tiền: <b>VND, sau VAT</b></span></div>
    </div>
    {logo}
  </div>
  {body}
</body></html>"""


def build_budget_pdf(r: Dict[str, Any], scope_label: str, logo_data_url: Optional[str] = None) -> bytes:
    return html_to_pdf_bytes(render_budget_html(r, scope_label, logo_data_url))
