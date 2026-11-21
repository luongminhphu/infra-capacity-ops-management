"""PDF "Kết quả rà soát owner" (A4, HSC) — evidence of a review period (ISO 27001 access / asset review).

  Tổng kết (tỷ lệ phản hồi, tài nguyên thu hồi) → 1 Quyết định theo loại → 2 Phản hồi theo owner → 3 Chi tiết VM
Numbers come from owners.review.summary (the same dict as the web page and the Excel)."""
from typing import Any, Dict

from .pdf_kit import base_css, data_table as _table, esc, exec_visual_css, html_to_pdf_bytes, now_str, stat_tiles_html, vn as _vn


def render_html(d: Dict[str, Any], username: str) -> str:
    generated = now_str()
    c, t, r = d["campaign"], d["totals"], d["reclaim"]
    resp = t["responsePct"]
    worst = "ok" if (resp or 0) >= 90 else ("warn" if (resp or 0) >= 60 else "bad")
    word = {"ok": "AN TOÀN", "warn": "THEO DÕI", "bad": "CẢNH BÁO"}[worst]
    money = f"{_vn(r['vndMonthly'])} đ/tháng · {_vn(r['vndYearly'])} đ/năm" if r["vndMonthly"] is not None else "chưa khai đơn giá"
    status = "Đã đóng" if c["status"] == "closed" else "Đang mở"
    headline = (f"Kỳ {c['periodLabel']}: {_vn(t['decided'])}/{_vn(t['owned'])} VM có quyết định của owner "
                f"({_vn(resp, 1) if resp is not None else '—'}%), thu hồi {_vn(r['vcpu'])} vCPU · {_vn(r['ramGB'])} GB RAM · "
                f"{_vn(r['diskGB'])} GB disk.")
    points = [f"{_vn(t['undecided'])} VM chưa có quyết định của owner" + (" — chờ team hạ tầng xác nhận." if c["status"] == "closed" else "."),
              f"{_vn(t['queue'])} VM chuyển owner / không phải của owner — chờ gán lại ở Hệ thống & Owner.",
              f"{_vn(t['unowned'])} VM chưa có owner — team hạ tầng tự rà.",
              f"Giá trị tài nguyên thu hồi: {money}."]
    lst = "".join(f'<li class="rec-sum-item"><span class="rec-num">{i}</span><span class="rec-sum-text">{esc(p)}</span></li>'
                  for i, p in enumerate(points, 1))
    tiles = stat_tiles_html([
        {"label": "Tỷ lệ phản hồi", "value": f"{_vn(resp, 1)}%" if resp is not None else "—", "sub": f"{_vn(t['decided'])}/{_vn(t['owned'])} VM", "cls": worst},
        {"label": "vCPU thu hồi", "value": _vn(r["vcpu"]), "sub": "thu hồi + giảm cấu hình", "cls": "na"},
        {"label": "RAM thu hồi", "value": f"{_vn(r['ramGB'])} GB", "sub": "", "cls": "na"},
        {"label": "Disk thu hồi", "value": f"{_vn(r['diskGB'])} GB", "sub": "VM thu hồi", "cls": "na"},
        {"label": "VM nên rà", "value": _vn(t["flagged"]), "sub": f"trên {_vn(t['items'])} VM", "cls": "na"}])
    by_dec = _table(["Quyết định", "Số VM"], [[esc(x["label"]), _vn(x["count"])] for x in d["byDecision"]]
                    + [["Chưa quyết định", _vn(t["undecided"])]], (1,))
    owners = _table(["Owner", "VM", "Đã quyết định", "Tỷ lệ"],
                    [[esc(o["ownerEmail"]), _vn(o["items"]), _vn(o["decided"]), f"{_vn(o['pct'], 1)}%"] for o in d["owners"]], (1, 2, 3))
    detail_rows = []
    for i in d["items"]:
        extra = ""
        if i["decision"] == "decommission" and i["actionDate"]:
            extra = f" · {i['actionDate'][8:10]}/{i['actionDate'][5:7]}/{i['actionDate'][:4]}"
        elif i["decision"] == "resize":
            extra = f" · {_vn(i['newVcpu']) if i['newVcpu'] else _vn(i['vcpu'])} vCPU / {_vn(i['newRamGB']) if i['newRamGB'] else _vn(i['ramGB'])} GB"
        elif i["decision"] == "transfer":
            extra = f" → {i['transferTo']}"
        detail_rows.append([f"<b>{esc(i['vm'])}</b><br><span class='small-note'>{esc(i['vcenterKey'])}</span>", esc(i.get("ip") or "—"),
                            esc(i["systemCode"] or "—"),
                            esc(i["ownerEmail"] or "chưa có owner"), esc(i["decisionLabel"] + extra), esc(i["decidedBy"] or "—"),
                            esc("; ".join(i["reasons"]) or "—")])
    detail = _table(["VM", "IP", "Hệ thống", "Owner", "Quyết định", "Người quyết định", "Tín hiệu lúc mở kỳ"], detail_rows)
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><style>{base_css('Kết quả rà soát owner', generated)}{exec_visual_css()}</style></head>
<body>
  <div class="cover-header"><div>
    <div class="eyebrow">Infrastructure Capacity Ops · Rà soát owner · {esc(generated.split(' ')[-1])}</div>
    <div class="cover-title" style="margin-bottom:6px;">Kết quả rà soát owner — kỳ {esc(c['periodLabel'])}</div>
    <div class="meta-row"><span>Trạng thái: <b>{esc(status)}</b></span><span>Hạn xác nhận: <b>{esc(c['dueAt'][8:10])}/{esc(c['dueAt'][5:7])}/{esc(c['dueAt'][:4])}</b></span>
      <span>VM trong kỳ: <b>{_vn(t['items'])}</b></span><span>Lập bởi: <b>{esc(username)}</b></span></div>
  </div></div>
  <div class="exec-brief {worst}">
    <div class="exec-brief-head"><span class="status-pill {worst}">{esc(word)}</span><span class="exec-brief-title">Tổng kết — phản hồi &amp; tài nguyên thu hồi</span></div>
    <div class="exec-brief-headline">{esc(headline)}</div>
    <ol class="rec-sum">{lst}</ol>
    <div class="stat-row" style="margin-bottom:0;">{tiles}</div>
  </div>
  <div class="section"><div class="section-title">1. Quyết định theo loại</div>{by_dec}</div>
  <div class="section"><div class="section-title">2. Phản hồi theo owner</div>{owners or '<div class="small-note">Không có owner.</div>'}</div>
  <div class="section"><div class="section-title">3. Chi tiết VM</div>
    <div class="section-sub">Bằng chứng rà soát: quyết định, người quyết định và tín hiệu tại lúc mở kỳ.</div>{detail}</div>
</body></html>"""


def build_pdf(d: Dict[str, Any], username: str) -> bytes:
    return html_to_pdf_bytes(render_html(d, username))
