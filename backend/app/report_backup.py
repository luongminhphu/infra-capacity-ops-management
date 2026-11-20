"""PDF report "Báo cáo hiện trạng Backup" (Veritas NetBackup).

Same page format and visual language as the capacity operations reports
(_base_css / _exec_visual_css, inline SVG charts, A4 portrait). Layout:

  Tóm tắt điều hành → 1 Tỷ lệ thành công theo ngày → 2 Nguyên nhân lỗi →
  3 Client cần xử lý (RPO) → 4 Độ phủ backup VM → 5 Storage → 6 Policy →
  7 Phát hiện & khuyến nghị → 8 Tiêu chí đánh giá → Phụ lục A job lỗi, B VM chưa backup
"""
from typing import Any, Dict, List, Optional

from .pdf_kit import (COLORS, _base_css, _exec_visual_css, _now_str, esc, html_to_pdf_bytes, svg_donut,
                         svg_line_chart)
from .report_sizing_charts import FONT, svg_hbars
from .timeutil import to_vn_str, parse_iso_to_vn_str

SEV_LABEL = {"critical": "Nghiêm trọng", "warning": "Cảnh báo", "info": "Lưu ý"}
SEV_CLS = {"critical": "crit", "warning": "warn", "info": "na"}
READY = {"ok": ("ok", "ĐẠT"), "warn": ("warn", "CẦN THEO DÕI"), "crit": ("crit", "CẦN XỬ LÝ")}
OUT_COLORS = {"success": "#21A33B", "partial": "#B89717", "failed": "#B82E37"}  # status meaning: HSC sg40 / sy30 / sr40
POOL_COLORS = ["#165AB8", "#23A4B8", "#B729FE", "#898C8F", "#074B9B", "#147E8F"]  # HSC series (not status colors)


def vn(v: Optional[float], d: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v:,.{d}f}".replace(",", "§").replace(".", ",").replace("§", ".")


def _t(v: Any, fmt: str = "%H:%M %d/%m") -> str:
    if v is None:
        return "—"
    return parse_iso_to_vn_str(v, fmt) if isinstance(v, str) else to_vn_str(v, fmt)


def _pill(text: str, cls: str) -> str:
    """Same status mark as every report and the web (glyph + ink label; crit = solid block)."""
    from .report_pdf import status_pill
    return status_pill({"text": text, "cls": cls})


def _hours(h: Optional[float]) -> str:
    if h is None:
        return "—"
    return f"{vn(h / 24, 1)} ngày" if h >= 48 else f"{vn(h, 0)} giờ"


def _table(head: List[str], rows: List[List[str]], cls: str = "", widths: Optional[List[str]] = None) -> str:
    if not rows:
        return '<div class="small-note" style="font-size:9px;">Không có mục nào.</div>'
    ths = "".join(f'<th{f" style=\"width:{widths[i]}\"" if widths and widths[i] else ""}>{h}</th>' for i, h in enumerate(head))
    trs = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="{cls}"><thead><tr>{ths}</tr></thead><tbody>{trs}</tbody></table>'


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------
def svg_daily_stack(daily: List[Dict[str, Any]], width: int = 690, height: int = 190) -> str:
    """Stacked bars per day (success / partial / failed) with the success rate printed on top."""
    if not daily:
        return '<div class="small-note">Không có dữ liệu.</div>'
    pad_l, pad_r, pad_t, pad_b = 34, 8, 18, 22
    pw, ph = width - pad_l - pad_r, height - pad_t - pad_b
    mx = max(1, max(d["success"] + d["partial"] + d["failed"] for d in daily))
    step = pw / len(daily)
    bw = min(34.0, step * 0.62)
    out = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" {FONT}>',
           f'<rect x="{pad_l}" y="{pad_t}" width="{pw}" height="{ph}" fill="{COLORS["surface"]}"/>']
    for i in range(5):
        v = mx * i / 4
        y = pad_t + ph - ph * i / 4
        out.append(f'<line x1="{pad_l}" x2="{pad_l + pw}" y1="{y:.1f}" y2="{y:.1f}" stroke="#fff" stroke-width="1"/>')
        out.append(f'<text x="{pad_l - 4}" y="{y + 3:.1f}" text-anchor="end" font-size="7" fill="{COLORS["faint"]}">{vn(v, 0)}</text>')
    every = max(1, len(daily) // 15)
    for i, d in enumerate(daily):
        x = pad_l + i * step + (step - bw) / 2
        y = pad_t + ph
        tot = d["success"] + d["partial"] + d["failed"]
        for k in ("success", "partial", "failed"):
            h = ph * d[k] / mx
            if h > 0:
                y -= h
                out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{h:.1f}" fill="{OUT_COLORS[k]}"/>')
        if tot:
            rate = (d["success"] + d["partial"]) * 100.0 / tot
            col = COLORS["crit"] if d["failed"] and rate < 95 else COLORS["muted"]
            out.append(f'<text x="{x + bw / 2:.1f}" y="{y - 3:.1f}" text-anchor="middle" font-size="6.8" font-weight="700" fill="{col}">{vn(rate, 0)}%</text>')
        if i % every == 0 or i == len(daily) - 1:
            lbl = f"{d['date'][8:10]}/{d['date'][5:7]}"
            out.append(f'<text x="{x + bw / 2:.1f}" y="{pad_t + ph + 11}" text-anchor="middle" font-size="7" fill="{COLORS["muted"]}">{lbl}</text>')
    out.append("</svg>")
    return "".join(out)


def _legend(items) -> str:
    return '<div class="legend">' + "".join(f'<span><span class="dot" style="background:{c}"></span>{esc(t)}</span>' for c, t in items) + "</div>"


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------
def _summary(r: Dict[str, Any]) -> str:
    k, th, cov = r["kpis"], r["thresholds"], r["coverage"]
    cls, label = READY[r["readiness"]]
    tone = {"ok": "ok", "warn": "warn", "crit": "crit"}[r["readiness"]]
    pts = []
    if k["jobs"]:
        pts.append(f"<b>{vn(k['jobs'], 0)}</b> job backup hoàn tất trong {r['period']['days']} ngày: {vn(k['success'], 0)} thành công, "
                   f"{vn(k['partial'], 0)} thành công một phần, <b>{vn(k['failed'], 0)} lỗi</b>; dữ liệu đã backup {vn(k['dataTB'], 2)} TB.")
    if k["clients"]:
        pts.append(f"<b>{vn(k['clientsCrit'], 0)}</b> client cần xử lý ngay, {vn(k['clientsWarn'], 0)} client cần theo dõi trên tổng {vn(k['clients'], 0)} client; "
                   f"{vn(k['rpoCompliancePct'])}% client có bản backup thành công trong {vn(th['rpo_hours'], 0)} giờ.")
    sc = r.get("schedule") or {}
    if sc.get("compliancePct") is not None:
        bad = sum((x.get("counts") or {}).get(k, 0) for x in sc.get("rules", []) for k in ("failed", "missing"))
        pts.append(f"Tuân thủ lịch backup: <b>{vn(sc['compliancePct'])}%</b> đêm đạt ({sc['evaluated']} đêm có lịch); "
                   f"<b>{vn(bad, 0)}</b> đêm có client lỗi hoặc không chạy.")
    if r["failureGroups"]:
        g = r["failureGroups"][0]
        pts.append(f"Nguyên nhân lỗi nhiều nhất: <b>{esc(g['label'].lower())}</b> ({vn(g['count'], 0)} job, {vn(g['clientCount'], 0)} client).")
    if cov.get("available"):
        pts.append(f"Độ phủ backup VM{' (trong phạm vi cần backup)' if cov.get('scopeActive') else ''}: <b>{vn(cov['pct'])}%</b> VM đang chạy có bản backup thành công trong {th['coverage_days']} ngày; "
                   f"<b>{vn(cov['unprotected'], 0)} VM</b> chưa có trong backup.")
    if k["storageMaxPct"] is not None:
        pts.append(f"Disk pool dùng nhiều nhất: {esc(k['storageMaxName'])} {vn(k['storageMaxPct'], 0)}%.")
    if k["copyJobs"]:
        pts.append(f"Bản sao sang DR (duplication/replication): {vn(k['copyRate'])}% thành công ({vn(k['copyFailed'], 0)} job lỗi).")
    tiles = [
        ("Tỷ lệ thành công", f"{vn(k['successRate'])}%", f"mục tiêu {vn(th['success_target_pct'], 0)}% · {r['period']['days']} ngày"),
        ("24 giờ qua", f"{vn(k['successRate24h'])}%", f"{vn(k['jobs24h'], 0)} job"),
        ("Client đạt RPO", f"{vn(k['rpoCompliancePct'])}%", f"RPO {vn(th['rpo_hours'], 0)} giờ"),
        ("Độ phủ VM", f"{vn(cov.get('pct'))}%" if cov.get("available") else "—", f"{vn(cov.get('unprotected'), 0)} VM chưa backup" if cov.get("available") else "chưa có dữ liệu vCenter"),
        ("Dữ liệu backup", f"{vn(k['dataTB'], 2)} TB", f"24 giờ: {vn(k['dataTB24h'], 2)} TB"),
        ("Disk pool cao nhất", f"{vn(k['storageMaxPct'], 0)}%" if k["storageMaxPct"] is not None else "—", esc(k["storageMaxName"] or "")),
    ]
    tiles_html = "".join(f'<div class="stat-tile"><div class="stat-label">{a}</div><div class="stat-value">{b}</div><div class="stat-sub">{c}</div></div>' for a, b, c in tiles)
    top = [f for f in r["findings"] if f["severity"] == "critical"][:4]
    decide = ("<div class=\"exec-brief-decision\"><b>Ưu tiên xử lý:</b> " + "; ".join(esc(f["title"]) for f in top) + ".</div>") if top else ""
    return f"""
  <div class="exec-brief {tone}">
    <div class="exec-brief-head"><span class="exec-brief-title">Tóm tắt điều hành</span>{_pill(label, cls)}</div>
    <div class="exec-brief-headline">{esc(r['headline'])}</div>
    <ul class="brief-list">{''.join(f'<li>{p}</li>' for p in pts)}</ul>
    <div class="stat-row">{tiles_html}</div>
    {decide}
  </div>"""


def _daily(r: Dict[str, Any]) -> str:
    rows = [[f"{d['date'][8:10]}/{d['date'][5:7]}", vn(d["success"], 0), vn(d["partial"], 0),
             f'<b style="color:{COLORS["crit"]}">{vn(d["failed"], 0)}</b>' if d["failed"] else "0",
             vn((d["success"] + d["partial"]) * 100.0 / max(1, d["success"] + d["partial"] + d["failed"]), 1) + "%" if (d["success"] + d["partial"] + d["failed"]) else "—",
             vn(d["tb"], 2)] for d in r["daily"]]
    table = _table(["Ngày", "Thành công", "Một phần", "Lỗi", "Tỷ lệ", "Dữ liệu (TB)"], rows, cls="num-table") if len(rows) <= 14 else ""
    return f"""
  <div class="section">
    <div class="section-title"><span class="sec-num">1</span>Kết quả job backup theo ngày</div>
    <div class="section-sub">Đếm job backup ở cấp thực thi (job con của một lần chạy VMware / multistream không đếm trùng với job cha). Thành công một phần (status 1) được tính là thành công nhưng cần rà soát.</div>
    <div class="chart-card">{svg_daily_stack(r['daily'])}{_legend([(OUT_COLORS['success'], 'Thành công'), (OUT_COLORS['partial'], 'Thành công một phần'), (OUT_COLORS['failed'], 'Lỗi')])}</div>
    <div style="margin-top:6px;">{table}</div>
  </div>"""


NIGHT = {"ok": ("ok", "✓"), "partial": ("warn", "½"), "late": ("warn", "T"), "wrongtype": ("warn", "S"),
         "failed": ("crit", "✕"), "missing": ("crit", "!"), "running": ("na", "…"), "pending": ("na", "·")}
WD = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]
SRC_LABEL = {"netbackup": "Từ policy", "fixed": "Khung cố định", "manual": "Lịch thủ công"}


def _schedule(r: Dict[str, Any]) -> str:
    sc = r.get("schedule") or {}
    unmapped = [x["system"] for x in sc.get("rules") or [] if not x.get("policies")]
    rules = [x for x in sc.get("rules") or [] if x.get("policies")]
    if not rules:
        return """
  <div class="section"><div class="section-title"><span class="sec-num">2</span>Tuân thủ lịch backup</div>
  <div class="small-note" style="font-size:9px;">Chưa có lịch backup (không đọc được schedule của policy và chưa khai báo lịch thủ công).</div></div>"""
    import datetime as _dt
    nights = (sc.get("nights") or [])[-14:]
    idx = len(sc.get("nights") or []) - len(nights)
    head = "".join(f"<th style='text-align:center'>{WD[_dt.date.fromisoformat(d).weekday()]}<br/>{d[8:10]}/{d[5:7]}</th>" for d in nights)
    rows = []
    for x in rules:
        cells = ""
        for c in x["nights"][idx:]:
            if not c.get("status"):
                cells += f"<td style='text-align:center;color:{COLORS['faint']}'>{'?' if c.get('expected') else ''}</td>"
                continue
            cls, sym = NIGHT[c["status"]]
            fg, bg = {"ok": ("ok", "ok_bg"), "warn": ("warn", "warn_bg"), "crit": ("crit", "crit_bg"), "na": ("na", "na_bg")}[cls]
            border = "2px solid" if c.get("expected") == "full" else "1px solid"
            cells += (f"<td style='text-align:center;padding:2px'><span style='display:inline-block;width:15px;line-height:13px;border-radius:3px;"
                      f"font-weight:700;color:{COLORS[fg]};background:{COLORS[bg]};border:{border} {COLORS[fg]}'>{sym}</span></td>")
        full = ("Mỗi đêm" if x.get("source") == "fixed" else f"Full mỗi {x['fullEveryDays']} ngày" if x.get("fullEveryDays")
                else "Full " + (", ".join(WD[d] for d in x["fullDays"]) or "—"))
        pct = x.get("compliancePct")
        col = COLORS["ok"] if pct is not None and pct >= 95 else (COLORS["warn"] if pct is not None and pct >= 80 else COLORS["crit"])
        rows.append(f"<tr><td><b>{esc(x['system'])}</b><div style='font-size:7px;color:{COLORS['muted']}'>{SRC_LABEL.get(x.get('source'), 'Lịch thủ công')}</div></td>"
                    f"<td style='font-size:7.6px'>{full}<br/>{esc(x['startTime'])}→{esc(x['endTime'])}</td>{cells}"
                    f"<td style='text-align:right'><b style='color:{col}'>{vn(pct)}%</b></td></tr>" if pct is not None else
                    f"<tr><td><b>{esc(x['system'])}</b></td><td style='font-size:7.6px'>{full}<br/>{esc(x['startTime'])}→{esc(x['endTime'])}</td>{cells}<td>—</td></tr>")
    legend = " · ".join(f"{s} {l}" for s, l in [("✓", "Đạt"), ("½", "Một phần"), ("T", "Trễ"), ("S", "Sai loại (đêm Full không chạy Full)"),
                                                 ("✕", "Lỗi"), ("!", "Thiếu"), ("…", "Đang chạy"), ("·", "Chưa đến lịch")])
    return f"""
  <div class="section breakable">
    <div class="section-title"><span class="sec-num">2</span>Tuân thủ lịch backup</div>
    <div class="section-sub">Tỷ lệ đêm đạt: <b>{vn(sc.get('compliancePct'))}%</b> ({sc.get('evaluated', 0)} đêm có lịch). Mỗi đêm tính từ {sc.get('cutoffHour', 12)}:00 hôm đó đến trước {sc.get('cutoffHour', 12)}:00 hôm sau
    (job 20:00 → 04:00 thuộc đêm bắt đầu). Đạt = mọi client thành công, đúng loại Full / Incremental, xong trước giờ kết thúc + {sc.get('graceMinutes', 60)} phút. Ô viền đậm = đêm Full.</div>
    <table class="sched-grid"><thead><tr><th>Hệ thống / policy</th><th>Lịch</th>{head}<th style='text-align:right'>Tuân thủ</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
    <div class="small-note">{legend}</div>
    {f'<div class="small-note">Lịch chưa gán policy NetBackup (chưa kiểm tra): {esc(", ".join(unmapped))}.</div>' if unmapped else ''}
  </div>"""


def _failures(r: Dict[str, Any]) -> str:
    if not r["topCodes"]:
        return """
  <div class="section"><div class="section-title"><span class="sec-num">3</span>Nguyên nhân lỗi</div>
  <div class="small-note" style="font-size:9px;">Không có job lỗi trong kỳ.</div></div>"""
    bars = svg_hbars([{"label": g["short"], "value": g["count"], "color": COLORS["crit"] if i == 0 else COLORS["warn"],
                       "text": f"{g['count']} job · {g['clientCount']} client"} for i, g in enumerate(r["failureGroups"])], width=330, label_w=118)
    actions = "".join(f'<div class="rec-cell" style="margin-bottom:4px;"><div class="rec-label">{esc(g["label"])}</div>{esc(g["action"])}</div>'
                      for g in r["failureGroups"][:3])
    codes = _table(["Mã", "Mô tả", "Nhóm", "Số job", "Client"],
                   [[f"<b>{c['code']}</b>", esc(c["desc"]), esc(c["group"]), vn(c["count"], 0),
                     esc(", ".join(c["clients"][:4])) + (f" +{c['clientCount'] - 4}" if c["clientCount"] > 4 else "")] for c in r["topCodes"]],
                   widths=["7%", "33%", "20%", "9%", "31%"])
    return f"""
  <div class="section">
    <div class="section-title"><span class="sec-num">3</span>Nguyên nhân lỗi</div>
    <div class="section-sub">Mã lỗi backup được gom theo đầu mối xử lý để giao việc cho đúng bộ phận.</div>
    <div class="chart-row"><div class="chart-card"><div class="chart-card-title">Job lỗi theo nhóm nguyên nhân</div>{bars}</div>
      <div style="flex:1;font-size:8.6px;line-height:1.45;">{actions}</div></div>
    <div style="margin-top:8px;">{codes}</div>
  </div>"""


def _clients(r: Dict[str, Any]) -> str:
    th = r["thresholds"]
    bad = [c for c in r["clients"] if c["status"] != "ok"]
    rows = [[f"<b>{esc(c['client'])}</b>", esc(c.get("ip") or "—"), esc(", ".join(c["policies"][:2])), _t(c["lastSuccessAt"]), _hours(c["hoursSinceSuccess"]),
             vn(c["consecutiveFail"], 0) if c["consecutiveFail"] else "—", esc(c["lastStatusText"]),
             _pill(c["statusLabel"], c["status"])] for c in bad[:60]]
    more = f'<div class="small-note">Còn {len(bad) - 60} client khác — xem file Excel.</div>' if len(bad) > 60 else ""
    return f"""
  <div class="section breakable">
    <div class="section-title"><span class="sec-num">4</span>Client cần xử lý — RPO và lỗi lặp lại</div>
    <div class="section-sub">{vn(len(bad), 0)} / {vn(len(r['clients']), 0)} client chưa đạt. Quá RPO: không có bản backup thành công trong {vn(th['rpo_hours'], 0)} giờ (nghiêm trọng khi quá {vn(th['rpo_crit_hours'], 0)} giờ hoặc lỗi liên tiếp từ {th['consecutive_fail']} lần).</div>
    {_table(["Client", "IP", "Policy", "Thành công gần nhất", "Đã qua", "Lỗi liên tiếp", "Kết quả lần cuối", "Đánh giá"], rows,
            widths=["16%", "11%", "15%", "12%", "8%", "7%", "19%", "12%"])}
    {more}
  </div>"""


def _coverage(r: Dict[str, Any]) -> str:
    cov, th = r["coverage"], r["thresholds"]
    if not cov.get("available"):
        return """
  <div class="section"><div class="section-title"><span class="sec-num">5</span>Độ phủ backup máy ảo</div>
  <div class="small-note" style="font-size:9px;">Chưa có dữ liệu inventory vCenter để đối chiếu — đồng bộ vCenter trước khi xuất báo cáo.</div></div>"""
    scope_txt = f"<b>Phạm vi cần backup:</b> {esc(cov['scopeLabel'])}. " if cov.get("scopeActive") else ""
    out_txt = f", {vn(cov['outOfScope'], 0)} VM ngoài phạm vi" if cov.get("scopeActive") else ""
    donut = svg_donut([{"label": "Có backup", "value": cov["protected"], "color": OUT_COLORS["success"]},
                       {"label": "Có job nhưng lỗi", "value": cov["failing"], "color": OUT_COLORS["partial"]},
                       {"label": "Chưa backup", "value": cov["unprotected"], "color": OUT_COLORS["failed"]}],
                      size=118, center_label=f"{vn(cov['pct'], 0)}%", center_sub="có backup")
    leg = "".join(f'<span class="donut-leg"><span class="dot" style="background:{c}"></span>{t}: <b>{vn(v, 0)}</b></span>'
                  for c, t, v in [(OUT_COLORS["success"], "Có backup", cov["protected"]), (OUT_COLORS["partial"], "Có job nhưng lỗi", cov["failing"]),
                                  (OUT_COLORS["failed"], "Chưa backup", cov["unprotected"])])
    rows = [[esc(c["vcenterKey"]), esc(c["cluster"]), vn(c["total"], 0), vn(c["protected"], 0), vn(c["failing"], 0),
             f'<b style="color:{COLORS["crit"]}">{vn(c["unprotected"], 0)}</b>' if c["unprotected"] else "0", vn(c["pct"]) + "%"] for c in cov["byCluster"]]
    return f"""
  <div class="section">
    <div class="section-title"><span class="sec-num">5</span>Độ phủ backup máy ảo</div>
    <div class="section-sub">{scope_txt}Đối chiếu {vn(cov['total'], 0)} VM đang chạy {'trong phạm vi' if cov.get('scopeActive') else 'trong inventory vCenter'} với client NetBackup (theo tên VM, hostname guest hoặc IP).
    Có backup = thành công trong {th['coverage_days']} ngày. Không tính {vn(cov['poweredOff'], 0)} VM đang tắt và {vn(cov['excluded'], 0)} VM loại trừ theo tên{out_txt}.</div>
    <div class="chart-row">
      <div class="chart-card" style="flex:0 0 34%;display:flex;gap:8px;align-items:center;">{donut}<div class="donut-legend">{leg}</div></div>
      <div style="flex:1;">{_table(["vCenter", "Cluster", "VM", "Có backup", "Lỗi", "Chưa backup", "Độ phủ"], rows)}</div>
    </div>
    <div class="small-note">Danh sách VM chưa backup: Phụ lục B.</div>
  </div>"""


def _storage(r: Dict[str, Any]) -> str:
    th = r["thresholds"]
    rows = [[f"<b>{esc(s['name'])}</b>", esc(s["type"] or "—"), esc(s["state"] or "—"), vn(s["totalTB"], 1), vn(s["usedTB"], 1), vn(s["freeTB"], 1),
             vn(s["usedPct"], 1) + "%" if s["usedPct"] is not None else "—",
             (("+" if (s["growthTBPerDay"] or 0) >= 0 else "") + vn(s["growthTBPerDay"], 2)) if s["growthTBPerDay"] is not None else "—",
             ("> 1 năm" if s["daysToFull"] > 365 else vn(s["daysToFull"], 0)) if s["daysToFull"] is not None else "—", _pill(s["statusLabel"], s["status"])] for s in r["storage"]]
    tr = r["storageTrend"]
    chart = ""
    if len(tr["dates"]) >= 2:
        series = [{"name": s["name"], "color": POOL_COLORS[i % len(POOL_COLORS)], "values": s["values"]} for i, s in enumerate(tr["series"])]
        chart = (f'<div class="chart-card" style="margin-top:8px;"><div class="chart-card-title">Mức dùng disk pool 30 ngày (%)</div>'
                 f'{svg_line_chart(tr["dates"], series, width=690, height=170, threshold=th["storage_crit_pct"], threshold_label="Nghiêm trọng", unit="%")}'
                 f'{_legend([(x["color"], x["name"]) for x in series])}</div>')
    return f"""
  <div class="section">
    <div class="section-title"><span class="sec-num">6</span>Dung lượng lưu trữ backup</div>
    <div class="section-sub">Cảnh báo từ {vn(th['storage_warn_pct'], 0)}%, nghiêm trọng từ {vn(th['storage_crit_pct'], 0)}% hoặc khi dự kiến chạm ngưỡng high-water trong {th['storage_days_warn']} ngày (xu hướng tuyến tính 30 ngày).</div>
    {_table(["Disk pool", "Loại", "Trạng thái", "Tổng (TB)", "Đã dùng", "Còn trống", "% dùng", "TB/ngày", "Ngày tới HWM", "Đánh giá"], rows)}
    {chart}
  </div>"""


def _size_gb(gb: Optional[float]) -> str:
    """GB → "512,4 GB" / "1,2 TB" (vi-VN, same as the web)."""
    if gb is None:
        return "—"
    return f"{vn(gb / 1024, 1)} TB" if gb >= 1024 else f"{vn(gb, 1)} GB"


def _size_bar(v: float, mx: float) -> str:
    """Relative bar against the largest row of the table (series colour: a size, not a status)."""
    w = max(2.0, min(100.0, v * 100.0 / mx)) if mx else 0
    return (f'<div style="height:4px;margin-top:2px;background:{COLORS["track_bg"]};border-radius:2px;">'
            f'<div style="width:{w:.1f}%;height:4px;background:{COLORS["series1"]};border-radius:2px;"></div></div>')


def _sizes(r: Dict[str, Any], top: int = 10) -> str:
    z = r.get("sizeStats") or {}
    by_size, by_total = (z.get("bySize") or [])[:top], (z.get("byTotal") or [])[:top]
    if not by_size:
        return """
  <div class="section"><div class="section-title"><span class="sec-num">7</span>Máy chủ dung lượng lớn nhất &amp; backup nhiều nhất</div>
  <div class="small-note" style="font-size:9px;">Chưa có job backup thành công trong kỳ.</div></div>"""
    mx_s = max(x["sizeGB"] or 0 for x in by_size)
    mx_t = max(x["totalGB"] or 0 for x in by_total) if by_total else 0
    name = lambda x: f"<b>{esc(x['vm'] or x['client'])}</b>" + (f'<div class="small-note">client {esc(x["client"])}</div>'
                                                             if x["vm"] and x["vm"].lower() != str(x["client"]).lower() else "")
    rows1 = [[vn(i + 1, 0), name(x), f"<b>{_size_gb(x['sizeGB'])}</b>{_size_bar(x['sizeGB'] or 0, mx_s)}",
              f"Full {_t(x['sizeAt'])}" if x["sizeBasis"] == "full" else "Job lớn nhất",
              f"{_size_gb(x['vmDiskUsedGB'])} / {_size_gb(x['vmDiskProvGB'])}" if x["vmDiskUsedGB"] is not None else "—",
              esc(", ".join(x["policies"][:2]) or "—")] for i, x in enumerate(by_size)]
    rows2 = [[vn(i + 1, 0), name(x), f"<b>{_size_gb(x['totalGB'])}</b>{_size_bar(x['totalGB'] or 0, mx_t)}", vn(x["jobs"], 0),
              f"{_size_gb(x['largestJob']['gb'])} · {esc(x['largestJob']['schedule'] or '—')} · {_t(x['largestJob']['startTime'])}"]
             for i, x in enumerate(by_total)]
    days = r["period"]["days"]
    return f"""
  <div class="section breakable">
    <div class="section-title"><span class="sec-num">7</span>Máy chủ dung lượng lớn nhất &amp; backup nhiều nhất</div>
    <div class="section-sub">{vn(z.get('clients'), 0)} máy chủ có backup thành công trong {days} ngày, tổng <b>{_size_gb(z.get('totalGB'))}</b>;
    10 máy chủ lớn nhất chiếm <b>{vn(z.get('top10SharePct'))}%</b>. Dung lượng = bản Full thành công gần nhất trong kỳ (ước tính dữ liệu cần backup);
    chưa có Full thì lấy job thành công lớn nhất. Đĩa VM lấy từ vCenter khi khớp tên / IP.</div>
    <div class="chart-card-title" style="margin-top:4px;">Top {len(by_size)} máy chủ theo dung lượng</div>
    {_table(["#", "Máy chủ", "Dung lượng", "Căn cứ", "Đĩa VM dùng / cấp", "Policy"], rows1, widths=["5%", "24%", "18%", "15%", "18%", "20%"])}
    <div class="chart-card-title" style="margin-top:8px;">Top {len(by_total)} máy chủ theo dữ liệu đã backup trong kỳ</div>
    {_table(["#", "Máy chủ", "Đã backup", "Số job", "Job lớn nhất"], rows2, widths=["5%", "30%", "20%", "9%", "36%"])}
    <div class="small-note">Danh sách đầy đủ (top 50): sheet “Dung lượng” trong file Excel.</div>
  </div>"""


def _policies(r: Dict[str, Any]) -> str:
    rows = [[f"<b>{esc(p['name'])}</b>", esc(p["policyType"] or "—"), "Bật" if p["active"] else ("Tắt" if p["active"] is False else "—"),
             vn(p["clients"], 0), _t(p["lastRunAt"]), vn(p["jobs"], 0), vn(p["failed"], 0) if p["failed"] else "0",
             (vn(p["successRate"]) + "%") if p["successRate"] is not None else "—", _pill(p["statusLabel"], p["status"])] for p in r["policies"]]
    return f"""
  <div class="section breakable">
    <div class="section-title"><span class="sec-num">8</span>Policy backup</div>
    <div class="section-sub">Policy đang bật mà không có job trong 48 giờ được đánh giá “Không chạy”. Số client của policy VMware lấy theo VM thực tế được backup (client chọn theo query).</div>
    {_table(["Policy", "Loại", "Hoạt động", "Client", "Chạy gần nhất", "Job", "Lỗi", "Tỷ lệ", "Đánh giá"], rows)}
  </div>"""


def _findings(r: Dict[str, Any]) -> str:
    rows = [[_pill(SEV_LABEL[f["severity"]], SEV_CLS[f["severity"]]), esc(f["area"]),
             f"<b>{esc(f['title'])}</b>" + (f'<div class="decision-action">{esc(f["detail"])}</div>' if f["detail"] else ""),
             esc(f["action"])] for f in r["findings"]]
    return f"""
  <div class="section breakable">
    <div class="section-title"><span class="sec-num">9</span>Phát hiện &amp; khuyến nghị</div>
    {_table(["Mức độ", "Hạng mục", "Phát hiện", "Khuyến nghị"], rows, cls="decision-table", widths=["12%", "13%", "40%", "35%"])}
  </div>"""


def _criteria(r: Dict[str, Any], servers: List[Dict[str, Any]]) -> str:
    th = r["thresholds"]
    win = f"{th.get('fixed_window_start', '20:00')}–{th.get('fixed_window_end', '04:00')}"
    if th.get("schedule_mode") == "fixed":
        src = f"khung cố định {win} mỗi đêm cho mọi policy"
    else:
        src = f"đọc từ schedule của policy NetBackup (policy không suy ra được lịch: khung {win} mỗi đêm)"
    if any(x.get("source") == "manual" for x in (r.get("schedule") or {}).get("rules", [])):
        src += ", lịch thủ công / Excel được ưu tiên cho policy đã gán"
    items = [
        f"Tỷ lệ thành công = (thành công + thành công một phần) / job backup đã kết thúc trong kỳ; mục tiêu ≥ {vn(th['success_target_pct'], 0)}%, nghiêm trọng khi dưới {vn(th['success_crit_pct'], 0)}%.",
        f"RPO client theo lịch của policy (mỗi đêm ≈ 1 ngày + cửa sổ + ân hạn); policy không có lịch: ≤ {vn(th['rpo_hours'], 0)} giờ. Nghiêm trọng khi quá thêm {vn(th['rpo_crit_hours'] - th['rpo_hours'], 0)} giờ hoặc lỗi liên tiếp ≥ {th['consecutive_fail']} lần.",
        f"Job chạy quá {vn(th['long_running_hours'], 0)} giờ hoặc chờ quá {vn(th['queued_hours'], 0)} giờ.",
        f"Lịch backup: {src}; "
        f"mỗi đêm tính từ {th['backup_day_cutoff_hour']}:00 đến trước {th['backup_day_cutoff_hour']}:00 hôm sau; trễ khi xong sau giờ kết thúc + {th['schedule_grace_minutes']} phút; "
        f"RPO của client theo lịch hệ thống (hằng đêm ≈ 1 ngày + cửa sổ + ân hạn).",
        f"Disk pool: cảnh báo {vn(th['storage_warn_pct'], 0)}%, nghiêm trọng {vn(th['storage_crit_pct'], 0)}% hoặc chạm high-water trong {th['storage_days_warn']} ngày.",
        f"Độ phủ VM: VM đang chạy có bản backup thành công trong {th['coverage_days']} ngày; loại trừ VM có tên chứa “{esc(th['coverage_exclude'])}”"
        + (f"; chỉ tính hệ thống trong phạm vi cần backup: {esc(r['coverage'].get('scopeLabel'))}." if r["coverage"].get("scopeActive") else "."),
    ]
    src = "; ".join(f"{esc(s['name'])} ({esc(s['host'])}) — đồng bộ {_t(s['lastSyncAt'], '%H:%M %d/%m/%Y')}" + (" · dữ liệu demo" if s.get("demo") else "")
                    for s in servers) or "—"
    return f"""
  <div class="section">
    <div class="section-title"><span class="sec-num">10</span>Tiêu chí đánh giá &amp; nguồn dữ liệu</div>
    <ul class="brief-list">{''.join(f'<li>{x}</li>' for x in items)}</ul>
    <div class="assumption-note">Nguồn: REST API của hệ thống backup qua tài khoản chỉ đọc — {src}. Inventory VM: lần đồng bộ vCenter gần nhất.</div>
  </div>"""


def _appendix(r: Dict[str, Any]) -> str:
    jobs = r["failedJobs"][:300]
    jrows = [[esc(str(j["jobId"])), _t(j["startTime"]), esc(j["client"] or "—"), esc(j.get("ip") or "—"), esc(j["policy"] or "—"),
              esc(j["schedule"] or "—"),
              f"<b>{j['status']}</b>", esc(j["statusText"])] for j in jobs]
    cov = r["coverage"]
    vrows = [[esc(v["vm"]), esc(v["vcenterKey"] or "—"), esc(v["cluster"] or "—"), esc(v.get("ip") or "—"), vn(v.get("vcpu"), 0),
              vn(v.get("ramGB"), 0), vn(v.get("diskGB"), 0)] for v in (cov.get("unprotectedVms") or [])[:400]] if cov.get("available") else []
    return f"""
  <div class="section breakable" style="page-break-before:always;">
    <div class="section-title">Phụ lục A. Job backup lỗi trong kỳ ({vn(len(r['failedJobs']), 0)} job{', hiển thị 300 job gần nhất' if len(r['failedJobs']) > 300 else ''})</div>
    {_table(["Job ID", "Bắt đầu", "Client", "IP", "Policy", "Lịch", "Mã", "Mô tả"], jrows, cls="appendix-table",
            widths=["8%", "10%", "17%", "11%", "16%", "12%", "6%", "20%"])}
  </div>
  <div class="section breakable">
    <div class="section-title">Phụ lục B. VM đang chạy chưa có backup ({vn(len(cov.get('unprotectedVms') or []), 0)} VM)</div>
    {_table(["VM", "vCenter", "Cluster", "IP", "vCPU", "RAM (GB)", "Disk (GB)"], vrows, cls="appendix-table")}
  </div>"""


def _css() -> str:
    return f"""
.sec-num {{ display:inline-block; min-width:16px; height:16px; line-height:16px; border-radius:3px; background:{COLORS['primary']}; color:#fff; font-size:9px; text-align:center; margin-right:6px; padding:0 3px; }}
.chart-row {{ display:flex; gap:12px; align-items:flex-start; }}
.num-table td:not(:first-child), .num-table th:not(:first-child) {{ text-align:right; }}
.rec-cell {{ background:{COLORS['surface']}; border:1px solid {COLORS['divider']}; border-radius:5px; padding:5px 7px; }}
.rec-label {{ font-size:7.4px; font-weight:700;   color:{COLORS['muted']}; margin-bottom:2px; }}
.decision-action {{ color:{COLORS['muted']}; font-size:7.8px; margin-top:1px; line-height:1.4; }}
.appendix-table th, .appendix-table td {{ font-size:7.6px; padding:3px 5px; }}
.appendix-table td {{ overflow-wrap:anywhere; }}
.meta-row {{ margin-bottom:8px; }}
.sched-grid th, .sched-grid td {{ font-size:7.6px; padding:3px 3px; }}
"""


def render_backup_html(r: Dict[str, Any], servers: List[Dict[str, Any]], scope_label: str,
                       logo_data_url: Optional[str] = None, appendix: bool = True) -> str:
    generated = _now_str()
    logo = f'<img class="cover-logo" src="{logo_data_url}" alt="logo"/>' if logo_data_url else ""
    p = r["period"]
    body = (_summary(r) + _daily(r) + _schedule(r) + _failures(r) + _clients(r) + _coverage(r) + _storage(r) + _sizes(r) + _policies(r)
            + _findings(r) + _criteria(r, servers) + (_appendix(r) if appendix else ""))
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><style>{_base_css('Báo cáo hiện trạng backup', generated)}{_exec_visual_css()}{_css()}</style></head>
<body>
  <div class="cover-header">
    <div>
      <div class="eyebrow">Infrastructure Capacity Ops · Backup · {esc(scope_label)} · {esc(generated.split(' ')[-1])}</div>
      <div class="cover-title" style="margin-bottom:6px;">Báo cáo hiện trạng Backup</div>
      <div class="meta-row"><span>Kỳ báo cáo: <b>{_t(p['from'], '%d/%m/%Y')} – {_t(p['to'], '%d/%m/%Y')}</b> ({p['days']} ngày)</span>
        <span>Phạm vi: <b>{esc(scope_label)}</b></span><span>Lập lúc: <b>{esc(generated)}</b></span></div>
    </div>
    {logo}
  </div>
  {body}
</body></html>"""


def build_backup_pdf(*args, **kwargs) -> bytes:
    return html_to_pdf_bytes(render_backup_html(*args, **kwargs))
