"""PDF renderer for the host-sizing proposal ("Đề xuất cấu hình máy chủ"):
management layout shared with the Operations report — summary box with the
decision requested first, then the candidate comparison table, then the
assumptions, with the requested VM list and the existing-cluster absorption
check as appendices. Meant to be attached to a procurement proposal."""
from typing import Any, Dict, List, Optional

from . import sizing
from .report_pdf import (COLORS, STATUS_COLOR, _base_css, _exec_visual_css, _now_str, _stat_tiles_html, esc, fmt_int,
                         fmt_num, fmt_pct, html_to_pdf_bytes)
from .report_planning import _planning_css
from .report_pdf import svg_bar_chart, svg_donut, svg_gauge, svg_radar_chart
from .report_sizing_charts import (SEG_COLORS, SERIES_COLORS, money_short, svg_cost_bars, svg_dual_hbars, svg_lines,
                                   svg_projection, svg_stack_bars, svg_waterfall)

PRIORITY_CSS = {"cao": "priority-cao", "tb": "priority-tb", "thap": "priority-thap", "info": "priority-info"}
PRIORITY_TXT = {"cao": "Quyết định chính", "tb": "Kèm theo", "thap": "Tuỳ chọn", "info": "Thông tin"}


def _gb(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{fmt_num(v / 1024, 1)} TB" if v >= 1024 else f"{fmt_int(v)} GB"


def _money(v: Optional[float], currency: str) -> str:
    if v is None:
        return "—"
    s = f"{int(round(v)):,}".replace(",", ".")
    return f"{s} {currency}".strip()


def _pill(text: str, cls: str) -> str:
    fg, bg = STATUS_COLOR.get(cls, STATUS_COLOR["na"])
    return f'<span class="pill" style="color:{COLORS[fg]};background:{COLORS[bg]};border-color:{COLORS[fg]}55;">{esc(text)}</span>'


def _summary_box(result: Dict[str, Any], best: Optional[Dict[str, Any]], existing: List[Dict[str, Any]],
                 currency: str) -> str:
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
            items.append({"pri": "tb", "title": f"Cấp {_gb(d['storageNeededGB'])} trên SAN cho dự án (đã gồm +{p['storage_overhead_pct']:g}% overhead)",
                          "decision": "Duyệt cấp dung lượng lưu trữ", "time": "Cùng đợt lắp host"})
        else:
            st = best["storage"]
            items.append({"pri": "tb", "title": f"Đĩa cục bộ ~{_gb(st.get('perHostNeededGB'))}/host (tổng {_gb(d['storageNeededGB'])}, hệ số bảo vệ ×{p['storage_protection_factor']:g})",
                          "decision": "Đạt với cấu hình đề xuất" if st.get("ok") else "Cần tăng đĩa cục bộ mỗi host", "time": "Trong cấu hình host"})
            if st.get("ok") is False:
                status_cls = "warn"
        f = best["failure"]
        if best["redundancy"] and f["ok"]:
            items.append({"pri": "info", "title": f"Mất {best['redundancy']} host vẫn chạy được: CPU {fmt_pct(f['cpuPct'], 0)} · RAM {fmt_pct(f['ramPct'], 0)} năng lực mục tiêu",
                          "decision": f"Đạt dự phòng N+{best['redundancy']}", "time": ""})
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

    li = ""
    for i, it in enumerate(items, 1):
        time_html = f' <span class="rec-sum-time">· {esc(it["time"])}</span>' if it["time"] else ""
        li += (f'<li class="rec-sum-item"><span class="rec-num">{i}</span>'
               f'<span class="priority-pill {PRIORITY_CSS[it["pri"]]}">{PRIORITY_TXT[it["pri"]]}</span>'
               f'<span class="rec-sum-text"><b>{esc(it["title"])}</b> → {esc(it["decision"])}{time_html}</span></li>')

    stats = [
        {"label": "VM yêu cầu", "value": fmt_int(d["vmCount"]), "sub": f"TB {fmt_num(d['avgVcpu'], 1)} vCPU · {_gb(d['avgRamGB'])} / VM", "cls": "na"},
        {"label": "vCPU", "value": f"{fmt_int(d['vcpu'])} → {fmt_int(d['vcpuPlanned'])}", "sub": f"sau +{p['growth_pct']:g}% tăng trưởng", "cls": "na"},
        {"label": "RAM (GB)", "value": f"{fmt_int(d['ramGB'])} → {fmt_int(d['ramPlannedGB'])}", "sub": f"sau tăng trưởng · overcommit {p['ram_ratio']:g}x", "cls": "na"},
        {"label": "Host đề xuất", "value": f"{best['hostsTotal']} host" if best else "—",
         "sub": (f"{best['profile']['name']} · N+{best['redundancy']}" if best else ""), "cls": "ok" if best else "na"},
        {"label": "SAN cần cấp" if p["storage_mode"] == "san" else "Lưu trữ cục bộ", "value": _gb(d["storageNeededGB"]),
         "sub": f"từ {_gb(d['diskGB'])} yêu cầu", "cls": "na"},
    ]
    fg, bg = STATUS_COLOR[status_cls]
    headline = result["recommendation"]["headline"] if best else "Chưa có VM nào trong danh sách yêu cầu."
    return f"""
    <div class="exec-brief {status_cls}">
      <div class="exec-brief-head">
        <span class="pill" style="color:{COLORS[fg]};background:{COLORS[bg]};border-color:{COLORS[fg]}55;font-size:9px;">{status_label}</span>
        <span class="exec-brief-title">Tổng kết — đề xuất &amp; quyết định đề nghị</span>
      </div>
      <div class="exec-brief-headline">{esc(headline)}</div>
      <ol class="rec-sum">{li}</ol>
      <div class="stat-row" style="margin-bottom:0;">{_stat_tiles_html(stats)}</div>
    </div>"""


def _comparison_table(result: Dict[str, Any], currency: str) -> str:
    rows = result["results"]
    local = result["params"]["storage_mode"] == "local"
    any_price = any(r["budget"] is not None for r in rows)
    body = ""
    for r in sorted(rows, key=lambda x: x["rank"] or 99):
        pr = r["profile"]
        f = r["failure"]
        fail_txt = f"{fmt_pct(f['cpuPct'], 0)} / {fmt_pct(f['ramPct'], 0)}" if f["hostsLeft"] else "—"
        fail_pill = _pill("N", "na") if r["redundancy"] == 0 else (_pill(f"Đạt N+{r['redundancy']}", "ok") if f["ok"] else _pill("Chưa đạt", "crit"))
        style = f' style="background:{COLORS["ok_bg"]};"' if r["recommended"] else ""
        tag = _pill("Đề xuất", "ok") if r["recommended"] else f'<span style="color:{COLORS["muted"]};">#{r["rank"]}</span>'
        storage_td = ""
        if local:
            st = r["storage"]
            storage_td = f"<td>{_gb(st.get('perHostNeededGB'))}/host {'' if st.get('ok') else _pill('thiếu đĩa', 'crit')}</td>"
        price_td = f"<td style=\"text-align:right;white-space:nowrap;\">{_money(r['budget'], currency)}</td>" if any_price else ""
        body += (f'<tr{style}><td style="white-space:nowrap;"><b>{esc(pr["name"])}</b><div style="color:{COLORS["muted"]};font-size:7.6px;">{pr["sockets"]}×{pr["coresPerSocket"]}C = {pr["cores"]} cores · {_gb(pr["ramGB"])} RAM</div></td>'
                 f'<td style="text-align:center;">{r["hostsForCpu"]} / {r["hostsForRam"]}</td>'
                 f'<td style="text-align:center;">+{r["redundancy"]}</td>'
                 f'<td style="text-align:center;font-size:11px;font-weight:700;">{r["hostsTotal"]}</td>'
                 f'<td>{esc(r["binding"])}</td>'
                 f'<td style="white-space:nowrap;">{fmt_int(r["totals"]["cores"])} / {_gb(r["totals"]["ramGB"])}</td>'
                 f'<td>{fmt_num(r["after"]["cpuOvercommit"], 2, "x")} / {fmt_num(r["after"]["ramOvercommit"], 2, "x")}</td>'
                 f'<td style="white-space:nowrap;">{fail_txt} {fail_pill}</td>'
                 f'<td style="text-align:center;">{"—" if r["spare"]["extraAvgVms"] is None else "~" + fmt_int(r["spare"]["extraAvgVms"])}</td>'
                 f'{storage_td}{price_td}<td>{tag}</td></tr>')
    return f"""<table class="decision-table">
      <thead><tr><th>Cấu hình host</th><th>Host CPU / RAM</th><th>Dự phòng</th><th>Tổng host</th><th>Ràng buộc</th>
      <th>Tổng cores / RAM</th><th>Overcommit sau CPU / RAM</th><th>Mất host: CPU / RAM</th><th>Thêm ~VM</th>
      {'<th>Đĩa cục bộ</th>' if local else ''}{'<th style="text-align:right;">Ngân sách ước tính</th>' if any_price else ''}<th></th></tr></thead>
      <tbody>{body}</tbody></table>"""


def _assumptions_html(result: Dict[str, Any]) -> str:
    p = result["params"]
    lines = "".join(f"<div>• {esc(x)}</div>" for x in result["assumptions"])
    return f"""<div class="chart-card soft" style="font-size:8.6px;line-height:1.55;">
      <div class="chart-card-title">Giả định sizing</div>
      {lines}
      <div class="small-note" style="margin-top:4px;">Cách tính: số host = max(host theo CPU, host theo RAM) + dự phòng N+{p['redundancy']}, tối thiểu {p['min_hosts']} host.
      Host theo CPU = vCPU yêu cầu (sau tăng trưởng) ÷ [(cores − {p['reserve_cores']:g}) × HT {p['ht_factor']:g} × {p['cpu_ratio']:g}x × {p['max_util_pct']:g}%];
      host theo RAM = RAM yêu cầu ÷ [(RAM host − {p['reserve_ram_gb']:g} GB) × {p['ram_ratio']:g}x × {p['max_util_pct']:g}%].
      Phương án đề xuất: ít host nhất → ngân sách thấp nhất → hiệu suất dùng cao nhất. Overcommit thực tế sau khi lắp luôn thấp hơn mục tiêu vì đã trừ headroom và dự phòng.</div>
    </div>"""


def _vm_table(vms: List[Dict[str, Any]], demand: Dict[str, Any]) -> str:
    rows = ""
    for v in vms:
        q = v["qty"]
        rows += (f'<tr><td>{esc(v["name"])}</td><td style="text-align:center;">{fmt_int(q)}</td><td style="text-align:right;">{fmt_num(v["vcpu"], 0)}</td>'
                 f'<td style="text-align:right;">{fmt_num(v["ramGB"], 0)}</td><td style="text-align:right;">{fmt_num(v["diskGB"], 0)}</td>'
                 f'<td style="text-align:right;">{fmt_num(q * v["vcpu"], 0)}</td><td style="text-align:right;">{fmt_num(q * v["ramGB"], 0)}</td>'
                 f'<td style="text-align:right;">{fmt_num(q * v["diskGB"], 0)}</td><td style="color:{COLORS["muted"]};">{esc(v.get("note") or "")}</td></tr>')
    rows += (f'<tr style="font-weight:700;background:{COLORS["surface2"]};"><td>Tổng</td><td style="text-align:center;">{fmt_int(demand["vmCount"])}</td><td></td><td></td><td></td>'
             f'<td style="text-align:right;">{fmt_int(demand["vcpu"])}</td><td style="text-align:right;">{fmt_int(demand["ramGB"])}</td><td style="text-align:right;">{fmt_int(demand["diskGB"])}</td><td></td></tr>')
    return f"""<table class="appendix-table">
      <thead><tr><th>Tên VM / nhóm</th><th>Số lượng</th><th style="text-align:right;">vCPU / VM</th><th style="text-align:right;">RAM GB / VM</th><th style="text-align:right;">Disk GB / VM</th>
      <th style="text-align:right;">Tổng vCPU</th><th style="text-align:right;">Tổng RAM GB</th><th style="text-align:right;">Tổng Disk GB</th><th>Ghi chú</th></tr></thead>
      <tbody>{rows}</tbody></table>"""


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
                       target: Optional[Dict[str, Any]], include_new: bool, currency: str, project: str) -> str:
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
        items.append({"pri": "tb", "title": f"Cấp {_gb(d['storageNeededGB'])} trên SAN cho dự án (đã gồm +{p['storage_overhead_pct']:g}% overhead)",
                      "decision": "Duyệt cấp dung lượng lưu trữ", "time": "Trước triển khai"})
    if target and target.get("extraAvgVms") is not None:
        items.append({"pri": "thap", "title": f"Dư địa còn lại của {target['name']} sau tiếp nhận: ~{fmt_int(target['extraAvgVms'])} VM cỡ dự án (tới 80% ngưỡng)",
                      "decision": "Theo dõi; đưa mở rộng vào kỳ quy hoạch tài nguyên" if target["extraAvgVms"] > 0 else "Lập kế hoạch mở rộng ngay", "time": ""})
    if include_new and best:
        items.append({"pri": "thap", "title": f"Phương án B — trang bị mới {n_new} host {best['profile']['name']}" + (f", ngân sách {budget}" if budget else ""),
                      "decision": "Không phải chi nếu dùng chung" if (target and _feasible(target)) else "Phương án dự phòng nếu không dùng chung được", "time": ""})

    li = ""
    for i, it in enumerate(items, 1):
        time_html = f' <span class="rec-sum-time">· {esc(it["time"])}</span>' if it["time"] else ""
        li += (f'<li class="rec-sum-item"><span class="rec-num">{i}</span>'
               f'<span class="priority-pill {PRIORITY_CSS[it["pri"]]}">{PRIORITY_TXT[it["pri"]]}</span>'
               f'<span class="rec-sum-text"><b>{esc(it["title"])}</b> → {esc(it["decision"])}{time_html}</span></li>')

    tgt_cls = "ok" if (target and _feasible(target)) else ("crit" if (not target or target.get("cls") == "crit") else "warn")
    stats = [
        {"label": "VM yêu cầu", "value": fmt_int(d["vmCount"]), "sub": f"TB {fmt_num(d['avgVcpu'], 1)} vCPU · {_gb(d['avgRamGB'])} / VM", "cls": "na"},
        {"label": "vCPU", "value": f"{fmt_int(d['vcpu'])} → {fmt_int(d['vcpuPlanned'])}", "sub": f"sau +{p['growth_pct']:g}% tăng trưởng", "cls": "na"},
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
        <span class="pill" style="color:{COLORS[fg]};background:{COLORS[bg]};border-color:{COLORS[fg]}55;font-size:9px;">{status_label}</span>
        <span class="exec-brief-title">Tổng kết — đề xuất &amp; quyết định đề nghị</span>
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


def render_brief_html(vms: List[sizing.VmSpec], params: sizing.SizingParams, profiles: List[sizing.HostProfile],
                      existing: Optional[List[Dict[str, Any]]] = None, policy_txt: str = "", currency: str = "VND",
                      logo_data_url: Optional[str] = None, prepared_by: str = "", mode: str = "new",
                      target_cluster: Optional[str] = None, include_new: bool = True, include_existing: bool = True,
                      **_ignored) -> str:
    """Short decision memo (summary · comparison · assumptions · VM appendix)."""
    result = sizing.build_sizing(vms, params, profiles)
    best = result["results"][result["recommendedIndex"]] if result["recommendedIndex"] is not None else None
    existing = existing or []
    generated_at = _now_str()
    env_name = params.environment.strip()
    project = env_name or "dự án mới"          # used inside sentences ("… VM của dự án mới …")
    title = "Đề xuất cấu hình máy chủ" + (f" — {esc(env_name)}" if env_name else "")
    eyebrow_project = f" · {esc(env_name)}" if env_name else ""
    logo_html = f'<img class="cover-logo" src="{logo_data_url}" alt="logo"/>' if logo_data_url else ""
    vm_dicts = [{"name": v.name, "qty": v.qty, "vcpu": v.vcpu, "ramGB": v.ram_gb, "diskGB": v.disk_gb, "note": v.note} for v in vms]
    appendix_break = "breakable" if len(vm_dicts) > 12 else ""
    comparison_html = f"""
  <div class="section">
    <div class="section-title">{'Phương án B — trang bị mới (đối chiếu)' if mode == 'reuse' else 'So sánh cấu hình host ứng viên'}</div>
    <div class="section-sub">Số host vật lý cần cho {fmt_int(result['demand']['vmCount'])} VM theo từng cấu hình; dòng tô xanh là phương án {'mua mới tốt nhất' if mode == 'reuse' else 'đề xuất'}.</div>
    {_comparison_table(result, currency)}
    {('<div class="insight-box" style="margin-top:8px;"><b>' + ('Nếu phải mua mới.' if mode == 'reuse' else 'Phương án đề xuất.') + '</b> ' + esc(result['recommendation']['detail']) + '</div>') if best else ''}
  </div>"""
    if mode == "reuse":
        target = _pick_target(existing, target_cluster)
        summary_html = _reuse_summary_box(result, best, existing, target, include_new, currency, project)
        if existing:
            existing_html = f"""
  <div class="section">
    <div class="section-title">Hạ tầng hiện hữu — khả năng tiếp nhận dự án</div>
    <div class="section-sub">Mọi cluster trong phạm vi vCenter đang chọn; dòng tô màu là cluster đang xét tiếp nhận (xanh: an toàn, cam: cần bổ sung host).</div>
    {_existing_table(existing, policy_txt, highlight=target['name'] if target else None)}
    {_target_detail(target, result['demand']) if target else ''}
  </div>"""
        else:
            existing_html = ('<div class="section"><div class="section-title">Hạ tầng hiện hữu — khả năng tiếp nhận dự án</div>'
                             '<div class="small-note" style="font-size:9px;">Không có dữ liệu cluster hiện hữu (chưa đồng bộ vCenter hoặc chưa import host/VM).</div></div>')
        main_html = existing_html + (comparison_html if include_new else "")
    else:
        summary_html = _summary_box(result, best, existing if include_existing else [], currency)
        existing_html = ""
        if existing and include_existing:
            existing_html = f"""
  <div class="section">
    <div class="section-title">Hạ tầng hiện hữu có hấp thụ được không?</div>
    {_existing_table(existing, policy_txt)}
  </div>"""
        main_html = comparison_html + existing_html
    body = f"""
  {summary_html}
  {main_html}
  <div class="section">
    <div class="section-title">Giả định</div>
    {_assumptions_html(result)}
  </div>
  <div class="section {appendix_break}" style="{'page-break-before:always;' if len(vm_dicts) > 12 else ''}">
    <div class="section-title">Phụ lục — Danh sách VM yêu cầu (team Dev cung cấp)</div>
    {_vm_table(vm_dicts, result['demand'])}
    <div class="small-note">Đề xuất này là tài liệu sizing dựa trên giả định; cấu hình và số lượng cuối cùng cần đối chiếu báo giá nhà cung cấp và xác nhận của chủ dự án trước khi trình duyệt ngân sách.{(' Người lập: ' + esc(prepared_by) + '.') if prepared_by else ''}</div>
  </div>"""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><style>{_base_css('Đề xuất cấu hình máy chủ', generated_at)}{_exec_visual_css()}{_planning_css()}
.decision-table td {{ vertical-align:middle; }}
.appendix-table td {{ font-size:8.3px; padding:3.5px 6px; }}
</style></head>
<body>
  <div class="cover-header">
    <div>
      <div class="eyebrow">Infrastructure Capacity Ops · Host Sizing · {'Tận dụng hiện hữu' if mode == 'reuse' else 'Trang bị mới'}{eyebrow_project} · {esc(generated_at.split(' ')[-1])}</div>
      <div class="cover-title" style="margin-bottom:6px;">{title}</div>
    </div>
    {logo_html}
  </div>
  {body}
</body></html>"""



# ===========================================================================
# Full consulting report ("Báo cáo tư vấn sizing") — cover, TOC and chaptered
# analysis with charts. Built for delivery to customers / partners, so every
# label that names the issuer is white-label (prepared_org, customer, code).
# ===========================================================================
SEV_PILL = {"critical": ("crit", "Nghiêm trọng"), "warning": ("warn", "Cảnh báo"), "info": ("na", "Lưu ý"), "ok": ("ok", "Đạt")}
READINESS_TXT = {"ok": ("ok", "SẴN SÀNG TRIỂN KHAI"), "warn": ("warn", "TRIỂN KHAI ĐƯỢC · CẦN LƯU Ý"), "crit": ("crit", "CẦN XỬ LÝ RỦI RO")}


def _fmt_dec(v: Optional[float], d: int = 1) -> str:
    return "—" if v is None else fmt_num(v, d)


def _legend(items: List[tuple]) -> str:
    return '<div class="legend">' + "".join(
        f'<span><span class="dot" style="background:{c};"></span>{esc(t)}</span>' for c, t in items) + "</div>"


def _capacity_segments(r: Dict[str, Any], d: Dict[str, Any], p: Dict[str, Any]) -> List[Dict[str, Any]]:
    n, red, n_fail = r["hostsTotal"], r["redundancy"], r["failure"]["hostsLeft"]
    util = max(0.05, min(1.0, p["max_util_pct"] / 100.0))
    ph, prof = r["perHost"], r["profile"]
    rows = []
    for label, total, usable_host, req, planned in (
        ("CPU", prof["cores"] * max(1.0, p["ht_factor"]) * p["cpu_ratio"] * n, ph["logicalCpu"] * p["cpu_ratio"], d["vcpu"], d["vcpuPlanned"]),
        ("RAM", prof["ramGB"] * p["ram_ratio"] * n, ph["usableRamGB"] * p["ram_ratio"], d["ramGB"], d["ramPlannedGB"]),
    ):
        rows.append({"label": label, "segments": [
            {"key": "request", "label": "Yêu cầu của dự án", "value": req},
            {"key": "growth", "label": "Dự phòng tăng trưởng", "value": planned - req},
            {"key": "free", "label": "Còn trống (sau N+x)", "value": max(0.0, usable_host * n_fail * util - planned)},
            {"key": "headroom", "label": f"Headroom {100 - p['max_util_pct']:g}%", "value": usable_host * n_fail * (1 - util)},
            {"key": "redundancy", "label": f"Dự phòng N+{red}", "value": usable_host * red},
            {"key": "esxi", "label": "ESXi", "value": max(0.0, total - usable_host * n)},
        ]})
    return rows


def _cover(result, best, params, *, title, customer, prepared_org, prepared_by, doc_code, generated_at, logo_html, currency, mode) -> str:
    d = result["demand"]
    a = result["analysis"]
    cls, txt = READINESS_TXT.get(a.get("readiness") or "ok")
    fg, bg = STATUS_COLOR[cls]
    tco = best["advisory"]["costs"]["tco"] if best else None
    tiles = [
        ("VM yêu cầu", fmt_int(d["vmCount"]), f"{fmt_int(d['vcpu'])} vCPU · {_gb(d['ramGB'])} RAM"),
        ("Host đề xuất", f"{best['hostsTotal']}" if best else "—", f"{best['profile']['name']} · N+{best['redundancy']}" if best else ""),
        (f"TCO {params.horizon_years} năm" if tco else "Lưu trữ cần cấp", money_short(tco, currency) if tco else _gb(d["storageNeededGB"]),
         "phần cứng + license" if tco else ("SAN dùng chung" if params.storage_mode == "san" else "cục bộ / vSAN")),
        ("Rủi ro / lưu ý", f"{a['findingCounts'].get('critical', 0)} / {a['findingCounts'].get('warning', 0)}", "nghiêm trọng / cảnh báo"),
    ]
    tiles_html = "".join(f'<div class="cv-tile"><div class="cv-tile-label">{esc(l)}</div><div class="cv-tile-value">{esc(v)}</div><div class="cv-tile-sub">{esc(s)}</div></div>' for l, v, s in tiles)
    meta = [("Dự án / hệ thống", params.environment or "—"), ("Khách hàng", customer or "—"),
            ("Đơn vị tư vấn", prepared_org or "—"), ("Người lập", prepared_by or "—"),
            ("Mã tài liệu", doc_code or "—"), ("Ngày lập", generated_at or "—"),
            ("Phạm vi", "Tận dụng hạ tầng hiện hữu + trang bị mới" if mode == "reuse" else "Trang bị mới máy chủ vật lý")]
    meta_html = "".join(f"<tr><td>{esc(k)}</td><td><b>{esc(v)}</b></td></tr>" for k, v in meta)
    toc = [("sec-summary", "Tóm tắt điều hành"), ("sec-workload", "Phân tích nhu cầu workload"),
           ("sec-options", "So sánh phương án cấu hình"), ("sec-design", "Thiết kế đề xuất chi tiết"),
           ("sec-sensitivity", "Độ nhạy & dự báo tăng trưởng"), ("sec-risks", "Rủi ro & khuyến nghị"),
           ("sec-roadmap", "Lộ trình triển khai"), ("sec-method", "Giả định & phương pháp"), ("sec-appendix", "Phụ lục — Danh sách VM")]
    if mode == "reuse":
        toc.insert(1, ("sec-existing", "Hạ tầng hiện hữu — khả năng tiếp nhận"))
    toc_html = "".join(f'<li><a href="#{i}">{esc(t)}</a></li>' for i, t in toc)
    return f"""
<div class="cover-page">
  <div class="cv-top">
    <div class="cv-brand">{esc(prepared_org) if prepared_org else 'Infrastructure Capacity Advisory'}</div>
    {logo_html}
  </div>
  <div class="cv-main">
    <div class="eyebrow">Báo cáo tư vấn · Sizing hạ tầng ảo hoá VMware vSphere</div>
    <div class="cv-title">{esc(title)}</div>
    <div class="cv-sub">{esc(result['recommendation']['headline']) if best else 'Chưa có VM trong danh sách yêu cầu.'}</div>
    <span class="pill" style="color:{COLORS[fg]};background:{COLORS[bg]};border-color:{COLORS[fg]}55;font-size:9px;">{txt}</span>
    <div class="cv-tiles">{tiles_html}</div>
  </div>
  <div class="two-col cv-bottom">
    <div><div class="cv-h">Thông tin tài liệu</div><table class="cv-meta">{meta_html}</table></div>
    <div><div class="cv-h">Nội dung</div><ol class="toc">{toc_html}</ol></div>
  </div>
  <div class="cv-foot">Tài liệu tư vấn dựa trên dữ liệu yêu cầu và giả định nêu tại mục "Giả định & phương pháp". Số liệu cuối cùng cần đối chiếu báo giá nhà cung cấp.
  {('Bảo mật — chỉ dùng nội bộ ' + esc(customer) + '.') if customer else 'Bảo mật — chỉ dùng nội bộ.'}</div>
</div>"""


def _section(sec_id: str, num: int, title: str, sub: str, body: str, cls: str = "") -> str:
    sub_html = f'<div class="section-sub">{sub}</div>' if sub else ""
    return f'<div class="section {cls}" id="{sec_id}"><div class="section-title"><span class="sec-num">{num}</span>{esc(title)}</div>{sub_html}{body}</div>'


def _gauges(best: Dict[str, Any], p: Dict[str, Any]) -> str:
    util = p["max_util_pct"]
    zones = ((0.0, util, "ok"), (util, 100.0, "warn"))
    f = best["failure"]
    items = [
        (best["after"]["cpuUtilPct"], "CPU · vận hành", f"{best['hostsTotal']} host"),
        (best["after"]["ramUtilPct"], "RAM · vận hành", f"{best['hostsTotal']} host"),
        (f["cpuPct"], f"CPU · mất {best['redundancy']} host", f"{f['hostsLeft']} host còn lại"),
        (f["ramPct"], f"RAM · mất {best['redundancy']} host", f"{f['hostsLeft']} host còn lại"),
    ]
    cards = ""
    for pct, top, cap in items:
        cls = "na" if pct is None else ("ok" if pct <= util else ("warn" if pct <= 100 else "crit"))
        color = COLORS[STATUS_COLOR[cls][0]] if cls != "na" else COLORS["faint"]
        cards += f'<div class="gauge-card {cls}">{svg_gauge(pct, color, size=118, label_top=top, caption=cap, zones=zones)}</div>'
    return f'<div class="gauge-row">{cards}</div>'


def _findings_table(fnd: List[Dict[str, str]], limit: Optional[int] = None) -> str:
    rows = ""
    for x in fnd[:limit] if limit else fnd:
        cls, lbl = SEV_PILL.get(x["severity"], ("na", x["severity"]))
        rows += (f'<tr><td>{_pill(lbl, cls)}</td><td style="white-space:nowrap;color:{COLORS["muted"]};">{esc(x["area"])}</td>'
                 f'<td><b>{esc(x["title"])}</b><div style="color:{COLORS["muted"]};font-size:8.2px;">{esc(x["detail"])}</div></td>'
                 f'<td style="font-size:8.4px;">{esc(x["action"])}</td></tr>')
    return f"""<table class="findings-table"><thead><tr><th style="width:62px;">Mức độ</th><th style="width:70px;">Lĩnh vực</th><th>Nhận định</th><th style="width:34%;">Khuyến nghị</th></tr></thead><tbody>{rows}</tbody></table>"""


def _workload_section(result: Dict[str, Any], params) -> str:
    d, a = result["demand"], result["analysis"]
    wl = a["workload"]
    bucket_colors = ["#bfdbfe", "#60a5fa", "#2681FF", "#1d4ed8", "#074B9B"]
    segs = [{"label": b["label"], "value": b["count"], "color": bucket_colors[i]} for i, b in enumerate(wl["sizeBuckets"])]
    donut = svg_donut(segs, size=118, center_label=fmt_int(d["vmCount"]), center_sub="VM")
    leg = "".join(f'<div class="radar-legend-item"><span class="radar-swatch" style="background:{s["color"]};"></span>{esc(s["label"])} — <b>{fmt_int(s["value"])}</b> VM</div>'
                  for s in segs if s["value"])
    top = [{"label": g["name"], "a": g["vcpuShare"], "b": g["ramShare"]} for g in wl["topGroups"][:8]]
    wf = a["waterfall"]
    wf_cpu = svg_waterfall(wf["cpu"], width=330, height=165, unit="vCPU")
    wf_ram = svg_waterfall(wf["ram"], width=330, height=165, unit="GB RAM")
    lg = wl["largestVm"] or {}
    char_note = {
        "compute": "Ưu tiên CPU nhiều core, RAM/host vừa phải (≈ 4 GB/core logic).",
        "balanced": "Cấu hình phổ thông 2 socket, tỷ lệ RAM/core trung bình là phù hợp.",
        "memory": "Ưu tiên host nhiều RAM (≥ 1 TB) — RAM sẽ là tài nguyên quyết định số host.",
    }[wl["character"]]
    kpis = f"""<div class="kpi-grid">
      <div class="kpi-card"><div class="kpi-label">VM · nhóm cấu hình</div><div class="kpi-value">{fmt_int(d['vmCount'])}</div><div class="kpi-sub">{wl['groupCount']} nhóm · {wl['paretoLines']} nhóm chiếm 80% RAM</div></div>
      <div class="kpi-card"><div class="kpi-label">Tổng vCPU / RAM</div><div class="kpi-value">{fmt_int(d['vcpu'])} / {_gb(d['ramGB'])}</div><div class="kpi-sub">TB {fmt_num(d['avgVcpu'], 1)} vCPU · {_gb(d['avgRamGB'])} mỗi VM</div></div>
      <div class="kpi-card"><div class="kpi-label">Tỷ lệ RAM / vCPU</div><div class="kpi-value">{fmt_num(wl['ramPerVcpu'], 1)} GB</div><div class="kpi-sub">{esc(wl['characterLabel'])}</div></div>
      <div class="kpi-card"><div class="kpi-label">VM lớn nhất</div><div class="kpi-value">{fmt_num(lg.get('vcpu'), 0)} vCPU</div><div class="kpi-sub">{esc(lg.get('name') or '—')} · {_gb(lg.get('ramGB') or 0)} RAM</div></div>
    </div>"""
    return f"""{kpis}
    <div class="chart-row" style="margin-top:8px;">
      <div class="chart-card"><div class="chart-card-title">Phân bố kích thước VM</div>
        <div style="display:flex;gap:10px;align-items:center;">{donut}<div>{leg}</div></div></div>
      <div class="chart-card"><div class="chart-card-title">Nhóm VM chiếm tài nguyên lớn nhất (% tổng)</div>{svg_dual_hbars(top, width=330)}
        {_legend([(COLORS['primary'], '% vCPU'), (COLORS['accent'], '% RAM')])}</div>
    </div>
    <div class="chart-row" style="margin-top:8px;">
      <div class="chart-card"><div class="chart-card-title">Từ yêu cầu đến năng lực lắp đặt — CPU</div>{wf_cpu}</div>
      <div class="chart-card"><div class="chart-card-title">Từ yêu cầu đến năng lực lắp đặt — RAM</div>{wf_ram}</div>
    </div>
    <div class="insight-box" style="margin-top:8px;"><b>Đặc tính workload:</b> {esc(wl['characterLabel'])} ({fmt_num(wl['ramPerVcpu'], 1)} GB RAM / vCPU). {esc(char_note)}
    {(' Có ' + str(len(wl['dbLike'])) + ' nhóm CSDL / middleware (' + esc(', '.join(wl['dbLike'][:4])) + ') — nên áp chính sách overcommit thấp hơn.') if wl['dbLike'] else ''}
    Biểu đồ thác nước cho thấy mỗi giả định (tăng trưởng, headroom) làm tăng năng lực cần có thế nào; cột cuối là năng lực ở overcommit mục tiêu của phương án đề xuất (đã gồm dự phòng N+x và phần ESXi bị trừ).</div>"""


def _options_section(result: Dict[str, Any], currency: str) -> str:
    rows = sorted(result["results"], key=lambda x: x["rank"] or 99)
    labels = [r["profile"]["name"] for r in rows]
    hosts_chart = svg_bar_chart(labels, [
        {"name": "Theo CPU", "color": COLORS["primary"], "values": [r["hostsForCpu"] for r in rows]},
        {"name": "Theo RAM", "color": COLORS["accent"], "values": [r["hostsForRam"] for r in rows]},
        {"name": "Tổng (gồm N+x)", "color": COLORS["primary_dark"], "values": [r["hostsTotal"] for r in rows]},
    ], width=330, height=170)
    axes = result["analysis"]["scoreAxes"]
    radar_series = []
    for i, r in enumerate(rows):
        sc = r["advisory"].get("scores")
        if not sc:
            continue
        radar_series.append({"name": r["profile"]["name"], "color": SERIES_COLORS[i % len(SERIES_COLORS)], "values": [sc[ax["key"]] for ax in axes]})
    radar = svg_radar_chart([ax["label"] for ax in axes], radar_series[:5], size=196)
    radar_leg = "".join(f'<div class="radar-legend-item" style="white-space:nowrap;"><span class="radar-swatch" style="background:{s["color"]};"></span><span>{esc(s["name"])}<br/><b>{fmt_num(sum(s["values"]) / len(s["values"]), 0)}</b>/100 điểm</span></div>' for s in radar_series[:5])
    score_rows = ""
    for r in rows:
        sc = r["advisory"].get("scores") or {}
        tds = "".join(f'<td style="text-align:center;">{fmt_num(sc.get(ax["key"]), 0)}</td>' for ax in axes)
        style = f' style="background:{COLORS["ok_bg"]};font-weight:700;"' if r["recommended"] else ""
        score_rows += f'<tr{style}><td>{esc(r["profile"]["name"])}</td>{tds}<td style="text-align:center;font-weight:800;">{fmt_num(r["advisory"].get("scoreTotal"), 0)}</td></tr>'
    score_table = (f'<table class="score-table"><thead><tr><th>Cấu hình</th>' + "".join(f'<th style="text-align:center;">{esc(ax["label"])}</th>' for ax in axes)
                   + f'<th style="text-align:center;">Điểm TB</th></tr></thead><tbody>{score_rows}</tbody></table>')
    cost_html = ""
    if any(r["advisory"]["costs"]["tco"] for r in rows):
        yrs = rows[0]["advisory"]["costs"]["years"]
        cost_rows = [{"label": r["profile"]["name"], "hw": r["advisory"]["costs"]["hardware"], "lic": r["advisory"]["costs"]["license"], "best": r["recommended"]} for r in rows]
        unit_rows = ""
        for r in rows:
            c = r["advisory"]["costs"]
            unit_rows += (f'<tr><td>{esc(r["profile"]["name"])}{" " + _pill("Đề xuất", "ok") if r["recommended"] else ""}</td>'
                          f'<td style="text-align:right;">{money_short(c["hardware"], currency)}</td><td style="text-align:right;">{money_short(c["license"], currency)}</td>'
                          f'<td style="text-align:right;font-weight:700;">{money_short(c["tco"], currency)}</td><td style="text-align:right;">{money_short(c["perVm"], currency)}</td>'
                          f'<td style="text-align:right;">{money_short(c["perVmMonth"], currency)}</td><td style="text-align:right;">{money_short(c["perVcpu"], currency)}</td></tr>')
        cost_html = f"""
    <div class="chart-row" style="margin-top:8px;">
      <div class="chart-card" style="flex:0.9;"><div class="chart-card-title">TCO {yrs} năm theo phương án</div>{svg_cost_bars(cost_rows, currency, width=300)}
        {_legend([(COLORS['primary'], 'Phần cứng'), (COLORS['accent'] + '8c', f'License {yrs} năm')])}</div>
      <div class="chart-card" style="flex:1.1;"><div class="chart-card-title">Chi phí đơn vị</div>
        <table class="mini-table"><thead><tr><th>Cấu hình</th><th style="text-align:right;">Phần cứng</th><th style="text-align:right;">License</th><th style="text-align:right;">TCO</th><th style="text-align:right;">/ VM</th><th style="text-align:right;">/ VM / tháng</th><th style="text-align:right;">/ vCPU</th></tr></thead><tbody>{unit_rows}</tbody></table></div>
    </div>"""
    return f"""
    <div class="chart-row">
      <div class="chart-card"><div class="chart-card-title">Số host cần theo từng ràng buộc</div>{hosts_chart}
        {_legend([(COLORS['primary'], 'Theo CPU'), (COLORS['accent'], 'Theo RAM'), (COLORS['primary_dark'], 'Tổng gồm N+x')])}</div>
      <div class="chart-card"><div class="chart-card-title">Bảng điểm đa tiêu chí (0–100, cao = tốt)</div>
        <div style="display:flex;gap:8px;align-items:center;">{radar}<div>{radar_leg}</div></div></div>
    </div>
    <div style="margin-top:8px;">{_comparison_table(result, currency)}</div>
    <div style="margin-top:8px;">{score_table}</div>
    <div class="small-note">Điểm: <b>Số host</b> &amp; <b>Chi phí</b> so với phương án tốt nhất (chi phí theo TCO nếu có đơn giá, nếu không theo số core license);
    <b>Hiệu suất</b> = mức dùng tài nguyên ràng buộc khi mất host dự phòng; <b>Cân bằng</b> = độ lệch giữa mức dùng CPU và RAM; <b>Chịu lỗi</b> = mức tải khi mất host;
    <b>Mở rộng</b> = dư địa tăng trưởng thêm; <b>NUMA</b> = tỷ lệ VM nằm gọn trong một socket.</div>
    {cost_html}"""


def _design_section(result: Dict[str, Any], best: Dict[str, Any], params) -> str:
    d, p = result["demand"], result["params"]
    prof, ph, adv = best["profile"], best["perHost"], best["advisory"]
    numa, bal, lic, run = adv["numa"], adv["balance"], adv["license"], adv["runway"]
    spec = [
        ("Cấu hình host", f"{prof['sockets']} socket × {prof['coresPerSocket']} cores = {prof['cores']} cores · {_gb(prof['ramGB'])} RAM" + (f" · {_gb(prof['diskGB'])} đĩa" if p["storage_mode"] == "local" else "")),
        ("Số host", f"{best['hostsTotal']} (= {best['hostsBase']} theo tải + {best['redundancy']} dự phòng" + (f", tối thiểu {p['min_hosts']}" if best["hostsTotal"] > best["hostsBase"] + best["redundancy"] else "") + ")"),
        ("Năng lực mỗi host", f"{fmt_num(ph['usableCores'], 0)} cores khả dụng · {fmt_num(ph['logicalCpu'], 0)} pCPU logic · {_gb(ph['usableRamGB'])} RAM khả dụng"),
        ("Năng lực quy hoạch / host", f"{fmt_num(ph['vcpuCapacity'], 0)} vCPU · {_gb(ph['vramCapacityGB'])} vRAM (ở {p['cpu_ratio']:g}x / {p['ram_ratio']:g}x, dùng tối đa {p['max_util_pct']:g}%)"),
        ("Tổng cluster", f"{fmt_int(best['totals']['cores'])} cores · {fmt_int(best['totals']['logicalCpu'])} pCPU logic · {_gb(best['totals']['ramGB'])} RAM"),
        ("Overcommit thực tế", f"CPU {fmt_num(best['after']['cpuOvercommit'], 2, 'x')} · RAM {fmt_num(best['after']['ramOvercommit'], 2, 'x')} (mục tiêu {p['cpu_ratio']:g}x / {p['ram_ratio']:g}x)"),
        ("NUMA", f"{fmt_int(prof['coresPerSocket'])} cores · {_gb(numa['ramPerSocketGB'])} mỗi socket — " + ("mọi VM nằm gọn trong 1 NUMA node" if numa["ok"] else f"{len(numa['wideCpu']) + len(numa['wideRam'])} nhóm VM trải nhiều NUMA node")),
        ("License (theo core)", f"{fmt_int(lic['coresTotal'])} core ({fmt_int(lic['coresPerHost'])}/host, tối thiểu {params.license_min_cores_per_socket}/socket)" + (f" · {fmt_int(lic['paddedCores'])} core trả thừa" if lic["paddedCores"] else "")),
        ("Dư địa tăng trưởng", ("—" if run["extraGrowthPct"] is None else f"thêm ~{fmt_num(max(0, run['extraGrowthPct']), 0)}% tải ngoài +{p['growth_pct']:g}% đã dự phòng (≈ {fmt_int(best['spare']['extraAvgVms'] or 0)} VM cỡ TB) mà vẫn giữ N+{best['redundancy']}")),
        ("Lưu trữ", (f"SAN: {_gb(d['storageNeededGB'])} (gồm tăng trưởng + {p['storage_overhead_pct']:g}% overhead)" if p["storage_mode"] == "san"
                     else f"Cục bộ / vSAN: {_gb(d['storageNeededGB'])} tổng · ~{_gb(best['storage'].get('perHostNeededGB'))}/host (×{p['storage_protection_factor']:g} bảo vệ)")),
    ]
    spec_html = "".join(f'<tr><td style="width:150px;color:{COLORS["muted"]};font-weight:700;">{esc(k)}</td><td>{esc(v)}</td></tr>' for k, v in spec)
    stack = svg_stack_bars(_capacity_segments(best, d, p), width=680)
    seg_leg = _legend([(SEG_COLORS["request"], "Yêu cầu dự án"), (SEG_COLORS["growth"], "Dự phòng tăng trưởng"), (SEG_COLORS["free"], "Còn trống"),
                       (SEG_COLORS["headroom"], f"Headroom {100 - p['max_util_pct']:g}%"), (SEG_COLORS["redundancy"], f"Dự phòng N+{best['redundancy']}"),
                       (SEG_COLORS["esxi"], "ESXi")])
    bal_txt = ""
    if bal["strandedCpuPct"] >= 15 and bal["idealRamPerHostGB"]:
        bal_txt = f"RAM là ràng buộc: khi RAM chạm ngưỡng, ~{fmt_num(bal['strandedCpuPct'], 0)}% năng lực CPU vẫn còn trống. Với workload này, cấu hình cân bằng là ≈ <b>{_gb(bal['idealRamPerHostGB'])} RAM</b> cho {prof['cores']} cores/host."
    elif bal["strandedRamPct"] >= 15 and bal["idealCoresPerHost"]:
        bal_txt = f"CPU là ràng buộc: ~{fmt_num(bal['strandedRamPct'], 0)}% RAM còn trống khi CPU chạm ngưỡng. Cấu hình cân bằng là ≈ <b>{fmt_num(bal['idealCoresPerHost'], 0)} cores</b> cho {_gb(prof['ramGB'])} RAM/host."
    else:
        bal_txt = f"CPU ({fmt_num(bal['cpuUtilPct'], 0)}%) và RAM ({fmt_num(bal['ramUtilPct'], 0)}%) được dùng tương đối cân bằng — ít tài nguyên bị lãng phí."
    return f"""
    <table class="spec-table">{spec_html}</table>
    <div class="chart-card" style="margin-top:8px;"><div class="chart-card-title">Phân bổ năng lực cluster đề xuất (quy đổi ở overcommit mục tiêu)</div>{stack}{seg_leg}</div>
    <div class="insight-box" style="margin-top:8px;"><b>Cân bằng CPU / RAM.</b> {bal_txt}</div>"""


def _sensitivity_section(result: Dict[str, Any], best_idx: int) -> str:
    a = result["analysis"]
    sens, proj = a["sensitivity"], a["projection"]

    def chart(key: str, fmt, title: str, note: str) -> str:
        sw = sens[key]
        xs = sw["x"]
        cur = xs.index(sw["current"]) if sw["current"] in xs else None
        series = [{"name": s["name"], "color": SERIES_COLORS[i % len(SERIES_COLORS)], "values": s["hosts"]} for i, s in enumerate(sw["series"])]
        return (f'<div class="chart-card"><div class="chart-card-title">{esc(title)}</div>'
                f'{svg_lines([fmt(x) for x in xs], series, width=330, height=150, current_idx=cur, y_label="host")}'
                f'<div class="small-note" style="margin-top:2px;">{esc(note)}</div></div>')

    leg = _legend([(SERIES_COLORS[i % len(SERIES_COLORS)], s["name"]) for i, s in enumerate(sens["growth"]["series"])])
    pts = proj["points"] if proj else []
    proj_note = (f"Với tăng trưởng {proj['annualGrowthPct']:g}%/năm, {proj['installed']} host đã lắp " +
                 (f"đủ dùng đến hết năm thứ {proj['expandYear'] - 1}; cần mở rộng vào năm thứ {proj['expandYear']}." if proj["expandYear"] is not None
                  else f"đủ dùng trong toàn bộ {proj['years']} năm dự báo.")) if proj else ""
    return f"""
    <div class="chart-row">
      {chart('growth', lambda x: f"{x:g}%", 'Số host theo mức dự phòng tăng trưởng', 'Cho biết ngân sách thay đổi thế nào nếu dự án tăng nhanh hơn dự kiến.')}
      {chart('cpuRatio', lambda x: f"{x:g}x", 'Số host theo overcommit CPU mục tiêu', 'Đường phẳng = RAM đang là ràng buộc, nâng overcommit CPU không giảm được host.')}
    </div>
    {leg}
    <div class="chart-row" style="margin-top:8px;">
      {chart('maxUtil', lambda x: f"{x:g}%", 'Số host theo mức dùng tối đa mỗi host', 'Headroom càng lớn càng an toàn nhưng cần nhiều host hơn.')}
      <div class="chart-card"><div class="chart-card-title">Dự báo nhu cầu host theo năm — {esc(result['results'][best_idx]['profile']['name'])}</div>
        {svg_projection(pts, proj['installed'] if proj else 0, width=330, height=150)}
        <div class="small-note" style="margin-top:2px;">{esc(proj_note)}</div></div>
    </div>"""


def _existing_chart(existing: List[Dict[str, Any]]) -> str:
    rows = existing[:10]
    labels = [e["name"] for e in rows]
    val = lambda e, k: e.get(k) if e.get(k) is not None else 0  # noqa: E731
    cpu = svg_bar_chart(labels, [
        {"name": "Trước", "color": "#93c5fd", "values": [val(e, "cpuBefore") for e in rows]},
        {"name": "Sau", "color": COLORS["primary"], "values": [val(e, "cpuAfter") for e in rows]},
    ], width=330, height=165, threshold=100, threshold_label="Ngưỡng", unit="%")
    ram = svg_bar_chart(labels, [
        {"name": "Trước", "color": "#67e8f9", "values": [val(e, "ramBefore") for e in rows]},
        {"name": "Sau", "color": COLORS["accent"], "values": [val(e, "ramAfter") for e in rows]},
    ], width=330, height=165, threshold=100, threshold_label="Ngưỡng", unit="%")
    return f"""<div class="chart-row" style="margin-bottom:8px;">
      <div class="chart-card"><div class="chart-card-title">CPU % ngưỡng chính sách — trước / sau tiếp nhận</div>{cpu}{_legend([("#93c5fd", "Trước"), (COLORS["primary"], "Sau tiếp nhận")])}</div>
      <div class="chart-card"><div class="chart-card-title">RAM % ngưỡng chính sách — trước / sau tiếp nhận</div>{ram}{_legend([("#67e8f9", "Trước"), (COLORS["accent"], "Sau tiếp nhận")])}</div>
    </div>"""


def _roadmap_html(steps: List[Dict[str, str]]) -> str:
    rows = "".join(f'<tr><td style="white-space:nowrap;"><b>{esc(s["phase"])}</b></td><td style="white-space:nowrap;color:{COLORS["muted"]};">{esc(s["when"])}</td><td>{esc(s["what"])}</td></tr>' for s in steps)
    return f'<table class="roadmap-table"><thead><tr><th>Giai đoạn</th><th>Thời điểm</th><th>Nội dung</th></tr></thead><tbody>{rows}</tbody></table>'


def _full_css() -> str:
    return f"""
@page cover {{ margin:14mm 14mm 14mm 14mm; @top-left {{ content:none; }} @top-right {{ content:none; }} @bottom-left {{ content:none; }} @bottom-right {{ content:none; }} }}
.cover-page {{ page:cover; height:268mm; display:flex; flex-direction:column; page-break-after:always; }}
.cv-top {{ display:flex; justify-content:space-between; align-items:center; border-bottom:2px solid {COLORS['primary_dark']}; padding-bottom:8px; }}
.cv-brand {{ font-size:11px; font-weight:800; letter-spacing:.08em; text-transform:uppercase; color:{COLORS['primary_dark']}; }}
.cv-main {{ margin-top:48mm; }}
.cv-title {{ font-size:28px; font-weight:800; color:{COLORS['primary_dark']}; line-height:1.2; margin:4px 0 10px; }}
.cv-sub {{ font-size:11.5px; color:{COLORS['ink']}; line-height:1.5; margin-bottom:10px; max-width:160mm; }}
.cv-tiles {{ display:flex; gap:8px; margin-top:14px; }}
.cv-tile {{ flex:1; border:1px solid {COLORS['border']}; border-top:3px solid {COLORS['primary']}; border-radius:4px; padding:8px 10px; background:{COLORS['surface']}; }}
.cv-tile-label {{ font-size:7.6px; font-weight:700; letter-spacing:.07em; text-transform:uppercase; color:{COLORS['muted']}; }}
.cv-tile-value {{ font-size:18px; font-weight:800; color:{COLORS['ink']}; margin-top:2px; }}
.cv-tile-sub {{ font-size:8px; color:{COLORS['muted']}; }}
.cv-bottom {{ margin-top:auto; }}
.cv-h {{ font-size:8.5px; font-weight:700; letter-spacing:.1em; text-transform:uppercase; color:{COLORS['accent']}; margin-bottom:4px; }}
.cv-meta td {{ border-bottom:1px solid {COLORS['divider']}; padding:3px 4px; font-size:9px; }}
.cv-meta tr:nth-child(even) {{ background:transparent; }}
.cv-meta td:first-child {{ color:{COLORS['muted']}; width:110px; }}
ol.toc {{ margin:0; padding-left:16px; font-size:9.6px; line-height:1.85; }}
ol.toc a {{ color:{COLORS['ink']}; text-decoration:none; }}
ol.toc a::after {{ content: leader('.') target-counter(attr(href), page); color:{COLORS['muted']}; }}
.cv-foot {{ margin-top:12px; font-size:7.8px; color:{COLORS['faint']}; border-top:1px solid {COLORS['divider']}; padding-top:6px; }}
.sec-num {{ display:inline-block; min-width:16px; height:16px; line-height:16px; text-align:center; border-radius:3px; background:{COLORS['primary_dark']}; color:#fff; font-size:9px; margin-right:6px; }}
.section.page {{ page-break-before:always; }}
.spec-table td {{ font-size:9px; padding:4px 6px; }}
.findings-table td {{ vertical-align:top; font-size:8.8px; }}
.score-table th, .score-table td {{ font-size:8px; padding:3.5px 5px; }}
.mini-table th, .mini-table td {{ font-size:7.8px; padding:3px 4px; white-space:nowrap; }}
.roadmap-table td {{ vertical-align:top; }}
.decision-table td {{ vertical-align:middle; }}
.appendix-table td {{ font-size:8.3px; padding:3.5px 6px; }}
"""


def render_full_html(vms: List[sizing.VmSpec], params: sizing.SizingParams, profiles: List[sizing.HostProfile],
                     existing: Optional[List[Dict[str, Any]]] = None, policy_txt: str = "", currency: str = "VND",
                     logo_data_url: Optional[str] = None, prepared_by: str = "", mode: str = "new",
                     target_cluster: Optional[str] = None, include_new: bool = True, include_existing: bool = True,
                     customer_name: str = "", prepared_org: str = "", document_code: str = "") -> str:
    result = sizing.build_sizing(vms, params, profiles)
    best_idx = result["recommendedIndex"]
    best = result["results"][best_idx] if best_idx is not None else None
    existing = existing or []
    generated_at = _now_str()
    env_name = params.environment.strip()
    project = env_name or "dự án mới"
    title = "Đề xuất cấu hình máy chủ" + (f" — {env_name}" if env_name else "")
    logo_html = f'<img class="cover-logo" src="{logo_data_url}" alt="logo"/>' if logo_data_url else ""
    a = result["analysis"]
    vm_dicts = [{"name": v.name, "qty": v.qty, "vcpu": v.vcpu, "ramGB": v.ram_gb, "diskGB": v.disk_gb, "note": v.note} for v in vms]

    cover = _cover(result, best, params, title=title, customer=customer_name, prepared_org=prepared_org, prepared_by=prepared_by,
                   doc_code=document_code, generated_at=generated_at, logo_html=logo_html, currency=currency, mode=mode)

    target = _pick_target(existing, target_cluster) if mode == "reuse" else None
    summary = (_reuse_summary_box(result, best, existing, target, include_new, currency, project) if mode == "reuse"
               else _summary_box(result, best, existing if include_existing else [], currency))
    top_findings = [x for x in a["findings"] if x["severity"] in ("critical", "warning")][:4]
    summary_body = summary
    if best:
        summary_body += f'<div class="section-sub" style="margin:10px 0 4px 0;">Mức dùng năng lực của phương án đề xuất — khi vận hành bình thường và khi mất {best["redundancy"]} host (vạch chia = ngưỡng dùng tối đa {params.max_util_pct:g}%).</div>'
        summary_body += _gauges(best, result["params"])
    if top_findings:
        summary_body += '<div class="section-sub" style="margin:6px 0 4px 0;">Điểm cần lưu ý chính (chi tiết tại mục Rủi ro &amp; khuyến nghị):</div>' + _findings_table(top_findings)

    n = 1
    sections = [_section("sec-summary", n, "Tóm tắt điều hành", "", summary_body)]
    if mode == "reuse":
        n += 1
        if existing:
            body = (_existing_chart(existing) + _existing_table(existing, policy_txt, highlight=target["name"] if target else None)
                    + (_target_detail(target, result["demand"]) if target else ""))
        else:
            body = '<div class="small-note" style="font-size:9px;">Không có dữ liệu cluster hiện hữu (chưa đồng bộ vCenter hoặc chưa import host/VM).</div>'
        sections.append(_section("sec-existing", n, "Hạ tầng hiện hữu — khả năng tiếp nhận dự án",
                                 "Mọi cluster trong phạm vi vCenter đang chọn; dòng tô màu là cluster đang xét tiếp nhận.", body, "page"))
    n += 1
    sections.append(_section("sec-workload", n, "Phân tích nhu cầu workload",
                             "Danh sách VM do chủ dự án cung cấp được phân loại theo kích thước, tỷ lệ RAM/CPU và mức tiêu thụ tài nguyên.",
                             _workload_section(result, params), "page"))
    if best and (mode != "reuse" or include_new):
        n += 1
        sections.append(_section("sec-options", n, "So sánh phương án cấu hình" + (" (phương án B — trang bị mới)" if mode == "reuse" else ""),
                                 f"{len(result['results'])} cấu hình host ứng viên; dòng tô xanh là phương án đề xuất (tiêu chí: {'chi phí thấp nhất' if params.optimize_for == 'cost' else 'ít host nhất, rồi chi phí'}).",
                                 _options_section(result, currency) + f'<div class="insight-box" style="margin-top:8px;"><b>Phương án đề xuất.</b> {esc(result["recommendation"]["detail"])}</div>', "page"))
        n += 1
        sections.append(_section("sec-design", n, f"Thiết kế đề xuất chi tiết — {best['profile']['name']}",
                                 "Thông số cluster, phân bổ năng lực và các kiểm tra kỹ thuật (NUMA, cân bằng tài nguyên, license).",
                                 _design_section(result, best, params), "page"))
        n += 1
        sections.append(_section("sec-sensitivity", n, "Độ nhạy & dự báo tăng trưởng",
                                 "Số host thay đổi thế nào khi giả định thay đổi, và khi nào cần mở rộng.",
                                 _sensitivity_section(result, best_idx)))
    n += 1
    sections.append(_section("sec-risks", n, "Rủi ro & khuyến nghị",
                             f"{a['findingCounts'].get('critical', 0)} nghiêm trọng · {a['findingCounts'].get('warning', 0)} cảnh báo · {a['findingCounts'].get('info', 0)} lưu ý · {a['findingCounts'].get('ok', 0)} đạt.",
                             _findings_table(a["findings"]) if a["findings"] else '<div class="small-note">Không có nhận định.</div>', "breakable"))
    n += 1
    sections.append(_section("sec-roadmap", n, "Lộ trình triển khai đề xuất", "", _roadmap_html(a["nextSteps"])))
    n += 1
    sections.append(_section("sec-method", n, "Giả định & phương pháp", "", _assumptions_html(result)))
    n += 1
    sections.append(_section("sec-appendix", n, "Phụ lục — Danh sách VM yêu cầu", "Do chủ dự án / team phát triển cung cấp.",
                             _vm_table(vm_dicts, result["demand"])
                             + f'<div class="small-note">Tài liệu sizing dựa trên giả định; cấu hình và số lượng cuối cùng cần đối chiếu báo giá nhà cung cấp và xác nhận của chủ dự án trước khi trình duyệt ngân sách.{(" Người lập: " + esc(prepared_by) + ".") if prepared_by else ""}</div>',
                             "page breakable"))

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><style>{_base_css('Báo cáo tư vấn sizing', generated_at)}{_exec_visual_css()}{_planning_css()}{_full_css()}</style></head>
<body>{cover}{''.join(sections)}</body></html>"""


def render_sizing_html(*args, detail: str = "full", **kwargs) -> str:
    if detail == "brief":
        return render_brief_html(*args, **kwargs)
    return render_full_html(*args, **kwargs)


def build_sizing_pdf(*args, **kwargs) -> bytes:
    return html_to_pdf_bytes(render_sizing_html(*args, **kwargs))
