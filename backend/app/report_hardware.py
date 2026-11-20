"""PDF "Sức khoẻ phần cứng" (A4, HSC) — Dell OME servers: status, failing parts, disks predicting failure, firmware
behind the baseline, Dell warranty vs Opex. Numbers come from hardware.service.listing (the same dict as the web page
Vận hành → Phần cứng and the Excel), so the three always agree.

  Tổng kết → 1 Máy chủ cần xử lý → 2 Bảo hành Dell ↔ Opex → 3 Danh sách máy chủ"""
from typing import Any, Dict, List

from .pdf_kit import base_css, esc, exec_visual_css, html_to_pdf_bytes, now_str, stat_tiles_html, status_pill

LEVEL_TEXT = {"crit": "VƯỢT NGƯỠNG", "bad": "CẢNH BÁO", "warn": "THEO DÕI", "ok": "AN TOÀN"}


def _vn(v: Any) -> str:
    if v is None:
        return "—"
    return f"{int(v):,}".replace(",", ".")


def _date(iso: Any) -> str:
    s = str(iso or "")
    return f"{s[8:10]}/{s[5:7]}/{s[:4]}" if len(s) >= 10 else "—"


def _table(head: List[str], rows: List[List[str]], num: tuple = ()) -> str:
    th = "".join(f'<th class="{"num" if i in num else ""}">{esc(h)}</th>' for i, h in enumerate(head))
    tr = "".join("<tr>" + "".join(f'<td class="{"num" if i in num else ""}">{c}</td>' for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f'<table class="data-table"><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table>'


def _pill(level: str) -> str:
    return status_pill({"text": LEVEL_TEXT.get(level, "NA"), "cls": level if level in LEVEL_TEXT else "na"})


def _server(i: Dict[str, Any]) -> str:
    return (f"<b>{esc(i['name'])}</b><br><span class='small-note'>{esc(i['serviceTag'] or '—')} · {esc(i['model'] or '—')}</span>")


def _warranty_cell(w: Dict[str, Any]) -> str:
    if not w.get("dellEnd"):
        return "Chưa có dữ liệu"
    days = w.get("daysLeft")
    note = f"hết {_vn(-days)} ngày trước" if days is not None and days < 0 else f"còn {_vn(days)} ngày"
    return f"{_date(w['dellEnd'])}<br><span class='small-note'>{esc(note)}</span>"


def _opex_cell(w: Dict[str, Any]) -> str:
    if w.get("opexState") in (None, "none"):
        return "Chưa có Opex"
    label = esc(w["opexCode"]) if w.get("opexCode") else {"active": "Còn hiệu lực", "planned": "Chưa bắt đầu", "expired": "Đã hết hạn"}.get(w["opexState"], "")
    return f"{label}<br><span class='small-note'>hết {_date(w.get('opexEnd'))}</span>"


def render_html(d: Dict[str, Any], username: str) -> str:
    generated = now_str()
    s, items = d["summary"], d["items"]
    lv = s["byLevel"]
    worst = next((k for k in ("crit", "bad", "warn") if lv.get(k)), "ok")
    need = [i for i in items if i["level"] != "ok"]
    headline = (f"{_vn(s['servers'])} máy chủ Dell: {_vn(lv['crit'])} VƯỢT NGƯỠNG, {_vn(lv['bad'])} CẢNH BÁO, {_vn(lv['warn'])} THEO DÕI; "
                f"{_vn(s['disksPredictive'])} ổ đĩa dự báo hỏng; {_vn(s['warrantyExpired'])} máy hết bảo hành Dell.")
    points = [f"{_vn(s['disksPredictive'])} ổ đĩa dự báo hỏng — thay trước khi hỏng hẳn.",
              f"{_vn(s['firmwareBehind'])} máy có firmware lệch baseline OME.",
              f"{_vn(s['warrantyExpired'])} máy hết bảo hành Dell, {_vn(s['warrantyExpiring90'])} máy hết trong 90 ngày tới.",
              f"{_vn(s['noOpex'])} máy có bảo hành Dell nhưng chưa có Opex bảo hành."]
    lst = "".join(f'<li class="rec-sum-item"><span class="rec-num">{n}</span><span class="rec-sum-text">{esc(p)}</span></li>'
                  for n, p in enumerate(points, 1))
    tiles = stat_tiles_html([
        {"label": "Máy chủ", "value": _vn(s["servers"]), "sub": f"{_vn(s['vmsOn'])} VM đang chạy", "cls": "na"},
        {"label": "Cần xử lý", "value": _vn(len(need)), "sub": f"{_vn(lv['crit'])} vượt ngưỡng", "cls": worst},
        {"label": "Ổ dự báo hỏng", "value": _vn(s["disksPredictive"]), "sub": "SMART", "cls": "crit" if s["disksPredictive"] else "ok"},
        {"label": "Firmware lệch", "value": _vn(s["firmwareBehind"]), "sub": "so baseline OME", "cls": "warn" if s["firmwareBehind"] else "ok"},
        {"label": "Hết bảo hành", "value": _vn(s["warrantyExpired"]), "sub": f"{_vn(s['warrantyExpiring90'])} hết trong 90 ngày", "cls": "bad" if s["warrantyExpired"] else "ok"}])
    sec1 = _table(["Máy chủ", "Trạng thái", "Lý do", "Host / hệ thống"],
                  [[_server(i), _pill(i["level"]), esc("; ".join(i["reasons"][:4]) or "—"),
                    esc(", ".join(h["name"] for h in i["hosts"][:2]) or "bare-metal")
                    + (f"<br><span class='small-note'>{esc(', '.join(x['code'] for x in i['systems'][:4]))}</span>" if i["systems"] else "")]
                   for i in need]) if need else '<div class="small-note">Không có máy chủ cần xử lý.</div>'
    wrows = [i for i in items if i["warranty"].get("case") or (i["warranty"].get("daysLeft") is not None and i["warranty"]["daysLeft"] <= 90)]
    sec2 = _table(["Máy chủ", "Bảo hành Dell đến", "Opex bảo hành", "Chênh lệch"],
                  [[_server(i), _warranty_cell(i["warranty"]), _opex_cell(i["warranty"]),
                    (f"{esc(i['warranty']['code'])}<br><span class='small-note'>{esc(i['warranty']['caseLabel'])}</span>"
                     if i["warranty"].get("case") else "Sắp hết bảo hành")] for i in wrows]) \
        if wrows else '<div class="small-note">Không có chênh lệch bảo hành và không máy nào hết bảo hành trong 90 ngày.</div>'
    sec3 = _table(["Máy chủ", "iDRAC", "Nguồn", "Trạng thái", "VM", "Bảo hành Dell"],
                  [[_server(i), esc(i["idracIp"] or "—"), esc(i["powerText"]), _pill(i["level"]), _vn(i["vmCount"]),
                    _warranty_cell(i["warranty"])] for i in items], (4,))
    scope = d.get("ownerScope") or ""
    f = d.get("filters") or {}
    flt = "; ".join(x for x in (f"trạng thái {LEVEL_TEXT[f['status']]}" if f.get("status") else "",
                                f"model {f['model']}" if f.get("model") else "",
                                f"hết bảo hành trong {f['warrantyDays']} ngày" if f.get("warrantyDays") is not None else "", scope) if x)
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><style>{base_css('Sức khoẻ phần cứng', generated)}{exec_visual_css()}</style></head>
<body>
  <div class="cover-header"><div>
    <div class="eyebrow">Infrastructure Capacity Ops · Phần cứng · {esc(generated.split(' ')[-1])}</div>
    <div class="cover-title" style="margin-bottom:6px;">Sức khoẻ phần cứng — máy chủ Dell (OME)</div>
    <div class="meta-row"><span>Máy chủ: <b>{_vn(s['servers'])}</b></span><span>Phạm vi: <b>{esc(flt or 'toàn bộ')}</b></span>
      <span>Lập bởi: <b>{esc(username)}</b></span></div>
  </div></div>
  <div class="exec-brief {worst}">
    <div class="exec-brief-head">{_pill(worst)}<span class="exec-brief-title">Tổng kết — trạng thái, linh kiện, bảo hành</span></div>
    <div class="exec-brief-headline">{esc(headline)}</div>
    <ol class="rec-sum">{lst}</ol>
    <div class="stat-row" style="margin-bottom:0;">{tiles}</div>
  </div>
  <div class="section"><div class="section-title">1. Máy chủ cần xử lý</div>{sec1}</div>
  <div class="section"><div class="section-title">2. Bảo hành Dell ↔ Opex</div>
    <div class="section-sub">Chênh lệch giữa bảo hành Dell (OME) và Opex bảo hành phần cứng, cùng các máy hết bảo hành trong 90 ngày.</div>{sec2}</div>
  <div class="section"><div class="section-title">3. Danh sách máy chủ</div>{sec3}</div>
</body></html>"""


def build_pdf(d: Dict[str, Any], username: str) -> bytes:
    return html_to_pdf_bytes(render_html(d, username))
