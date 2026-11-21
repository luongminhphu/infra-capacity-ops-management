"""PDF "Vòng đời & bảo mật" (A4, HSC) — ICO v2 G9b. Numbers from lifecycle.service.report (same as the page / Excel).

  Tổng kết → 1 Vòng đời ESXi / vCenter → 2 VMSA chưa vá → 3 Chứng chỉ TLS"""
from typing import Any, Dict

from .pdf_kit import base_css, data_table as _table, esc, exec_visual_css, html_to_pdf_bytes, level_pill, now_str, stat_tiles_html, vn as _vn


def _pill(lv: str) -> str:
    return level_pill(lv) if lv in ("crit", "bad", "warn", "ok") else '<span class="small-note">không xác định</span>'


def render_html(d: Dict[str, Any], username: str) -> str:
    generated = now_str()
    s = d["summary"]
    bad = lambda k: s[k]["crit"] + s[k]["bad"]  # noqa: E731
    worst = next((lv for lv in ("crit", "bad", "warn") if any(s[k][lv] for k in ("lifecycle", "vmsa", "certs"))), "ok")
    word = {"crit": "VƯỢT NGƯỠNG", "bad": "CẢNH BÁO", "warn": "THEO DÕI", "ok": "AN TOÀN"}[worst]
    headline = (f"{_vn(s['lifecycle']['crit'])} đối tượng đã hết hỗ trợ, {_vn(len(d['vmsa']))} VMSA chưa vá trên {_vn(s['vmsaHosts'])} đối tượng, "
                f"{_vn(bad('certs'))} chứng chỉ hết hạn trong 60 ngày.")
    tiles = stat_tiles_html([
        {"label": "Hết / sắp hết hỗ trợ", "value": _vn(bad("lifecycle")), "sub": f"{_vn(s['lifecycle']['warn'])} trong 1 năm", "cls": "na"},
        {"label": "VMSA chưa vá", "value": _vn(len(d["vmsa"])), "sub": f"{_vn(s['vmsaHosts'])} đối tượng", "cls": "na"},
        {"label": "Chứng chỉ ≤ 60 ngày", "value": _vn(bad("certs")), "sub": f"{_vn(s['certs']['warn'])} ≤ 90 ngày", "cls": "na"},
        {"label": "Chưa đọc được", "value": _vn(s["certs"]["na"]), "sub": "chứng chỉ", "cls": "na"}])
    life = _table(["Mức", "Đối tượng", "Phiên bản · build", "Hết hỗ trợ", "Còn (ngày)"],
                  [[_pill(x["level"]), f"<b>{esc(x['name'])}</b><br><span class='small-note'>{esc(x['productLabel'])} · {esc(x['vcenterKey'])}</span>",
                    esc(f"{x.get('version') or '—'} · {x.get('build') or '—'}"), esc(x["eogs"] or "chưa có trong bảng"), _vn(x["daysLeft"])]
                   for x in d["lifecycle"][:1000]], (4,))
    vm = _table(["Mức", "VMSA", "CVSS", "Đối tượng", "Build hiện tại → đã vá"],
                [[_pill(x["level"]), esc(x["vmsa"]) + (f"<br><span class='small-note'>{esc(x['title'])}</span>" if x.get("title") else ""),
                  _vn(x["cvss"], 1), esc(x["name"]), esc(f"{x.get('build')} → {x['fixedBuild']}")] for x in d["vmsa"][:1000]], (2,))
    certs = _table(["Mức", "Loại · tên", "Máy chủ", "Hết hạn", "Còn (ngày)", "Issuer"],
                   [[_pill(c["level"]), esc(f"{c['kindLabel']} · {c['name']}"), esc(f"{c['host']}:{c['port']}"),
                     esc((c["notAfter"] or "")[:10]) or esc(c.get("error") or "—"), _vn(c["daysLeft"]),
                     esc(c.get("issuer") or "—") + (" <span class='small-note'>(tự ký)</span>" if c.get("selfSigned") else "")] for c in d["certs"]], (4,))
    empty = '<div class="small-note">Không có.</div>'
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><style>{base_css('Vòng đời & bảo mật', generated)}{exec_visual_css()}</style></head>
<body>
  <div class="cover-header"><div>
    <div class="eyebrow">Infrastructure Capacity Ops · Vòng đời &amp; bảo mật · {esc(generated.split(' ')[-1])}</div>
    <div class="cover-title" style="margin-bottom:6px;">Vòng đời, bản vá bảo mật và chứng chỉ TLS</div>
    <div class="meta-row"><span>Lập bởi: <b>{esc(username)}</b></span></div>
  </div></div>
  <div class="exec-brief {worst}">
    <div class="exec-brief-head"><span class="status-pill {worst}">{esc(word)}</span><span class="exec-brief-title">Tổng kết</span></div>
    <div class="exec-brief-headline">{esc(headline)}</div>
    <div class="stat-row" style="margin-bottom:0;">{tiles}</div>
  </div>
  <div class="section breakable"><div class="section-title">1. Vòng đời ESXi / vCenter</div>{life if d['lifecycle'] else empty}</div>
  <div class="section breakable"><div class="section-title">2. VMSA chưa vá</div>{vm if d['vmsa'] else empty}</div>
  <div class="section breakable"><div class="section-title">3. Chứng chỉ TLS</div>{certs if d['certs'] else empty}</div>
</body></html>"""


def build_pdf(d: Dict[str, Any], username: str) -> bytes:
    return html_to_pdf_bytes(render_html(d, username))
