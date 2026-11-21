"""PDF "Baseline cấu hình" (A4, HSC) — ICO v2 G9a. Numbers from baseline.service.report (same as the page / Excel).

  Tổng kết (lệch theo mức, quy tắc lệch nhiều nhất) → 1 Quy tắc → 2 Lệch cấu hình → 3 Ngoại lệ đã chấp nhận"""
from typing import Any, Dict

from .pdf_kit import base_css, data_table as _table, esc, exec_visual_css, html_to_pdf_bytes, level_pill, now_str, stat_tiles_html, vn as _vn


def render_html(d: Dict[str, Any], username: str) -> str:
    generated = now_str()
    s = d["summary"]
    worst = next((lv for lv in ("crit", "bad", "warn") if s["byLevel"][lv]), "ok")
    word = {"crit": "VƯỢT NGƯỠNG", "bad": "CẢNH BÁO", "warn": "THEO DÕI", "ok": "AN TOÀN"}[worst]
    top = sorted((r for r in d["rules"] if r["findings"]), key=lambda r: -r["findings"])[:4]
    headline = (f"{_vn(s['findings'])} lệch cấu hình trên {_vn(s['hosts'])} host và {_vn(s['vms'])} VM; "
                f"{_vn(s['excepted'])} lệch đã chấp nhận có hạn.") if s["findings"] else "Không có lệch cấu hình ngoài các ngoại lệ đã chấp nhận."
    lst = "".join(f'<li class="rec-sum-item"><span class="rec-num">{i}</span><span class="rec-sum-text">{esc(r["label"])}: '
                  f'{_vn(r["findings"])} đối tượng lệch</span></li>' for i, r in enumerate(top, 1))
    tiles = stat_tiles_html([
        {"label": "Lệch", "value": _vn(s["findings"]), "sub": "chưa có ngoại lệ", "cls": worst},
        {"label": "CẢNH BÁO", "value": _vn(s["byLevel"]["bad"] + s["byLevel"]["crit"]), "sub": "mức cao", "cls": "na"},
        {"label": "THEO DÕI", "value": _vn(s["byLevel"]["warn"]), "sub": "", "cls": "na"},
        {"label": "Ngoại lệ", "value": _vn(s["excepted"]), "sub": "còn hạn", "cls": "na"}])
    rules = _table(["Quy tắc", "Phạm vi", "Mức", "Đã kiểm", "Lệch", "Ngoại lệ"],
                   [[esc(r["label"]) + ("" if r["enabled"] else " <span class='small-note'>(tắt)</span>"), esc(r["scopeLabel"]), level_pill(r["level"]),
                     _vn(r["checked"]), _vn(r["findings"]), _vn(r["excepted"])] for r in d["rules"]], (3, 4, 5))
    cell = lambda f: f"<b>{esc(f['entity'])}</b>" + (f"<br><span class='small-note'>{esc(f['ip'])}</span>" if f.get("ip") else "")  # noqa: E731
    finds = _table(["Mã", "Mức", "Quy tắc", "Đối tượng", "Cluster", "Chi tiết"],
                   [[esc(f["code"]), level_pill(f["level"]), esc(f["label"]), cell(f), esc(f.get("cluster") or "—"), esc(f["detail"])]
                    for f in d["findings"][:1500]])
    exc = _table(["Mã", "Quy tắc", "Đối tượng", "Lý do", "Đến ngày"],
                 [[esc(f["code"]), esc(f["label"]), cell(f), esc(f["exception"]["reason"]), esc(f["exception"]["until"])] for f in d["excepted"]])
    empty = '<div class="small-note">Không có.</div>'
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><style>{base_css('Baseline cấu hình', generated)}{exec_visual_css()}</style></head>
<body>
  <div class="cover-header"><div>
    <div class="eyebrow">Infrastructure Capacity Ops · Baseline cấu hình · {esc(generated.split(' ')[-1])}</div>
    <div class="cover-title" style="margin-bottom:6px;">Baseline cấu hình ESXi / cluster / VM</div>
    <div class="meta-row"><span>Host: <b>{_vn(s['hosts'])}</b></span><span>VM: <b>{_vn(s['vms'])}</b></span><span>Lập bởi: <b>{esc(username)}</b></span></div>
  </div></div>
  <div class="exec-brief {worst}">
    <div class="exec-brief-head"><span class="status-pill {worst}">{esc(word)}</span><span class="exec-brief-title">Tổng kết</span></div>
    <div class="exec-brief-headline">{esc(headline)}</div>
    {f'<ol class="rec-sum">{lst}</ol>' if lst else ''}
    <div class="stat-row" style="margin-bottom:0;">{tiles}</div>
  </div>
  <div class="section"><div class="section-title">1. Quy tắc</div>{rules}</div>
  <div class="section breakable"><div class="section-title">2. Lệch cấu hình</div>{finds if d['findings'] else empty}</div>
  <div class="section breakable"><div class="section-title">3. Ngoại lệ đã chấp nhận</div>{exc if d['excepted'] else empty}</div>
</body></html>"""


def build_pdf(d: Dict[str, Any], username: str) -> bytes:
    return html_to_pdf_bytes(render_html(d, username))
