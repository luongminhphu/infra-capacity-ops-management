"""PDF "Tổng kết nhu cầu năm & kế hoạch năm sau" (A4, HSC) — ICO v2 G8.

  Trang 1 kết luận (số host cần thêm, ngân sách, nhu cầu ròng và nguồn số) → Phụ lục A tổng kết năm, B xin → cấp → dùng,
  C nhu cầu ròng theo cluster, D nhu cầu đã biết và thu hồi.
Numbers come from itsm.annual.build (the same dict as the web page and the Excel)."""
from typing import Any, Dict

from .pdf_kit import base_css, data_table as _table, esc, exec_visual_css, html_to_pdf_bytes, now_str, stat_tiles_html, vn as _vn


def _pct(v) -> str:
    return "—" if v is None else f"{_vn(v)}%"


def _ratio(v) -> str:
    return "—" if v is None else _vn(v * 100) + "%"


def _sizes(rows, first: str):
    return [[esc(r[first]), _vn(r["requests"]), _vn(r["vms"]), _vn(r["vcpu"]), _vn(r["ramGB"]), _vn(r["diskGB"])] for r in rows]


def render_html(d: Dict[str, Any], username: str) -> str:
    generated = now_str()
    s, f, fc = d["summary"], d["fulfilment"], d["forecast"]
    t, st, n = fc["totals"], s["totals"], fc["node"]
    nodes = t["nodes"]
    level = "ok" if nodes == 0 else "warn"
    word = "AN TOÀN" if nodes == 0 else "THEO DÕI"
    money = f"{_vn(t['amount'])} đ (chưa VAT)" if t["amount"] is not None else "chưa nhập đơn giá host"
    headline = (f"Năm {d['planYear']} cần thêm {_vn(nodes)} host {n['name']} — {money}." if nodes
                else f"Năm {d['planYear']} chưa cần thêm host: nhu cầu ròng vẫn dưới mục tiêu {_vn(fc['targetPct'])}% ngưỡng và đạt N+1.")
    ov = f["overall"]
    points = [
        f"Năm {d['year']}: {_vn(st['requests'])} yêu cầu, {_vn(st['vms'])} VM mới, xin {_vn(st['vcpu'])} vCPU · {_vn(st['ramGB'])} GB RAM · "
        f"{_vn(st['diskGB'])} GB disk; thời gian xử lý trung vị {_vn(st['medianDays'], 1) if st['medianDays'] is not None else '—'} ngày.",
        (f"Dùng thật / xin (VM đã tạo, sau {ov['horizon']} ngày): vCPU {_ratio(ov['ratioVcpu'])}, RAM {_ratio(ov['ratioRamGB'])}"
         f" — {'đã' if fc['useFactor'] else 'không'} nhân hệ số vào nhu cầu năm sau." if ov["horizon"] else
         "Chưa đo được dùng thật / xin (chưa có VM tạo theo yêu cầu đủ 90 ngày): nhu cầu năm sau tính theo số xin."),
        f"Nhu cầu ròng {_vn(t['netVcpu'])} vCPU · {_vn(t['netRamGB'])} GB RAM = hiện tại {_vn(t['usedVcpu'])} · {_vn(t['usedRamGB'])} GB"
        f" + tăng tự nhiên {_vn(t['organicVcpu'])} · {_vn(t['organicRamGB'])} GB + đã biết {_vn(t['knownVcpu'])} · {_vn(t['knownRamGB'])} GB"
        f" − thu hồi {_vn(t['reclaimVcpu'])} · {_vn(t['reclaimRamGB'])} GB.",
        f"Host chuẩn {n['name']}; N+1 và mục tiêu {_vn(fc['targetPct'])}% ngưỡng overcommit sau khi bổ sung.",
    ]
    lst = "".join(f'<li class="rec-sum-item"><span class="rec-num">{i}</span><span class="rec-sum-text">{esc(p)}</span></li>'
                  for i, p in enumerate(points, 1))
    tiles = stat_tiles_html([
        {"label": "Host cần thêm", "value": _vn(nodes), "sub": n["name"], "cls": level},
        {"label": "Ngân sách", "value": _vn(t["amount"]) if t["amount"] is not None else "—", "sub": "đ, chưa VAT", "cls": "na"},
        {"label": "vCPU ròng", "value": _vn(t["netVcpu"]), "sub": f"hiện tại {_vn(t['usedVcpu'])}", "cls": "na"},
        {"label": "RAM ròng", "value": f"{_vn(t['netRamGB'])} GB", "sub": f"hiện tại {_vn(t['usedRamGB'])} GB", "cls": "na"},
        {"label": f"VM mới {d['year']}", "value": _vn(st["vms"]), "sub": f"{_vn(st['requests'])} yêu cầu", "cls": "na"}])
    clusters = _table(["Cluster", "Host", "Hiện tại vCPU / RAM", "Ròng vCPU / RAM", "% ngưỡng CPU / RAM", "Host thêm", "Sau bổ sung"],
                      [[esc(c["name"]), _vn(c["hosts"]), f"{_vn(c['usedVcpu'])} / {_vn(c['usedRamGB'])}", f"{_vn(c['netVcpu'])} / {_vn(c['netRamGB'])}",
                        f"{_pct(c['cpuPct'])} / {_pct(c['ramPct'])}", f"<b>{_vn(c['nodes'])}</b>", f"{_pct(c['cpuPctAfter'])} / {_pct(c['ramPctAfter'])}"]
                       for c in fc["clusters"]], (1, 2, 3, 4, 5, 6))
    head6 = ["Yêu cầu", "VM", "vCPU", "RAM (GB)", "Disk (GB)"]
    quarters = _table(["Quý"] + head6, _sizes(s["byQuarter"], "key"), (1, 2, 3, 4, 5))
    deps = _table(["Phòng ban"] + head6, _sizes(s["byDepartment"], "key"), (1, 2, 3, 4, 5))
    projects = _table(["Dự án"] + head6, _sizes(s["byProject"], "key"), (1, 2, 3, 4, 5))
    envs = _table(["Môi trường"] + head6, _sizes(s["byEnv"], "key"), (1, 2, 3, 4, 5))
    fulfil = _table(["Phòng ban", "VM", "Xin vCPU / RAM", "Cấp vCPU / RAM", "Dùng 90 ngày", "Dùng 180 ngày", "Dùng / xin vCPU · RAM"],
                    [[esc(x["department"]), _vn(x["vms"]), f"{_vn(x['reqVcpu'])} / {_vn(x['reqRamGB'])}", f"{_vn(x['provVcpu'])} / {_vn(x['provRamGB'])}",
                      f"{_vn(x['used90Vcpu'], 1)} / {_vn(x['used90RamGB'], 1)} ({_vn(x['vms90'])} VM)",
                      f"{_vn(x['used180Vcpu'], 1)} / {_vn(x['used180RamGB'], 1)} ({_vn(x['vms180'])} VM)",
                      f"{_ratio(x['ratioVcpu'])} · {_ratio(x['ratioRamGB'])}"] for x in f["departments"] + [{**ov, "department": "Toàn bộ"}]],
                    (1, 2, 3, 4, 5, 6))
    organic = _table(["Cluster", "Số ngày lịch sử", "Tăng quan sát vCPU / RAM", "Trừ VM theo yêu cầu", "Tăng tự nhiên / năm"],
                     [[esc(c["name"]), _vn(c["organic"]["spanDays"]),
                       f"{_vn(c['organic']['observedVcpu'])} / {_vn(c['organic']['observedRamGB'])}",
                       f"{_vn(c['organic']['fromRequestsVcpu'])} / {_vn(c['organic']['fromRequestsRamGB'])}",
                       f"{_vn(c['organicVcpu'])} / {_vn(c['organicRamGB'])}"] for c in fc["clusters"]], (1, 2, 3, 4))
    src = {"approved": "Yêu cầu đã duyệt, chưa cấp", "manual": "Dự án năm sau"}
    known = _table(["Nhu cầu", "Nguồn", "Phòng ban", "Cluster", "vCPU × hệ số", "RAM × hệ số (GB)"],
                   [[esc(k["label"]), esc(src.get(k["source"], k["source"])), esc(k.get("department") or "—"), esc(k["cluster"] or "—"),
                     f"{_vn(k.get('vcpu'))} × {_vn(k['factorVcpu'], 2)} = {_vn(k['vcpuNet'], 1)}",
                     f"{_vn(k.get('ramGB'))} × {_vn(k['factorRamGB'], 2)} = {_vn(k['ramGBNet'], 1)}"] for k in fc["known"]], (4, 5))
    reclaims = _table(["VM", "Quyết định", "Hệ thống", "Cluster", "vCPU", "RAM (GB)"],
                      [[esc(x["label"]), "Thu hồi" if x["decision"] == "decommission" else "Giảm cấu hình", esc(x.get("systemCode") or "—"),
                        esc(x["cluster"]), _vn(x["vcpu"]), _vn(x["ramGB"])] for x in fc["reclaims"]], (4, 5))
    empty = '<div class="small-note">Không có.</div>'
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><style>{base_css('Nhu cầu hạ tầng — tổng kết & kế hoạch', generated)}{exec_visual_css()}</style></head>
<body>
  <div class="cover-header"><div>
    <div class="eyebrow">Infrastructure Capacity Ops · Nhu cầu hạ tầng · {esc(generated.split(' ')[-1])}</div>
    <div class="cover-title" style="margin-bottom:6px;">Tổng kết nhu cầu {d['year']} &amp; kế hoạch hạ tầng ảo hoá {d['planYear']}</div>
    <div class="meta-row"><span>Host chuẩn: <b>{esc(n['name'])}</b></span><span>Mục tiêu: <b>{_vn(fc['targetPct'])}% ngưỡng</b></span>
      <span>Hệ số dùng thật: <b>{'Bật' if fc['useFactor'] else 'Tắt'}</b></span><span>Lập bởi: <b>{esc(username)}</b></span></div>
  </div></div>
  <div class="exec-brief {level}">
    <div class="exec-brief-head"><span class="status-pill {level}">{esc(word)}</span><span class="exec-brief-title">Kết luận</span></div>
    <div class="exec-brief-headline">{esc(headline)}</div>
    <ol class="rec-sum">{lst}</ol>
    <div class="stat-row" style="margin-bottom:0;">{tiles}</div>
  </div>
  <div class="section"><div class="section-title">Số host cần thêm theo cluster</div>{clusters or empty}</div>
  <div class="section breakable" style="page-break-before:always;">
    <div class="section-title">Phụ lục A. Tổng kết nhu cầu năm {d['year']}</div>
    <div class="section-sub">Yêu cầu đọc tự động hoặc đã duyệt: VM cấp mới và phần thay đổi của nâng cấp (To-be − As-is).</div>
    {quarters}{deps}{projects}{envs}</div>
  <div class="section breakable"><div class="section-title">Phụ lục B. Xin → cấp → dùng thật</div>
    <div class="section-sub">VM tạo theo yêu cầu (khớp chắc). Dùng = cấu hình × % sử dụng trung bình 7 ngày từ ngày 90 / 180.</div>{fulfil}</div>
  <div class="section breakable"><div class="section-title">Phụ lục C. Tăng tự nhiên theo cluster</div>
    <div class="section-sub">Lịch sử 12 tháng, trừ phần VM tạo theo yêu cầu (đã tính ở nhu cầu đã biết) để không đếm đôi.</div>{organic or empty}</div>
  <div class="section breakable"><div class="section-title">Phụ lục D. Nhu cầu đã biết</div>{known or empty}</div>
  <div class="section breakable"><div class="section-title">Phụ lục E. Thu hồi đã cam kết (rà soát owner)</div>{reclaims or empty}</div>
</body></html>"""


def build_pdf(d: Dict[str, Any], username: str) -> bytes:
    return html_to_pdf_bytes(render_html(d, username))
