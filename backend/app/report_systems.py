"""PDF "Danh sách VM theo hệ thống" (A4, HSC) — inventory of assets per business system and its owners
(ISO/IEC 27001:2022 A.5.9 Inventory of information and other associated assets).

  Tổng kết → 1 Tổng hợp theo hệ thống → 2 Chi tiết từng hệ thống (liên hệ + VM) → 3 VM chưa thuộc hệ thống
Numbers come from owners.inventory.build (the same dict as the Excel and the e-mail drafts)."""
from typing import Any, Dict, List

from .owners.mail_drafts import guest_name
from .pdf_kit import base_css, esc, exec_visual_css, html_to_pdf_bytes, now_str, stat_tiles_html

MAX_UNOWNED_ROWS = 500


def _vn(v: Any, d: int = 0) -> str:
    if v is None:
        return "—"
    s = f"{float(v):,.{d}f}"
    return s.replace(",", "\0").replace(".", ",").replace("\0", ".")


def _table(head: List[str], rows: List[List[str]], num: tuple = ()) -> str:
    th = "".join(f'<th class="{"num" if i in num else ""}">{esc(h)}</th>' for i, h in enumerate(head))
    tr = "".join("<tr>" + "".join(f'<td class="{"num" if i in num else ""}">{c}</td>' for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f'<table class="data-table"><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table>'


def _person(email, name) -> str:
    if not email and not name:
        return "<span class='small-note'>chưa có</span>"
    return f"{esc(name or '')}{'<br>' if name and email else ''}<span class='small-note'>{esc(email or '')}</span>"


def _vm_rows(vms: List[Dict[str, Any]], backup: bool) -> List[List[str]]:
    rows = []
    for v in vms:
        gn = guest_name(v)
        r = [f"<b>{esc(v['vm'])}</b>" + (f"<br><span class='small-note'>Hostname: {esc(gn)}</span>" if gn else "")
             + f"<br><span class='small-note'>{esc(v['vcenterKey'])}</span>",
             f"{esc(v['cluster'] or '—')}<br><span class='small-note'>{esc(v['host'] or '')}</span>",
             "Bật" if v["powerOn"] else "Tắt", _vn(v["vcpu"]), _vn(v["ramGB"]), _vn(v["diskGB"]),
             esc(v["os"] or "—"), esc(v["ip"] or "—")]
        if backup:
            r.append(esc(v["backupLabel"]))
        rows.append(r)
    return rows


VM_HEAD = ["VM", "Cluster / host", "Nguồn", "vCPU", "RAM (GB)", "Disk (GB)", "Hệ điều hành", "IP"]


def render_html(d: Dict[str, Any], username: str) -> str:
    generated = now_str()
    t, systems, unowned, bk = d["totals"], d["systems"], d["unowned"], d["backupMonitored"]
    head = VM_HEAD + (["Backup"] if bk else [])
    issues = (t["noOwner"] or 0) + (t["unowned"] or 0) + (t["unprotected"] or 0)
    worst = "warn" if issues else "ok"
    word = "THEO DÕI" if issues else "AN TOÀN"
    headline = (f"{_vn(t['systems'])} hệ thống, {_vn(t['vms'])} VM ({_vn(t['on'])} bật · {_vn(t['off'])} tắt) — "
                f"{_vn(t['vcpu'])} vCPU · {_vn(t['ramGB'])} GB RAM · {_vn(t['diskGB'])} GB disk.")
    points = [f"{_vn(t['noOwner'])} hệ thống chưa có owner — chỉ định owner trước kỳ rà soát." if t["noOwner"]
              else "Mọi hệ thống đều có owner.",
              f"{_vn(t['noVm'])} hệ thống chưa có VM nào." if t["noVm"] else "Mọi hệ thống đều có VM."]
    if t["unowned"] is not None:
        points.append(f"{_vn(t['unowned'])} VM chưa thuộc hệ thống nào — gán ở Hệ thống & Owner → VM chưa có owner."
                      if t["unowned"] else "Mọi VM đã thuộc một hệ thống.")
    if bk:
        points.append(f"{_vn(t['unprotected'])} VM đang bật chưa có backup, {_vn(t['failing'])} VM backup lỗi.")
    lst = "".join(f'<li class="rec-sum-item"><span class="rec-num">{i}</span><span class="rec-sum-text">{esc(p)}</span></li>'
                  for i, p in enumerate(points, 1))
    tiles = stat_tiles_html([
        {"label": "Hệ thống", "value": _vn(t["systems"]), "sub": f"{_vn(t['noOwner'])} chưa có owner", "cls": "warn" if t["noOwner"] else "ok"},
        {"label": "VM", "value": _vn(t["vms"]), "sub": f"{_vn(t['on'])} bật · {_vn(t['off'])} tắt", "cls": "na"},
        {"label": "vCPU", "value": _vn(t["vcpu"]), "sub": "", "cls": "na"},
        {"label": "RAM", "value": f"{_vn(t['ramGB'])} GB", "sub": "", "cls": "na"},
        {"label": "Disk", "value": f"{_vn(t['diskGB'])} GB", "sub": "cấp phát", "cls": "na"}])
    summary = _table(["Mã", "Hệ thống", "Môi trường · mức", "Owner", "Backup", "VM", "vCPU", "RAM (GB)", "Disk (GB)"],
                     [[esc(s["code"]), esc(s["name"]), f"{esc(s['envLabel'])}<br><span class='small-note'>{esc(s['criticalityLabel'])}</span>",
                       _person(s["ownerEmail"], s["ownerName"]), _person(s["backupEmail"], s["backupName"]),
                       _vn(s["totals"]["vms"]), _vn(s["totals"]["vcpu"]), _vn(s["totals"]["ramGB"]), _vn(s["totals"]["diskGB"])]
                      for s in systems], (5, 6, 7, 8))
    blocks = []
    for s in systems:
        st = s["totals"]
        meta = [f"Owner: <b>{_person(s['ownerEmail'], s['ownerName'])}</b>", f"Backup: {_person(s['backupEmail'], s['backupName'])}"]
        if s["supportEmails"] or s["supportName"] or s["supportContact"]:
            meta.append("Support: " + esc(" · ".join(x for x in (s["supportName"], ", ".join(s["supportEmails"]), s["supportContact"]) if x)))
        if s["managerEmail"]:
            meta.append(f"Quản lý: {esc(s['managerEmail'])}")
        info = " · ".join(esc(x) for x in (s["project"], s["department"], s["envLabel"], s["criticalityLabel"]) if x)
        body = _table(head, _vm_rows(s["vms"], bk), (3, 4, 5)) if s["vms"] else '<div class="small-note">Hệ thống chưa có VM.</div>'
        blocks.append(f"""<div class="section" style="page-break-inside:auto">
  <div class="section-title" style="font-size:12px">{esc(s['code'])} — {esc(s['name'])}</div>
  <div class="section-sub">{info}</div>
  <div class="small-note" style="margin-bottom:4px">{' · '.join(meta)}</div>
  <div class="small-note" style="margin-bottom:6px">{_vn(st['vms'])} VM ({_vn(st['on'])} bật) · {_vn(st['vcpu'])} vCPU · {_vn(st['ramGB'])} GB RAM · {_vn(st['diskGB'])} GB disk</div>
  {body}</div>""")
    un = ""
    if unowned is not None:
        shown = unowned[:MAX_UNOWNED_ROWS]
        more = (f'<div class="small-note">… và {_vn(len(unowned) - len(shown))} VM khác — xem đủ trong file Excel.</div>'
                if len(unowned) > len(shown) else "")
        un = (f'<div class="section"><div class="section-title">3. VM chưa thuộc hệ thống ({_vn(len(unowned))})</div>'
              f'<div class="section-sub">Chưa có owner chịu trách nhiệm — gán vào hệ thống trước kỳ rà soát.</div>'
              + (_table(head, _vm_rows(shown, bk), (3, 4, 5)) + more if unowned else '<div class="small-note">Không có.</div>') + "</div>")
    scope = f"<span>Phạm vi: <b>{esc(d['scope'])}</b></span>" if d["scope"] else "<span>Phạm vi: <b>toàn bộ hệ thống</b></span>"
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><style>{base_css('Danh sách VM theo hệ thống', generated)}{exec_visual_css()}</style></head>
<body>
  <div class="cover-header"><div>
    <div class="eyebrow">Infrastructure Capacity Ops · Hệ thống &amp; Owner · {esc(generated.split(' ')[-1])}</div>
    <div class="cover-title" style="margin-bottom:6px;">Danh sách VM theo hệ thống</div>
    <div class="meta-row">{scope}<span>Lập bởi: <b>{esc(username)}</b></span><span>Lúc: <b>{esc(generated)}</b></span></div>
  </div></div>
  <div class="exec-brief {worst}">
    <div class="exec-brief-head"><span class="status-pill {worst}">{esc(word)}</span><span class="exec-brief-title">Tổng kết — tài sản theo hệ thống và owner</span></div>
    <div class="exec-brief-headline">{esc(headline)}</div>
    <ol class="rec-sum">{lst}</ol>
    <div class="stat-row" style="margin-bottom:0;">{tiles}</div>
  </div>
  <div class="section"><div class="section-title">1. Tổng hợp theo hệ thống</div>
    <div class="section-sub">Sổ tài sản theo hệ thống và người chịu trách nhiệm (ISO/IEC 27001:2022 A.5.9).</div>
    {summary if systems else '<div class="small-note">Không có hệ thống trong phạm vi.</div>'}</div>
  <div class="section"><div class="section-title">2. Chi tiết từng hệ thống</div></div>
  {''.join(blocks)}
  {un}
</body></html>"""


def build_pdf(d: Dict[str, Any], username: str) -> bytes:
    return html_to_pdf_bytes(render_html(d, username))
