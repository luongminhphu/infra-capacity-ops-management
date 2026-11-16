"""Host-sizing PDF ("Đề xuất cấu hình máy chủ") — shared building blocks and the entry point.

Both report variants are rendered by report_sizing_full.render_full_html:
detail='full' → báo cáo tư vấn đầy đủ, detail='brief' → báo cáo tư vấn rút gọn (same page format,
compact content). This module keeps the summary / decision boxes and the existing-cluster blocks
used by that template."""
from .pdf_kit import status_pill as _status_mark  # one status mark for every report (MASTER.md §3)
from typing import Any, Dict, List, Optional

from . import platforms
from .pdf_kit import COLORS, STATUS_COLOR, _stat_tiles_html, esc, fmt_int, fmt_num, fmt_pct, html_to_pdf_bytes

PRIORITY_CSS = {"cao": "priority-cao", "tb": "priority-tb", "thap": "priority-thap", "info": "priority-info"}
PRIORITY_TXT = {"cao": "Quyết định chính", "tb": "Kèm theo", "thap": "Tuỳ chọn", "info": "Thông tin"}


import re as _re
from .shared.text import vn_g

# Decision lines of the summary boxes are written for an internal approval memo
# ("Duyệt ngân sách …"). In the full consulting report the author is the SI /
# solution provider talking to the customer, so the same lines are re-voiced.
_SI_DECISIONS = [
    (r"^Duyệt ngân sách (.+)$", r"Phê duyệt ngân sách \1"),
    (r"^Duyệt chủ trương mua sắm \(chưa có báo giá\)$", "Phê duyệt chủ trương đầu tư; báo giá chi tiết gửi kèm"),
    (r"^Duyệt cấp dung lượng lưu trữ$", "Xác nhận dung lượng lưu trữ cấp cho hệ thống"),
    (r"^Thêm host hoặc hạ overcommit mục tiêu$", "Bổ sung host hoặc giảm tỷ lệ chia sẻ tài nguyên"),
    (r"^Cân nhắc dùng chung thay vì mua mới$", "Có thể triển khai trên hạ tầng sẵn có nếu ưu tiên tiết kiệm chi phí"),
    (r"^Bắt buộc bổ sung host$", "Cần đầu tư host mới theo đề xuất"),
    (r"^Duyệt chủ trương dùng chung hạ tầng, thống nhất với System Owner cluster (.+)$", r"Phê duyệt dùng chung hạ tầng; phối hợp với đơn vị quản lý cụm \1"),
    (r"^Chỉ duyệt dùng chung kèm bổ sung host; System Owner cluster (.+) xác nhận$", r"Chỉ dùng chung khi bổ sung host; phối hợp với đơn vị quản lý cụm \1"),
    (r"^Cân nhắc chuyển dự án sang cluster (.+)$", r"Khuyến nghị triển khai trên cụm \1"),
    (r"^Duyệt thêm 1 host dự phòng cho cluster$", "Phê duyệt bổ sung 1 host dự phòng"),
    (r"^Không cần host dự phòng thêm$", "Không cần bổ sung host dự phòng"),
    (r"^Theo dõi; đưa mở rộng vào kỳ quy hoạch tài nguyên$", "Theo dõi và đưa mở rộng vào kỳ quy hoạch tiếp theo"),
    (r"^Lập kế hoạch mở rộng ngay$", "Cần lập kế hoạch mở rộng ngay"),
    (r"^Không phải chi nếu dùng chung$", "Không phát sinh chi phí nếu dùng chung hạ tầng"),
    (r"^Phương án dự phòng nếu không dùng chung được$", "Phương án thay thế nếu không dùng chung được"),
    (r"^Đạt với cấu hình đề xuất$", "Đã gồm trong cấu hình đề xuất"),
    (r"^Cần tăng đĩa cục bộ mỗi host$", "Cần bổ sung ổ đĩa cho mỗi host"),
    (r"^Đạt dự phòng N\+(\d+)$", r"Đạt dự phòng N+\1"),
]


def _si_voice(text: str) -> str:
    for pat, repl in _SI_DECISIONS:
        new = _re.sub(pat, repl, text)
        if new != text:
            return new
    return text


def _gb(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{fmt_num(v / 1024, 1)} TiB" if v >= 1024 else f"{fmt_int(v)} GB"


def _money(v: Optional[float], currency: str) -> str:
    if v is None:
        return "—"
    # English grouping here; the report converts every number to Vietnamese format (1.950.000.000) in one pass.
    s = f"{int(round(v)):,}"
    return f"{s} {currency}".strip()


def _pill(text: str, cls: str) -> str:
    return _status_mark({"text": text, "cls": cls})


def _summary_box(result: Dict[str, Any], best: Optional[Dict[str, Any]], existing: List[Dict[str, Any]],
                 currency: str, si: bool = False) -> str:
    d, p = result["demand"], result["params"]
    items: List[Dict[str, str]] = []
    status_cls, status_label = "ok", "ĐỀ XUẤT"
    if best:
        prof = best["profile"]
        budget = _money(best["budget"], currency) if best["budget"] is not None else None
        items.append({"pri": "cao",
                      "title": f"Mua {best['hostsTotal']} host {prof['name']} ({prof['cores']} cores · {_gb(prof['ramGB'])} RAM/host)",
                      "decision": f"Duyệt ngân sách {budget}" if budget else "Duyệt chủ trương mua sắm (chưa có báo giá)",
                      "time": "Theo tiến độ dự án"})
        if p["storage_mode"] == "san":
            items.append({"pri": "tb", "title": f"Cấp {_gb(d['storageNeededGB'])} trên SAN cho dự án (đã gồm +{vn_g(p['storage_overhead_pct'])}% overhead)",
                          "decision": "Duyệt cấp dung lượng lưu trữ", "time": "Cùng đợt lắp host"})
        else:
            st = best["storage"]
            items.append({"pri": "tb", "title": f"{platforms.get(p)['hci']}: ~{_gb(st.get('perHostNeededGB'))} thô / host (tổng {_gb(d['storageNeededGB'])}, hệ số bảo vệ ×{vn_g(p['storage_protection_factor'])})",
                          "decision": "Đạt với cấu hình đề xuất" if st.get("ok") else "Cần tăng đĩa cục bộ mỗi host", "time": "Trong cấu hình host"})
            if st.get("ok") is False:
                status_cls = "warn"
        f = best["failure"]
        if best["redundancy"] and f["ok"]:
            items.append({"pri": "info", "title": f"Mất {best['redundancy']} host vẫn chạy được: CPU {fmt_pct(f['cpuPct'], 0)} · RAM {fmt_pct(f['ramPct'], 0)}"
                                   + (" — sát mức tối đa" if f.get("atLimit") else ""),
                          "decision": f"Đạt dự phòng N+{best['redundancy']}" + (" (sát ngưỡng)" if f.get("atLimit") else ""), "time": ""})
        elif best["redundancy"]:
            status_cls = "warn"
            items.append({"pri": "cao", "title": f"Chưa đạt dự phòng N+{best['redundancy']}", "decision": "Thêm host hoặc hạ overcommit mục tiêu", "time": ""})
    if existing:
        can = [e for e in existing if e.get("cls") == "ok" and e.get("n1Ok", True)]
        if can:
            items.append({"pri": "thap",
                          "title": f"Phương án thay thế: cluster {', '.join(e['name'] for e in can[:3])} hiện hữu hấp thụ được toàn bộ nhu cầu (<80% ngưỡng, giữ N+1)",
                          "decision": "Cân nhắc dùng chung thay vì mua mới", "time": ""})
        else:
            items.append({"pri": "info", "title": "Không cluster hiện hữu nào hấp thụ được toàn bộ nhu cầu mà vẫn an toàn",
                          "decision": "Bắt buộc bổ sung host", "time": ""})

    if si:
        for it in items:
            it["decision"] = _si_voice(it["decision"])
            if it["title"].startswith("Mua "):
                it["title"] = "Trang bị " + it["title"][4:]
    li = ""
    for i, it in enumerate(items, 1):
        time_html = f' <span class="rec-sum-time">· {esc(it["time"])}</span>' if it["time"] else ""
        li += (f'<li class="rec-sum-item"><span class="rec-num">{i}</span>'
               f'<span class="priority-pill {PRIORITY_CSS[it["pri"]]}">{PRIORITY_TXT[it["pri"]]}</span>'
               f'<span class="rec-sum-text"><b>{esc(it["title"])}</b> → {esc(it["decision"])}{time_html}</span></li>')

    stats = [
        {"label": "VM yêu cầu", "value": fmt_int(d["vmCount"]), "sub": f"trung bình {fmt_num(d['avgVcpu'], 1)} vCPU · {_gb(d['avgRamGB'])} / VM", "cls": "na"},
        {"label": "vCPU", "value": f"{fmt_int(d['vcpu'])} → {fmt_int(d['vcpuPlanned'])}", "sub": f"sau +{vn_g(p['growth_pct'])}% tăng trưởng", "cls": "na"},
        {"label": "RAM (GB)", "value": f"{fmt_int(d['ramGB'])} → {fmt_int(d['ramPlannedGB'])}", "sub": f"sau tăng trưởng · tỷ lệ chia sẻ {vn_g(p['ram_ratio'])} : 1", "cls": "na"},
        {"label": "Host đề xuất", "value": f"{best['hostsTotal']} host" if best else "—",
         "sub": (f"{best['profile']['name']} · N+{best['redundancy']}" if best else ""), "cls": "ok" if best else "na"},
        {"label": "SAN cần cấp" if p["storage_mode"] == "san" else f"{platforms.get(p)['hci']} (thô)", "value": _gb(d["storageNeededGB"]),
         "sub": f"từ {_gb(d['diskGB'])} dữ liệu", "cls": "na"},
    ]
    if best and best["failure"].get("atLimit"):
        status_cls = "warn"
    fg, bg = STATUS_COLOR[status_cls]
    headline = result["recommendation"]["headline"] if best else "Chưa có VM nào trong danh sách yêu cầu."
    return f"""
    <div class="exec-brief {status_cls}">
      <div class="exec-brief-head">
        {_status_mark({"text": status_label, "cls": status_cls})}
        <span class="exec-brief-title">{'Giải pháp đề xuất &amp; hạng mục cần quyết định' if si else 'Tổng kết — đề xuất &amp; quyết định đề nghị'}</span>
      </div>
      <div class="exec-brief-headline">{esc(headline)}</div>
      <ol class="rec-sum">{li}</ol>
      <div class="stat-row" style="margin-bottom:0;">{_stat_tiles_html(stats)}</div>
    </div>"""


def _feasible(e: Dict[str, Any]) -> bool:
    return (e.get("cls") == "ok") and (e.get("n1Ok") is not False)


def _pick_target(existing: List[Dict[str, Any]], target_cluster: Optional[str]) -> Optional[Dict[str, Any]]:
    """Target cluster for the reuse scenario: the one named by the user, else
    the feasible cluster with the most headroom left, else None."""
    if not existing:
        return None
    if target_cluster:
        for e in existing:
            if e["name"] == target_cluster:
                return e
    feasible = [e for e in existing if _feasible(e)]
    if not feasible:
        return None
    return min(feasible, key=lambda e: max(e.get("cpuAfter") or 0, e.get("ramAfter") or 0))


def _existing_table(existing: List[Dict[str, Any]], policy_txt: str, highlight: Optional[str] = None) -> str:
    rows = ""
    show_spare = any(e.get("extraAvgVms") is not None for e in existing)
    for e in existing:
        cls = e.get("cls") or "na"
        label = {"ok": "Hấp thụ được", "warn": "Sát ngưỡng", "crit": "Vượt ngưỡng"}.get(cls, "—")
        n1 = e.get("n1CpuPct")
        n1_txt = (f"{fmt_pct(e.get('n1CpuPct'), 0)} / {fmt_pct(e.get('n1RamPct'), 0)} " + (_pill("Đạt", "ok") if e.get("n1Ok") else _pill("Không", "crit"))) if n1 is not None else "—"
        is_target = bool(highlight) and e["name"] == highlight
        style = f' style="background:{COLORS["ok_bg" if _feasible(e) else "warn_bg"]};"' if is_target else ""
        tag = (" " + (_pill("Tiếp nhận", "ok") if _feasible(e) else _pill("Đang xét", "warn"))) if is_target else ""
        spare_td = ""
        if show_spare:
            sp = e.get("extraAvgVms")
            spare_td = f'<td style="text-align:center;">{"—" if sp is None else "~" + fmt_int(sp)}</td>'
        rows += (f'<tr{style}><td><b>{esc(e["name"])}</b> <span style="color:{COLORS["muted"]};">· {fmt_int(e.get("hostCount"))} host</span>{tag}</td>'
                 f'<td>{fmt_pct(e.get("cpuBefore"), 0)} → <b>{fmt_pct(e.get("cpuAfter"), 0)}</b></td>'
                 f'<td>{fmt_pct(e.get("ramBefore"), 0)} → <b>{fmt_pct(e.get("ramAfter"), 0)}</b></td>'
                 f'<td style="white-space:nowrap;">{n1_txt}</td>{spare_td}<td>{_pill(label, cls)}</td></tr>')
    return f"""<table>
      <thead><tr><th>Cluster hiện hữu</th><th>CPU % ngưỡng (trước → sau)</th><th>RAM % ngưỡng (trước → sau)</th><th>Khi mất 1 host (CPU / RAM)</th>{'<th>Dư địa còn lại ~VM</th>' if show_spare else ''}<th>Kết luận</th></tr></thead>
      <tbody>{rows}</tbody></table>
      <div class="small-note">Giả định đưa toàn bộ nhu cầu (sau tăng trưởng) vào từng cluster; % tính theo ngưỡng overcommit của chính sách hiện hành{(' (' + esc(policy_txt) + ')') if policy_txt else ''}.{' Dư địa còn lại: số VM cỡ trung bình của dự án còn thêm được tới 80% ngưỡng.' if show_spare else ''}</div>"""


def _reuse_summary_box(result: Dict[str, Any], best: Optional[Dict[str, Any]], existing: List[Dict[str, Any]],
                       target: Optional[Dict[str, Any]], include_new: bool, currency: str, project: str, si: bool = False) -> str:
    d, p = result["demand"], result["params"]
    items: List[Dict[str, str]] = []
    n_new = best["hostsTotal"] if best else 0
    budget = _money(best["budget"], currency) if (best and best["budget"] is not None) else None
    if target and _feasible(target):
        status_cls, status_label = "ok", "TẬN DỤNG HIỆN HỮU"
        headline = (f"Triển khai {fmt_int(d['vmCount'])} VM của {project} trên cluster {target['name']} hiện hữu "
                    f"({fmt_int(target.get('hostCount'))} host) — không cần mua host mới; sau tiếp nhận CPU {fmt_pct(target.get('cpuAfter'), 0)} · "
                    f"RAM {fmt_pct(target.get('ramAfter'), 0)} ngưỡng, vẫn giữ N+1.")
    elif target and target.get("cls") == "crit":
        status_cls, status_label = "crit", "KHÔNG AN TOÀN"
        headline = (f"Cluster {target['name']} không tiếp nhận được an toàn {fmt_int(d['vmCount'])} VM — "
                    f"CPU {fmt_pct(target.get('cpuAfter'), 0)} · RAM {fmt_pct(target.get('ramAfter'), 0)} ngưỡng sau tiếp nhận"
                    + (", mất N+1" if target.get("n1Ok") is False else "") + " — "
                    + (f"cần bổ sung host cho cluster hoặc chọn phương án B ({n_new} host mới)." if include_new and best else "cần bổ sung host cho cluster trước khi triển khai."))
    elif target:
        status_cls, status_label = "warn", "CẦN CÂN NHẮC"
        why = "sát ngưỡng" if target.get("cls") == "warn" else "không giữ được N+1"
        headline = (f"Cluster {target['name']} tiếp nhận được {fmt_int(d['vmCount'])} VM nhưng {why} "
                    f"(CPU {fmt_pct(target.get('cpuAfter'), 0)} · RAM {fmt_pct(target.get('ramAfter'), 0)} ngưỡng sau tiếp nhận) — "
                    + (f"nên kết hợp bổ sung host hoặc chọn phương án B ({n_new} host mới)." if include_new and best else "cần bổ sung host dự phòng."))
    else:
        status_cls, status_label = "crit", "KHÔNG KHẢ THI"
        headline = (f"Không cluster hiện hữu nào tiếp nhận được toàn bộ {fmt_int(d['vmCount'])} VM mà vẫn an toàn — "
                    + (f"cần trang bị mới {n_new} host (phương án B)." if include_new and best else "cần trang bị mới host."))

    if target:
        items.append({"pri": "cao",
                      "title": f"Triển khai trên {target['name']}: CPU {fmt_pct(target.get('cpuBefore'), 0)} → {fmt_pct(target.get('cpuAfter'), 0)}, RAM {fmt_pct(target.get('ramBefore'), 0)} → {fmt_pct(target.get('ramAfter'), 0)} ngưỡng",
                      "decision": (f"Duyệt chủ trương dùng chung hạ tầng, thống nhất với System Owner cluster {target['name']}" if _feasible(target)
                                   else f"Chỉ duyệt dùng chung kèm bổ sung host; System Owner cluster {target['name']} xác nhận"),
                      "time": "Theo tiến độ dự án"})
        if not _feasible(target):
            alt = _pick_target(existing, None)
            if alt and alt["name"] != target["name"]:
                items.append({"pri": "cao", "title": f"Thay thế: {alt['name']} hấp thụ được (CPU {fmt_pct(alt.get('cpuAfter'), 0)} · RAM {fmt_pct(alt.get('ramAfter'), 0)} ngưỡng, N+1 đạt)",
                              "decision": f"Cân nhắc chuyển dự án sang cluster {alt['name']}", "time": ""})
        if target.get("n1Ok") is False:
            items.append({"pri": "cao", "title": f"Sau tiếp nhận, {target['name']} không chịu được mất 1 host (CPU {fmt_pct(target.get('n1CpuPct'), 0)} · RAM {fmt_pct(target.get('n1RamPct'), 0)})",
                          "decision": "Duyệt thêm 1 host dự phòng cho cluster", "time": "Trước go-live"})
        elif target.get("n1Ok"):
            items.append({"pri": "info", "title": f"Mất 1 host vẫn chạy được: CPU {fmt_pct(target.get('n1CpuPct'), 0)} · RAM {fmt_pct(target.get('n1RamPct'), 0)} ngưỡng",
                          "decision": "Không cần host dự phòng thêm", "time": ""})
    if p["storage_mode"] == "san":
        items.append({"pri": "tb", "title": f"Cấp {_gb(d['storageNeededGB'])} trên SAN cho dự án (đã gồm +{vn_g(p['storage_overhead_pct'])}% overhead)",
                      "decision": "Duyệt cấp dung lượng lưu trữ", "time": "Trước triển khai"})
    if target and target.get("extraAvgVms") is not None:
        items.append({"pri": "thap", "title": f"Dư địa còn lại của {target['name']} sau tiếp nhận: ~{fmt_int(target['extraAvgVms'])} VM cỡ dự án (tới 80% ngưỡng)",
                      "decision": "Theo dõi; đưa mở rộng vào kỳ quy hoạch tài nguyên" if target["extraAvgVms"] > 0 else "Lập kế hoạch mở rộng ngay", "time": ""})
    if include_new and best:
        items.append({"pri": "thap", "title": f"Phương án B — trang bị mới {n_new} host {best['profile']['name']}" + (f", ngân sách {budget}" if budget else ""),
                      "decision": "Không phải chi nếu dùng chung" if (target and _feasible(target)) else "Phương án dự phòng nếu không dùng chung được", "time": ""})

    if si:
        for it in items:
            it["decision"] = _si_voice(it["decision"])
            if it["title"].startswith("Mua "):
                it["title"] = "Trang bị " + it["title"][4:]
    li = ""
    for i, it in enumerate(items, 1):
        time_html = f' <span class="rec-sum-time">· {esc(it["time"])}</span>' if it["time"] else ""
        li += (f'<li class="rec-sum-item"><span class="rec-num">{i}</span>'
               f'<span class="priority-pill {PRIORITY_CSS[it["pri"]]}">{PRIORITY_TXT[it["pri"]]}</span>'
               f'<span class="rec-sum-text"><b>{esc(it["title"])}</b> → {esc(it["decision"])}{time_html}</span></li>')

    tgt_cls = "ok" if (target and _feasible(target)) else ("crit" if (not target or target.get("cls") == "crit") else "warn")
    stats = [
        {"label": "VM yêu cầu", "value": fmt_int(d["vmCount"]), "sub": f"trung bình {fmt_num(d['avgVcpu'], 1)} vCPU · {_gb(d['avgRamGB'])} / VM", "cls": "na"},
        {"label": "vCPU", "value": f"{fmt_int(d['vcpu'])} → {fmt_int(d['vcpuPlanned'])}", "sub": f"sau +{vn_g(p['growth_pct'])}% tăng trưởng", "cls": "na"},
        {"label": "RAM (GB)", "value": f"{fmt_int(d['ramGB'])} → {fmt_int(d['ramPlannedGB'])}", "sub": "sau tăng trưởng", "cls": "na"},
        {"label": "Cluster tiếp nhận", "value": target["name"] if target else "Không có",
         "sub": (f"{fmt_int(target.get('hostCount'))} host · N+1 {'Đạt' if target.get('n1Ok') else 'Không' if target.get('n1Ok') is False else '—'}" if target else "cần trang bị mới"), "cls": tgt_cls},
        ({"label": "Chi phí tránh được", "value": budget, "sub": f"= {n_new} host {best['profile']['name']}", "cls": "ok"}
         if (budget and target and _feasible(target)) else
         {"label": "SAN cần cấp" if p["storage_mode"] == "san" else "Lưu trữ cục bộ", "value": _gb(d["storageNeededGB"]), "sub": f"từ {_gb(d['diskGB'])} yêu cầu", "cls": "na"}),
    ]
    fg, bg = STATUS_COLOR[status_cls]
    return f"""
    <div class="exec-brief {status_cls}">
      <div class="exec-brief-head">
        {_status_mark({"text": status_label, "cls": status_cls})}
        <span class="exec-brief-title">{'Giải pháp đề xuất &amp; hạng mục cần quyết định' if si else 'Tổng kết — đề xuất &amp; quyết định đề nghị'}</span>
      </div>
      <div class="exec-brief-headline">{esc(headline)}</div>
      <ol class="rec-sum">{li}</ol>
      <div class="stat-row" style="margin-bottom:0;">{_stat_tiles_html(stats)}</div>
    </div>"""


def _target_detail(target: Dict[str, Any], d: Dict[str, Any]) -> str:
    parts = [f"<b>Cluster {esc(target['name'])}</b>: {fmt_int(target.get('hostCount'))} host"]
    if target.get("totalCores") is not None:
        parts[-1] += f" · {fmt_int(target['totalCores'])} cores / {_gb(target.get('totalRamGB'))} RAM vật lý"
    parts[-1] += "."
    if target.get("usedVcpu") is not None:
        parts.append(f"Đang cấp phát {fmt_int(target['usedVcpu'])} vCPU / {_gb(target.get('usedRamGB'))} cho {fmt_int(target.get('poweredOnCount'))} VM bật; "
                     f"dự án thêm {fmt_int(d['vcpuPlanned'])} vCPU / {_gb(d['ramPlannedGB'])} (đã gồm tăng trưởng).")
    if target.get("cpuOcAfter") is not None:
        parts.append(f"Overcommit sau tiếp nhận: CPU {fmt_num(target['cpuOcAfter'], 2, 'x')} · RAM {fmt_num(target.get('ramOcAfter'), 2, 'x')} "
                     f"(= {fmt_pct(target.get('cpuAfter'), 0)} · {fmt_pct(target.get('ramAfter'), 0)} ngưỡng chính sách).")
    if target.get("n1Ok") is not None:
        parts.append(("Mất 1 host lớn nhất vẫn trong ngưỡng" if target["n1Ok"] else "Mất 1 host lớn nhất sẽ vượt ngưỡng")
                     + f" (CPU {fmt_pct(target.get('n1CpuPct'), 0)} · RAM {fmt_pct(target.get('n1RamPct'), 0)}).")
    return f'<div class="insight-box" style="margin-top:8px;">{" ".join(parts)}</div>'


def render_sizing_html(*args, detail: str = "full", **kwargs) -> str:
    """detail='full' → báo cáo tư vấn đầy đủ · detail='brief' → báo cáo tư vấn rút gọn (same template, compact)."""
    from .report_sizing_full import render_full_html
    return render_full_html(*args, compact=(detail == "brief"), **kwargs)


def build_sizing_pdf(*args, **kwargs) -> bytes:
    return html_to_pdf_bytes(render_sizing_html(*args, **kwargs))
