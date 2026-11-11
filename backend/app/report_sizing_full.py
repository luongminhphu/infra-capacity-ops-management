"""Full host-sizing consulting report ("Đề xuất cấu hình máy chủ") — PDF template.

Structure (short, plain headings):
  Trang bìa (thông tin tài liệu, mục lục, lịch sử phiên bản, ký duyệt)
  1 Tóm tắt · 2 Phạm vi & dữ liệu đầu vào · [Hạ tầng hiện hữu] · 3 Nhu cầu tài nguyên
  4 So sánh phương án · 5 Cấu hình đề xuất · 6 Khả năng mở rộng · 7 Rủi ro & khuyến nghị
  8 Kế hoạch triển khai · 9 Giả định & cách tính
  Phụ lục A. Danh sách máy ảo · B. Thuật ngữ

Conventions: one vocabulary ("host" after first mention, "tỷ lệ chia sẻ", "vùng an toàn",
"yếu tố quyết định", "luồng CPU (thread)"), Vietnamese number format (1.234,5),
binary capacity units (1 TiB = 1 024 GB). Layout: one page margin declared in @page,
every block full-width with no extra side margins; charts are inline SVG and the
capacity bar is an HTML table (WeasyPrint lays tables out more predictably than flex).
"""
import math
import re
from typing import Any, Dict, List, Optional

from . import platforms, sizing
from .report_pdf import (COLORS, STATUS_COLOR, _base_css, _exec_visual_css, _now_str, esc, html_to_pdf_bytes,
                         svg_bar_chart, svg_donut, svg_gauge)
from .report_planning import _planning_css
from .report_sizing import _existing_table, _pick_target, _reuse_summary_box, _summary_box, _target_detail
from .report_sizing_charts import (COST_COLORS, SERIES_COLORS, money_short, svg_cost_bars, svg_dual_hbars, svg_lines, svg_projection,
                                   svg_waterfall, svg_yearly_tco)

# ---------------------------------------------------------------------------
# Vietnamese number formatting
# ---------------------------------------------------------------------------
_NUM_RE = re.compile(r"(?<![\d.,\w])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+)(?![\d])")


def vn(text: Any) -> str:
    """Swap English separators inside numbers of an engine-generated sentence: 12,100.5 → 12.100,5."""
    if text is None:
        return ""
    return _NUM_RE.sub(lambda m: m.group(1).replace(",", "§").replace(".", ",").replace("§", "."), str(text))


def n(v: Optional[float], d: int = 0) -> str:
    if v is None or not isinstance(v, (int, float)) or not math.isfinite(v):
        return "—"
    return f"{v:,.{d}f}".replace(",", "§").replace(".", ",").replace("§", ".")


def pct(v: Optional[float], d: int = 0) -> str:
    return "—" if v is None else f"{n(v, d)}%"


def cap(gb: Optional[float], d: int = 1) -> str:
    """Capacity: GB as entered (= GiB in the hypervisor) below 1 024, TiB above."""
    if gb is None:
        return "—"
    return f"{n(gb / 1024, d)} TiB" if gb >= 1024 else f"{n(gb, 0)} GB"


def ram(gb: Optional[float]) -> str:
    if gb is None:
        return "—"
    return f"{n(gb / 1024, 1 if gb % 1024 else 0)} TB" if gb >= 1024 else f"{n(gb, 0)} GB"


def ratio(v: Optional[float], d: int = 2) -> str:
    return "—" if v is None else f"{n(v, d)} : 1"


def _pill(text: str, cls: str) -> str:
    fg, bg = STATUS_COLOR.get(cls, STATUS_COLOR["na"])
    return f'<span class="pill" style="color:{COLORS[fg]};background:{COLORS[bg]};border-color:{COLORS[fg]}55;">{esc(text)}</span>'


SEV_PILL = {"critical": ("crit", "Nghiêm trọng"), "warning": ("warn", "Cảnh báo"), "info": ("na", "Lưu ý"), "ok": ("ok", "Đạt")}
READINESS_TXT = {"ok": ("ok", "SẴN SÀNG TRIỂN KHAI"), "warn": ("warn", "TRIỂN KHAI ĐƯỢC · CẦN LƯU Ý"), "crit": ("crit", "CẦN XỬ LÝ RỦI RO")}

DEFAULT_OUT_OF_SCOPE = [
    "Thiết bị mạng vật lý (switch, firewall, cáp quang) và thiết kế mạng chi tiết.",
    "Giải pháp sao lưu / khôi phục thảm hoạ (DR) và lưu trữ bản sao ngoài cụm.",
    "License hệ điều hành và ứng dụng chạy trong VM (Windows Server, SQL Server, phần mềm ứng dụng…).",
    "Di chuyển (migration) VM và dữ liệu từ hệ thống hiện tại.",
    "Đánh giá hiệu năng lưu trữ chi tiết (IOPS, độ trễ) và kiểm thử tải ứng dụng.",
    "Rà soát bảo mật, hardening và tuân thủ.",
]

GLOSSARY = [
    ("vCPU", "CPU ảo cấp cho một VM. Một vCPU chạy trên một luồng CPU (thread) của host tại một thời điểm."),
    ("Core", "Nhân xử lý vật lý của CPU. Nhiều nền tảng tính license theo core, thường có số core tối thiểu mỗi CPU."),
    ("Thread (luồng CPU)", "Với Hyper-Threading, mỗi core chạy 2 luồng; host 64 core có 128 thread."),
    ("Tỷ lệ chia sẻ (overcommit)", "Số vCPU (hoặc GB RAM ảo) cấp ra trên mỗi đơn vị tài nguyên vật lý. Ví dụ 2 : 1 là 2 vCPU trên 1 core."),
    ("N+1", "Có thêm 1 host dự phòng: khi 1 host hỏng hoặc bảo trì, các host còn lại vẫn chạy đủ toàn bộ VM."),
    ("Vùng an toàn", "Phần năng lực để trống trên mỗi host (ví dụ 20%) cho tải đột biến và vận hành."),
    ("Yếu tố quyết định", "Tài nguyên (CPU hoặc RAM) hết trước, quyết định số host cần mua."),
    ("NUMA", "Mỗi CPU có bộ nhớ gắn trực tiếp. VM lớn hơn một CPU phải dùng bộ nhớ của CPU kia, chậm hơn."),
    ("Hypervisor", "Phần mềm ảo hoá chạy trực tiếp trên host, tạo và chạy các VM."),
    ("HA (sẵn sàng cao)", "Tự khởi động lại VM trên host khác khi một host hỏng. Cần lưu trữ dùng chung (HCI hoặc SAN)."),
    ("Cân bằng tải tự động", "Tự dàn VM giữa các host và áp dụng quy tắc bố trí (ví dụ tách 2 Domain Controller ra 2 host)."),
    ("Di chuyển VM trực tuyến", "Chuyển VM đang chạy sang host khác không gián đoạn, dùng khi bảo trì host."),
    ("Lưu trữ HCI", "Gộp ổ đĩa trên các host thành một kho lưu trữ dùng chung, có bản sao dữ liệu giữa các host."),
    ("TiB", "Đơn vị dung lượng nhị phân: 1 TiB = 1.024 GB (GiB). Dung lượng thô là trước khi trừ phần bảo vệ dữ liệu."),
    ("TCO", "Tổng chi phí sở hữu trong kỳ: phần cứng + license (và các chi phí khác nếu có)."),
]


# ---------------------------------------------------------------------------
# Small building blocks
# ---------------------------------------------------------------------------
def _legend(items: List[tuple]) -> str:
    return '<div class="legend">' + "".join(
        f'<span><span class="dot" style="background:{c};"></span>{esc(t)}</span>' for c, t in items) + "</div>"


def _section(sec_id: str, num: str, title: str, sub: str, body: str, cls: str = "") -> str:
    sub_html = f'<div class="section-desc">{sub}</div>' if sub else ""
    num_html = f'<span class="sec-num">{esc(num)}</span>' if num else ""
    return f'<div class="section {cls}" id="{sec_id}"><div class="section-title">{num_html}{esc(title)}</div>{sub_html}{body}</div>'


def _kv_table(rows: List[tuple], cls: str = "kv-table", key_w: str = "34%") -> str:
    body = "".join(f'<tr><th style="width:{key_w};">{esc(k)}</th><td>{v}</td></tr>' for k, v in rows)
    return f'<table class="{cls}">{body}</table>'


def _table(header: List[str], rows: List[List[str]], cls: str = "", aligns: Optional[List[str]] = None,
           widths: Optional[List[str]] = None) -> str:
    aligns = aligns or ["left"] * len(header)
    th = "".join(f'<th style="text-align:{a};{("width:" + w + ";") if w else ""}">{h}</th>'
                 for h, a, w in zip(header, aligns, widths or [""] * len(header)))
    body = ""
    for r in rows:
        style = ""
        if isinstance(r, dict):
            style, r = r.get("style", ""), r["cells"]
        body += f'<tr{style}>' + "".join(f'<td style="text-align:{a};">{c}</td>' for c, a in zip(r, aligns)) + "</tr>"
    return f'<table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>'


def _clean_note(name: str, note: str) -> str:
    """Drop notes that only repeat the VM name ("EWS VM: …" → "…", "EWS VM" → "")."""
    nt = (note or "").strip()
    nm = (name or "").strip()
    if not nt:
        return ""
    if nm and nt.lower().startswith(nm.lower()):
        nt = nt[len(nm):].lstrip(" :-–—·").strip()
    return nt


# ---------------------------------------------------------------------------
# Cover
# ---------------------------------------------------------------------------
TOC_BASE = [("sec-summary", "1", "Tóm tắt"), ("sec-scope", "2", "Phạm vi & dữ liệu đầu vào"),
            ("sec-demand", "3", "Nhu cầu tài nguyên"), ("sec-options", "4", "So sánh phương án"),
            ("sec-design", "5", "Cấu hình đề xuất"), ("sec-scale", "6", "Khả năng mở rộng"),
            ("sec-risks", "7", "Rủi ro & khuyến nghị"), ("sec-plan", "8", "Kế hoạch triển khai"),
            ("sec-method", "9", "Giả định & cách tính"), ("app-vms", "A", "Phụ lục A. Danh sách máy ảo"),
            ("app-glossary", "B", "Phụ lục B. Thuật ngữ")]


def _cover(result, best, params, doc: Dict[str, str], logo_html: str, toc: List[tuple]) -> str:
    a = result["analysis"]
    cls, txt = READINESS_TXT.get(a.get("readiness") or "ok")
    fg, bg = STATUS_COLOR[cls]
    env = params.environment.strip()
    customer = doc.get("customer") or ""
    meta = [("Khách hàng", customer or "—"), ("Đầu mối khách hàng", doc.get("contact") or "—"),
            ("Dự án / hệ thống", env or "—"), ("Mã tài liệu", doc.get("code") or "—"),
            ("Phiên bản", doc.get("version") or "1.0"), ("Ngày lập", doc.get("date") or "—"),
            ("Người lập", doc.get("preparedBy") or "—"), ("Người duyệt", doc.get("approvedBy") or "—"),
            ("Đơn vị tư vấn", doc.get("org") or "—")]
    meta_html = "".join(f"<tr><td>{esc(k)}</td><td><b>{esc(v)}</b></td></tr>" for k, v in meta)
    toc_html = "".join(f'<li><a href="#{i}"><span class="toc-num">{esc(nm) if nm.isdigit() else ""}</span>{esc(t)}</a></li>' for i, nm, t in toc)
    hist = (f'<tr><td>{esc(doc.get("version") or "1.0")}</td><td>{esc(doc.get("date") or "—")}</td>'
            f'<td>{esc(doc.get("versionNote") or "Phát hành lần đầu")}</td><td>{esc(doc.get("preparedBy") or "—")}</td></tr>')
    sign = "".join(f'<td><div class="sign-role">{esc(r)}</div><div class="sign-space"></div><div class="sign-name">{esc(nm or "")}</div></td>'
                   for r, nm in (("Người lập", doc.get("preparedBy")), ("Người duyệt", doc.get("approvedBy")),
                                 ("Đại diện khách hàng", doc.get("contact"))))
    return f"""
<div class="cover-page">
  <div class="cv-top">
    <div class="cv-brand">{esc(doc.get("org") or "")}</div>
    {logo_html}
  </div>
  <div class="cv-main">
    <div class="eyebrow">Đề xuất giải pháp · Hạ tầng ảo hoá{(" " + esc(platforms.get(params)["label"])) if getattr(params, "platform", "vmware") != "generic" else ""}</div>
    <div class="cv-title">Đề xuất cấu hình máy chủ</div>
    <div class="cv-project">{esc(" – ".join(x for x in (env, customer) if x))}</div>
    <div class="cv-sub">{esc(vn(result['recommendation']['headline'])) if best else 'Chưa có VM trong danh sách yêu cầu.'}</div>
    <span class="pill" style="color:{COLORS[fg]};background:{COLORS[bg]};border-color:{COLORS[fg]}55;font-size:9px;">{txt}</span>
  </div>
  <table class="cv-grid"><tr>
    <td style="width:48%;"><div class="cv-h">Thông tin tài liệu</div><table class="cv-meta">{meta_html}</table></td>
    <td style="width:4%;"></td>
    <td style="width:48%;"><div class="cv-h">Nội dung</div><ol class="toc">{toc_html}</ol></td>
  </tr></table>
  <div class="cv-bottom">
    <div class="cv-h">Lịch sử phiên bản</div>
    <table class="cv-hist"><thead><tr><th style="width:14%;">Phiên bản</th><th style="width:18%;">Ngày</th><th>Nội dung thay đổi</th><th style="width:24%;">Người thực hiện</th></tr></thead><tbody>{hist}</tbody></table>
    <div class="cv-h" style="margin-top:8px;">Ký duyệt</div>
    <table class="cv-sign"><tr>{sign}</tr></table>
  </div>
</div>"""


# ---------------------------------------------------------------------------
# Section bodies
# ---------------------------------------------------------------------------
def _summary(result, best, existing, currency, mode, target, include_new, include_existing, project, opts: Optional[Dict[str, Any]] = None) -> str:
    opts = opts or {}
    a = result["analysis"]
    fc = a.get("findingCounts") or {}
    box = (_reuse_summary_box(result, best, existing, target, include_new, currency, project, si=True) if mode == "reuse"
           else _summary_box(result, best, existing if include_existing else [], currency, si=True))
    box = _vn_html(box)
    why = result["recommendation"].get("why") or ""
    if opts.get("selectionNote"):
        why = (why.rstrip(".") + ". " if why else "") + "Lý do: " + opts["selectionNote"].strip()
    why_html = f'<div class="callout" style="margin-top:8px;"><b>Lý do chọn phương án.</b> {esc(vn(why[len("Lý do chọn: "):] if why.startswith("Lý do chọn: ") else why))}</div>' if why else ""
    risk_line = (f'<div class="note-line">Rủi ro &amp; lưu ý: <b>{fc.get("critical", 0)} nghiêm trọng</b> · <b>{fc.get("warning", 0)} cảnh báo</b> · '
                 f'{fc.get("info", 0)} lưu ý' + (f' — chi tiết tại mục {opts["risksNum"]}.' if opts.get("risksNum") else '.') + '</div>')
    note = (opts.get("note") or "").strip()
    note_html = ("<div class=\"callout note\" style=\"margin-top:8px;\"><b>Ghi chú của người lập.</b> "
                 + "<br/>".join(esc(x) for x in note.splitlines() if x.strip()) + "</div>") if note else ""
    return box + why_html + note_html + risk_line


_TEXT_NODE_RE = re.compile(r">([^<>]+)<")


def _vn_html(html: str) -> str:
    """Apply `vn` to text nodes only (never to CSS / attributes)."""
    return _TEXT_NODE_RE.sub(lambda m: ">" + vn(m.group(1)) + "<", html)


def _scope(result, params, doc: Dict[str, Any]) -> str:
    d = result["demand"]
    wl = result["analysis"]["workload"]
    out_scope = [x for x in (doc.get("outOfScope") or []) if str(x).strip()] or DEFAULT_OUT_OF_SCOPE
    pf = platforms.get(params)
    in_scope = ["Tính số host và cấu hình phần cứng cho cụm ảo hoá (CPU, RAM).",
                f"Dung lượng lưu trữ cần cấp ({pf['hci_long'] if params.storage_mode == 'local' else 'SAN dùng chung'}).",
                f"Ước tính license ({'theo CPU socket' if pf['license_unit'] == 'socket' else 'theo core'}), kiến trúc logic và kế hoạch triển khai."]
    rows = [
        ("Khách hàng", esc((doc.get("customer") or "—") + (f" (đầu mối: {doc['contact']})" if doc.get("contact") else ""))),
        ("Hệ thống", esc(params.environment or "—")),
        ("Nền tảng ảo hoá", esc(pf["label"]) + ("" if params.platform != "generic" else " — tên sản phẩm cụ thể chốt khi chọn nền tảng")),
        ("Nguồn dữ liệu", esc(doc.get("inputSource") or "Danh sách máy ảo do khách hàng cung cấp")),
        ("Ngày nhận dữ liệu", esc(doc.get("inputDate") or "—")),
        ("Dữ liệu đầu vào", esc(f"{wl['groupCount']} nhóm, {n(d['vmCount'])} VM — {n(d['vcpu'])} vCPU, {n(d['ramGB'])} GB RAM, "
                                f"{cap(d['diskGB'])} dữ liệu (Phụ lục A)")),
        ("Trong phạm vi", "<ul class='tight'>" + "".join(f"<li>{esc(x)}</li>" for x in in_scope) + "</ul>"),
        ("Ngoài phạm vi", "<ul class='tight'>" + "".join(f"<li>{esc(x)}</li>" for x in out_scope) + "</ul>"),
        ("Quy ước", f"Máy chủ vật lý chạy hypervisor ({esc(pf['hypervisor'])}) gọi là <b>host</b>. Số liệu dùng dấu chấm phân cách hàng nghìn, dấu phẩy thập phân. "
                    "Dung lượng theo hệ nhị phân: 1 TiB = 1.024 GB. Thuật ngữ: xem Phụ lục B."),
    ]
    return _kv_table(rows, key_w="24%")


def _demand(result, params, best) -> str:
    d, p, a = result["demand"], result["params"], result["analysis"]
    wl = a["workload"]
    g = p["growth_pct"]
    so = 1 + p["storage_overhead_pct"] / 100.0
    prot = d["storageProtectionFactor"]
    usable = d["diskGB"] * d["growthFactor"] * so
    rows = [
        ["CPU", f"{n(d['vcpu'])} vCPU", f"{n(d['vcpuPlanned'])} vCPU", f"Trung bình {n(d['avgVcpu'], 1)} vCPU / VM"],
        ["RAM", f"{n(d['ramGB'])} GB", f"{n(d['ramPlannedGB'])} GB", f"Trung bình {n(d['avgRamGB'], 1)} GB / VM"],
        ["Dữ liệu", cap(d["diskGB"]), cap(d["diskPlannedGB"]),
         (f"Dùng được {cap(usable)} (+{n(p['storage_overhead_pct'])}% dự trữ) · thô {cap(d['storageNeededGB'])} (×{n(prot, 0 if prot == int(prot) else 2)} bảo vệ)"
          if p["storage_mode"] == "local" else f"Cần cấp {cap(d['storageNeededGB'])} (+{n(p['storage_overhead_pct'])}% dự trữ)")],
    ]
    demand_tbl = _table(["Tài nguyên", "Yêu cầu", f"Sau tăng trưởng (+{n(g)}%)", "Ghi chú"], rows, cls="num-table",
                        aligns=["left", "right", "right", "left"], widths=["14%", "18%", "22%", ""])
    bucket_colors = ["#bfdbfe", "#60a5fa", "#2681FF", "#1d4ed8", "#074B9B"]
    segs = [{"label": b["label"], "value": b["count"], "color": bucket_colors[i]} for i, b in enumerate(wl["sizeBuckets"])]
    donut = svg_donut(segs, size=112, center_label=n(d["vmCount"]), center_sub="VM")
    leg = "".join(f'<div class="leg-row"><span class="radar-swatch" style="background:{s["color"]};"></span>{esc(s["label"])}: <b>{n(s["value"])}</b> VM</div>'
                  for s in segs if s["value"])
    top = [{"label": g_["name"], "a": g_["vcpuShare"], "b": g_["ramShare"]} for g_ in wl["topGroups"][:8]]
    binding = (best or {}).get("binding", "RAM")
    key = "cpu" if binding == "CPU" else "ram"
    wf = a["waterfall"][key]
    unit = "vCPU" if key == "cpu" else "GB RAM"
    wf_svg = svg_waterfall(wf, width=330, height=170, unit=unit, fmt=lambda v: n(v))
    lg = wl.get("largestVm") or {}
    per_core = params.cpu_ratio * max(1.0, params.ht_factor)
    char = wl["characterLabel"]
    reason = ""
    if best and binding == "RAM" and wl["character"] != "memory":
        reason = (f" Dù workload không thiên RAM, RAM vẫn là yếu tố quyết định vì tỷ lệ chia sẻ CPU giả định "
                  f"({n(params.cpu_ratio)} vCPU/thread ≈ {n(per_core)} vCPU/core) cho phép xếp rất nhiều vCPU trên mỗi core.")
    elif best and binding == "CPU" and wl["character"] != "compute":
        reason = " CPU là yếu tố quyết định do tỷ lệ chia sẻ CPU giả định thấp so với lượng vCPU yêu cầu."
    facts = (f"{n(d['vmCount'])} VM thuộc {wl['groupCount']} nhóm; {wl['paretoLines']} nhóm chiếm 80% RAM. "
             f"VM lớn nhất: {lg.get('name', '—')} ({n(lg.get('vcpu'))} vCPU · {n(lg.get('ramGB'))} GB). "
             f"Tỷ lệ {n(wl['ramPerVcpu'], 1)} GB RAM / vCPU — {char[0].lower() + char[1:]}.{reason}")
    return f"""
    {demand_tbl}
    <div class="callout" style="margin-top:8px;">{esc(facts)}</div>
    <table class="grid2 keep"><tr>
      <td><div class="chart-card"><div class="chart-card-title">Phân bố kích thước VM</div>
        <table class="inner"><tr><td style="width:120px;">{donut}</td><td>{leg}</td></tr></table></div></td>
      <td><div class="chart-card"><div class="chart-card-title">Nhóm VM dùng nhiều tài nguyên nhất (% tổng)</div>
        {svg_dual_hbars(top, width=330, label_w=118)}
        {_legend([(COLORS['primary'], '% vCPU'), (COLORS['accent'], '% RAM')])}</div></td>
    </tr></table>
    <div class="chart-card keep" style="margin-top:8px;"><div class="chart-card-title">Từ yêu cầu đến năng lực lắp đặt — {('CPU' if key == 'cpu' else 'RAM')} (yếu tố quyết định)</div>
      <table class="inner"><tr><td style="width:340px;">{wf_svg}</td>
      <td class="muted-cell">Cột đầu là nhu cầu ban đầu; các cột giữa là phần cộng thêm cho tăng trưởng và vùng an toàn;
      cột cuối là tổng năng lực của {best['hostsTotal'] if best else '—'} host đề xuất (gồm host dự phòng, đã trừ phần dành cho hypervisor).
      {('CPU không được vẽ vì không phải yếu tố quyết định.' if key == 'ram' else 'RAM không được vẽ vì không phải yếu tố quyết định.')}</td></tr></table>
    </div>"""


def _row_status(r: Dict[str, Any]) -> str:
    if r.get("suggested"):
        return _pill("Tham khảo", "na")
    if r["recommended"]:
        return _pill("Đề xuất", "ok")
    if not r["failure"]["ok"]:
        return _pill("Chưa đạt N+1", "crit")
    if r.get("tight"):
        return _pill("Sát ngưỡng", "warn")
    return f'<span class="muted">#{r["rank"]}</span>'


def _options(result, currency, show: Optional[Dict[str, bool]] = None) -> str:
    show = show or {}
    rows = sorted(result["results"], key=lambda x: (x.get("suggested", False), x["rank"] or 99))
    p = result["params"]
    local = p["storage_mode"] == "local"
    any_price = any(r["advisory"]["costs"]["tco"] for r in rows if not r.get("suggested"))
    labels = [r["profile"]["name"].replace(" (gợi ý)", "*") for r in rows]
    hosts_chart = svg_bar_chart(labels, [
        {"name": "Theo CPU", "color": COLORS["primary"], "values": [r["hostsForCpu"] for r in rows]},
        {"name": "Theo RAM", "color": COLORS["accent"], "values": [r["hostsForRam"] for r in rows]},
        {"name": "Tổng (gồm dự phòng)", "color": COLORS["primary_dark"], "values": [r["hostsTotal"] for r in rows]},
    ], width=680, height=170)
    body = []
    for r in rows:
        pr, f, adv = r["profile"], r["failure"], r["advisory"]
        fail = f"{pct(f['cpuPct'])} / {pct(f['ramPct'])}" if f["hostsLeft"] else "—"
        extra = r["spare"]["extraAvgVms"]
        extra_txt = "—" if extra is None else ("0" if extra <= 0 else f"~{n(extra)}")
        bind = {"CPU & RAM": "CPU & RAM"}.get(r["binding"], r["binding"])
        cells = [f'<b>{esc(pr["name"])}</b>', f'{r["hostsForCpu"]} / {r["hostsForRam"]} / +{r["redundancy"]}',
                 f'<b>{r["hostsTotal"]}</b>', esc(bind), ratio(r.get("vcpuPerCore")), fail, extra_txt,
                 n(adv["license"]["coresTotal"])]
        if local:
            st = r["storage"]
            cells.append(n((st.get("perHostNeededGB") or 0) / 1024, 1) + ("" if st.get("ok") is not False else " ⚠"))
        if any_price:
            cells.append(money_short(adv["costs"]["tco"], currency) if _tco_comparable(r, result) else "—")
        cells.append(_row_status(r))
        style = f' style="background:{COLORS["ok_bg"]};"' if r["recommended"] else (' style="color:#475467;font-style:italic;"' if r.get("suggested") else "")
        body.append({"style": style, "cells": cells})
    header = ["Phương án", "Host CPU / RAM / dự phòng", "Tổng host", "Yếu tố quyết định", "vCPU : core",
              "Mất 1 host: CPU / RAM", "Chứa thêm (VM)", "Core license"]
    aligns = ["left", "center", "center", "center", "right", "center", "center", "right"]
    if local:
        header.append("Đĩa thô/host (TiB)")
        aligns.append("right")
    if any_price:
        header.append(f"TCO {next((r['advisory']['costs']['years'] for r in rows if r['advisory']['costs'].get('tco')), p.get('tco_years', 5))} năm")
        aligns.append("right")
    header.append("Kết luận")
    aligns.append("center")
    cmp_tbl = _table(header, body, cls="cmp-table", aligns=aligns)
    axes = result["analysis"]["scoreAxes"]
    srows = []
    for r in rows:
        sc = r["advisory"].get("scores") or {}
        style = f' style="background:{COLORS["ok_bg"]};font-weight:700;"' if r["recommended"] else ""
        srows.append({"style": style, "cells": [esc(r["profile"]["name"])] + [n(sc.get(ax["key"])) for ax in axes]
                      + [f'<b>{n(r["advisory"].get("scoreTotal"))}</b>']})
    score_tbl = _table(["Phương án"] + [esc(ax["label"]) for ax in axes] + ["Điểm TB"], srows, cls="score-table",
                       aligns=["left"] + ["center"] * (len(axes) + 1))
    cost_html = ""
    has_sugg = any(r.get("suggested") for r in rows)
    basis_txt = {"tco": "TCO", "budget": "chi phí phần cứng", "cores": "số core license"}.get(result.get("costBasis"), "chi phí")
    score_html = (f'''<div class="keep" style="margin-top:8px;"><div class="sub-h">Bảng điểm (0–100, cao = tốt)</div>{score_tbl}</div>
    <div class="small-note">Số host, Chi phí: so với phương án tốt nhất (chi phí theo {basis_txt}). Hiệu suất: mức dùng của yếu tố quyết định khi mất host.
    Cân đối: độ chênh giữa mức dùng CPU và RAM. Chịu lỗi: mức tải khi mất host. Mở rộng: dư địa tăng trưởng. NUMA: tỷ lệ VM nằm gọn trong một CPU.</div>''')
    return f"""
    <div class="chart-card keep"><div class="chart-card-title">Số host cần theo từng phương án</div>{hosts_chart}
      {_legend([(COLORS['primary'], 'Theo CPU'), (COLORS['accent'], 'Theo RAM'), (COLORS['primary_dark'], 'Tổng (gồm dự phòng)')])}</div>
    <div class="keep" style="margin-top:8px;">{cmp_tbl}</div>
    <div class="small-note">Quy tắc chọn: ít host nhất → không sát ngưỡng (còn dư địa) → {basis_txt} thấp nhất → điểm tổng hợp cao nhất.
    “Mất 1 host” là mức dùng trên phần năng lực khả dụng của các host còn lại; tối đa cho phép {n(p['max_util_pct'])}%.
    {'Dòng in nghiêng (*) là phương án tham khảo do hệ thống gợi ý, không thuộc danh sách ứng viên ban đầu.' if has_sugg else ''}</div>
    {score_html if show.get("scores", True) else ""}
    {cost_html}"""


def _dimms(ram_gb: float, sockets: int) -> str:
    for size in (32, 64, 96, 128, 16, 256):
        cnt = ram_gb / size
        if cnt == int(cnt) and int(cnt) % max(1, sockets) == 0 and int(cnt) in (8, 12, 16, 24, 32):
            return f"{int(cnt)} × {size} GB DDR5 RDIMM"
    return f"{n(ram_gb)} GB DDR5 RDIMM (chia đều các kênh nhớ)"


def _bom(best, params, result) -> str:
    pf = platforms.get(params)
    prof, adv = best["profile"], best["advisory"]
    local = params.storage_mode == "local"
    nh = best["hostsTotal"]
    rows = [
        ["CPU", f"{prof['coresPerSocket']} core / CPU, thế hệ hiện hành (Intel Xeon Scalable hoặc AMD EPYC)", n(prof["sockets"]), n(prof["sockets"] * nh)],
        ["Bộ nhớ (RAM)", f"{ram(prof['ramGB'])} / host — {_dimms(prof['ramGB'], prof['sockets'])}", "1 bộ", n(nh) + " bộ"],
        ["Ổ khởi động", f"2 × M.2 / SSD 480 GB, RAID-1 (cài {pf['hypervisor']})", "2", n(2 * nh)],
    ]
    if local:
        per_host_tib = (best["storage"].get("perHostNeededGB") or 0) / 1024
        drive_tib = 3.84e12 / 1024 ** 4  # 3,84 TB (decimal, nhãn ổ đĩa) ≈ 3,49 TiB
        cnt = max(2, math.ceil(per_host_tib / drive_tib)) if per_host_tib else 0
        rows.append([f"Ổ dữ liệu {pf['hci']}", f"NVMe / SSD Enterprise, ≥ {n(per_host_tib, 1)} TiB thô / host (ví dụ {cnt} × 3,84 TB)", n(cnt), n(cnt * nh)])
        rows.append(["Mạng", f"2 card 25GbE × 2 cổng: quản lý + VM, {pf['migration']}, {pf['hci']} (VLAN tách biệt, dự phòng 2 đường)", "4 cổng", n(4 * nh) + " cổng"])
    else:
        rows.append(["Kết nối lưu trữ", "2 × HBA FC 32Gb hoặc 2 cổng iSCSI 25GbE tới SAN (dự phòng 2 đường)", "2", n(2 * nh)])
        rows.append(["Mạng", f"2 card 25GbE × 2 cổng: quản lý + VM, {pf['migration']} (VLAN tách biệt, dự phòng 2 đường)", "4 cổng", n(4 * nh) + " cổng"])
    rows += [
        ["Quản trị phần cứng", "Cổng quản trị từ xa (iDRAC / iLO / XCC)", "1", n(nh)],
        ["Nguồn", "2 bộ nguồn dự phòng (redundant), hot-plug", "2", n(2 * nh)],
        ["Bảo hành phần cứng", f"{adv['costs'].get('warrantyIncludedYears', 3)} năm kèm theo giá mua"
         + (f"; gia hạn đến năm {adv['costs']['years']} theo mục Chi phí & TCO" if adv["costs"].get("years", 0) > adv["costs"].get("warrantyIncludedYears", 3) else ""),
         "—", n(nh)],
        [f"License {pf['short']}" if params.platform != "generic" else "License nền tảng",
         esc(platforms.edition_of(params)) + (" — tính theo CPU socket" if pf["license_unit"] == "socket" else
                                              f" — tính theo core, tối thiểu {params.license_min_cores_per_socket} core / CPU"
                                              + (f", {pf['license_min_per_host']} core / host" if pf.get("license_min_per_host") else "")),
         f"{n(adv['license']['coresPerHost'])} {adv['license'].get('unit', 'core')}", f"{n(adv['license']['coresTotal'])} {adv['license'].get('unit', 'core')}"],
    ]
    if local:
        rows.append([f"License {pf['hci']}", f"{pf['hci_license']} (≈ {cap(result['demand']['storageNeededGB'])} thô)", "—", "—"])
    rows.append([pf["manager"], "Dùng hệ thống quản trị hiện hữu hoặc triển khai mới (" + " ".join(pf["manager_note"]) + ")", "—", "1"])
    return _table(["Hạng mục", "Cấu hình tham chiếu", "SL / host", f"Tổng ({nh} host)"], rows, cls="bom-table",
                  aligns=["left", "left", "center", "center"], widths=["17%", "55%", "12%", "16%"]) + \
        ('<div class="small-note">BOM sơ bộ để ước lượng; model, thông số và số lượng cuối cùng chốt trong báo giá. '
         + esc(pf["license_note"]) + '</div>')


def svg_architecture(best, params, result) -> str:
    """Logical design, platform-neutral: four networks (management / live migration / storage / VM),
    hosts running the hypervisor, shared storage (HCI or SAN), central management. Product names
    come from platforms.py, so the same picture serves vSphere, Nutanix, Hyper-V, Proxmox…"""
    pf = platforms.get(params)
    nh = best["hostsTotal"]
    prof = best["profile"]
    local = params.storage_mode == "local"
    W, H = 680, 268
    generic = params.platform == "generic"
    nets = [("Quản lý (Management)", "#074B9B"), (pf["migration"] if not generic else "Di chuyển VM", "#7c3aed"),
            ((f"Lưu trữ ({pf['hci_short']})" if not generic else "Lưu trữ HCI") if local else "Lưu trữ (iSCSI / FC)", "#0891b2"),
            ("Mạng VM (Production)", "#15803d")]
    x0, x1 = 128, W - 10
    ny = [26, 44, 62, 80]
    parts = [f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="Roboto, Noto Sans, sans-serif">']
    for (label, color), y in zip(nets, ny):
        parts.append(f'<line x1="{x0}" y1="{y}" x2="{x1}" y2="{y}" stroke="{color}" stroke-width="2.4"/>')
        parts.append(f'<text x="{x0 - 12}" y="{y + 3}" text-anchor="end" font-size="8" font-weight="700" fill="{color}">{esc(label)}</text>')
    shown = min(nh, 6)
    hx0, hx1 = x0 + 6, x1 - 6
    gap = 10
    hw = (hx1 - hx0 - gap * (shown - 1 + (1 if nh > shown else 0))) / (shown + (1 if nh > shown else 0))
    hy, hh = 116, 58
    # cluster boundary
    parts.append(f'<rect x="{x0 - 2}" y="{hy - 6}" width="{x1 - x0 + 4}" height="{hh + 20}" rx="6" fill="none" stroke="{COLORS["faint"]}" stroke-dasharray="5 3"/>')
    parts.append(f'<text x="{x1 - 4}" y="{hy + hh + 10}" text-anchor="end" font-size="7.4" font-weight="700" fill="{COLORS["muted"]}">'
                 f'Cụm ảo hoá · {esc(pf["ha"] if not generic else "HA")} · {esc(pf["balancer"].split(" (")[0] if not generic else "cân bằng tải")} · N+{best["redundancy"]}</text>')
    for i in range(shown + (1 if nh > shown else 0)):
        x = hx0 + i * (hw + gap)
        cx = x + hw / 2
        if i == shown:
            parts.append(f'<text x="{cx:.1f}" y="{hy + hh / 2 + 3:.1f}" text-anchor="middle" font-size="14" fill="{COLORS["muted"]}">…</text>')
            continue
        for (lbl, color), y in zip(nets, ny):
            parts.append(f'<line x1="{cx - 9 + nets.index((lbl, color)) * 6:.1f}" y1="{y}" x2="{cx - 9 + nets.index((lbl, color)) * 6:.1f}" y2="{hy}" stroke="{color}" stroke-width="1"/>')
            parts.append(f'<circle cx="{cx - 9 + nets.index((lbl, color)) * 6:.1f}" cy="{y}" r="2" fill="{color}"/>')
        parts.append(f'<rect x="{x:.1f}" y="{hy}" width="{hw:.1f}" height="{hh}" rx="4" fill="{COLORS["surface"]}" stroke="{COLORS["primary_dark"]}" stroke-width="1.2"/>')
        parts.append(f'<text x="{cx:.1f}" y="{hy + 15}" text-anchor="middle" font-size="8.4" font-weight="800" fill="{COLORS["ink"]}">Host {i + 1}</text>')
        parts.append(f'<text x="{cx:.1f}" y="{hy + 27}" text-anchor="middle" font-size="7" fill="{COLORS["muted"]}">{esc(pf["hypervisor"])}</text>')
        parts.append(f'<text x="{cx:.1f}" y="{hy + 39}" text-anchor="middle" font-size="7" fill="{COLORS["muted"]}">{prof["cores"]} core · {esc(ram(prof["ramGB"]))}</text>')
        if i == nh - 1 and best["redundancy"]:
            parts.append(f'<text x="{cx:.1f}" y="{hy + 51}" text-anchor="middle" font-size="6.8" font-weight="700" fill="{COLORS["warn"]}">năng lực dự phòng</text>')
    # storage
    sy = hy + hh + 26
    if local:
        parts.append(f'<rect x="{x0}" y="{sy}" width="{x1 - x0}" height="32" rx="5" fill="#e0f2fe" stroke="#0891b2"/>')
        parts.append(f'<text x="{(x0 + x1) / 2:.1f}" y="{sy + 13}" text-anchor="middle" font-size="8.4" font-weight="800" fill="#075985">{esc(pf["hci"] if not generic else "Lưu trữ HCI")} (dùng chung) — {esc(cap(result["demand"]["storageNeededGB"]))} thô</text>')
        parts.append(f'<text x="{(x0 + x1) / 2:.1f}" y="{sy + 25}" text-anchor="middle" font-size="7.2" fill="#075985">Gộp ổ đĩa của {nh} host · hệ số bảo vệ ×{esc(n(params.storage_protection_factor, 2).rstrip("0").rstrip(","))}</text>')
        for i in range(shown):
            cx = hx0 + i * (hw + gap) + hw / 2
            parts.append(f'<line x1="{cx:.1f}" y1="{hy + hh}" x2="{cx:.1f}" y2="{sy}" stroke="#0891b2" stroke-dasharray="3 2"/>')
    else:
        parts.append(f'<rect x="{(x0 + x1) / 2 - 150:.1f}" y="{sy}" width="300" height="32" rx="5" fill="#e0f2fe" stroke="#0891b2"/>')
        parts.append(f'<text x="{(x0 + x1) / 2:.1f}" y="{sy + 13}" text-anchor="middle" font-size="8.4" font-weight="800" fill="#075985">Hệ thống lưu trữ SAN dùng chung</text>')
        parts.append(f'<text x="{(x0 + x1) / 2:.1f}" y="{sy + 25}" text-anchor="middle" font-size="7.2" fill="#075985">Cấp {esc(cap(result["demand"]["storageNeededGB"]))} cho cụm</text>')
    # central management
    parts.append(f'<rect x="0" y="{hy}" width="{x0 - 16}" height="{hh}" rx="4" fill="#fff" stroke="#074B9B" stroke-dasharray="4 2"/>')
    mgr = pf["manager"] if not generic else "Quản trị tập trung"
    mgr_lines = [mgr] if len(mgr) <= 18 else [mgr.split(" (")[0].split(" / ")[0], ""]
    parts.append(f'<text x="{(x0 - 16) / 2:.1f}" y="{hy + 18}" text-anchor="middle" font-size="8" font-weight="800" fill="#074B9B">{esc(mgr_lines[0])}</text>')
    parts.append(f'<text x="{(x0 - 16) / 2:.1f}" y="{hy + 31}" text-anchor="middle" font-size="6.8" fill="{COLORS["muted"]}">{esc(pf["manager_note"][0])}</text>')
    parts.append(f'<text x="{(x0 - 16) / 2:.1f}" y="{hy + 42}" text-anchor="middle" font-size="6.8" fill="{COLORS["muted"]}">{esc(pf["manager_note"][1])}</text>')
    # management → management network: out of the box's right edge, up the gutter left of the network lines.
    gx = x0 - 6
    parts.append(f'<polyline points="{x0 - 16},{hy + hh / 2:.1f} {gx},{hy + hh / 2:.1f} {gx},{ny[0]} {x0},{ny[0]}" fill="none" stroke="#074B9B" stroke-width="1"/>')
    parts.append("</svg>")
    return "".join(parts)


def _arch_note(params) -> str:
    pf = platforms.get(params)
    if params.platform == "generic":
        return ("Sơ đồ trình bày theo khối chức năng, dùng chung cho các nền tảng (VMware vSphere, Nutanix AHV, Microsoft Hyper-V, "
                "Proxmox VE…): phần mềm quản trị tập trung quản lý cụm; HA và cân bằng tải bật ở mức cụm. Tên sản phẩm tương ứng xem Phụ lục B.")
    return f"{pf['manager']} quản lý cụm; {pf['ha']} và {pf['balancer']} bật ở mức cụm."


def _stackbar(best, d, p) -> str:
    """Capacity allocation as an HTML table — percentages normalised to exactly 100,
    labels hidden below 8% (the legend carries them)."""
    n_tot, red, n_fail = best["hostsTotal"], best["redundancy"], best["failure"]["hostsLeft"]
    util = max(0.05, min(1.0, p["max_util_pct"] / 100.0))
    ph, prof = best["perHost"], best["profile"]
    segs_def = [("request", "Yêu cầu", "#2681FF", "#fff"), ("growth", "Dự phòng tăng trưởng", "#93c5fd", COLORS["ink"]),
                ("free", "Còn trống", "#bbf7d0", COLORS["ink"]), ("safe", f"Vùng an toàn {n(100 - p['max_util_pct'])}%", "#fde68a", COLORS["ink"]),
                ("red", f"Dự phòng N+{red}", "#fca5a5", COLORS["ink"]), ("hv", "Hypervisor", "#cbd5e1", COLORS["ink"])]
    rows_html = ""
    for label, total, usable_host, req, planned in (
        ("CPU", prof["cores"] * max(1.0, p["ht_factor"]) * p["cpu_ratio"] * n_tot, ph["logicalCpu"] * p["cpu_ratio"], d["vcpu"], d["vcpuPlanned"]),
        ("RAM", prof["ramGB"] * p["ram_ratio"] * n_tot, ph["usableRamGB"] * p["ram_ratio"], d["ramGB"], d["ramPlannedGB"]),
    ):
        vals = [req, planned - req, max(0.0, usable_host * n_fail * util - planned), usable_host * n_fail * (1 - util),
                usable_host * red, max(0.0, total - usable_host * n_tot)]
        vals = [max(0.0, v) for v in vals]
        tot = sum(vals) or 1.0
        raw = [v / tot * 100 for v in vals]
        rounded = [round(x, 1) for x in raw]
        diff = round(100.0 - sum(rounded), 1)
        if rounded:
            k = max(range(len(rounded)), key=lambda i: rounded[i])
            rounded[k] = round(rounded[k] + diff, 1)
        tds = ""
        for (key, name, bg, fg), w in zip(segs_def, rounded):
            if w <= 0:
                continue
            lbl = f"{n(w)}%" if w >= 8 else ""
            tds += f'<td class="seg" style="width:{w}%;background:{bg};color:{fg};" title="{esc(name)}">{lbl}</td>'
        rows_html += f'<tr><td class="label">{label}</td><td class="bar"><table class="stackbar"><tr>{tds}</tr></table></td></tr>'
    return (f'<table class="stack-wrap">{rows_html}</table>'
            + _legend([(bg, name) for _, name, bg, _ in segs_def]))


def _gauges(best, p) -> str:
    util = p["max_util_pct"]
    zones = ((0.0, util, "ok"), (util, 100.0, "warn"))
    f = best["failure"]
    items = [(best["after"]["cpuUtilPct"], "CPU · vận hành", f"{best['hostsTotal']} host"),
             (best["after"]["ramUtilPct"], "RAM · vận hành", f"{best['hostsTotal']} host"),
             (f["cpuPct"], f"CPU · mất {best['redundancy']} host", f"còn {f['hostsLeft']} host"),
             (f["ramPct"], f"RAM · mất {best['redundancy']} host", f"còn {f['hostsLeft']} host")]
    cells = ""
    for v, top, capn in items:
        cls = "na" if v is None else ("ok" if v < util - 2 else ("warn" if v <= 100 else "crit"))
        color = COLORS[STATUS_COLOR[cls][0]] if cls != "na" else COLORS["faint"]
        svg = svg_gauge(v, color, size=112, label_top=top, caption=capn, zones=zones)
        cells += f'<td><div class="gauge-card {cls}">{_vn_html(svg)}</div></td>'
    return f'<table class="grid4 keep"><tr>{cells}</tr></table>'


def _design(result, best, params, num: str = "5", show: Optional[Dict[str, bool]] = None) -> str:
    show = show or {}
    d, p = result["demand"], result["params"]
    prof, ph, adv = best["profile"], best["perHost"], best["advisory"]
    numa, lic, run = adv["numa"], adv["license"], adv["runway"]
    local = p["storage_mode"] == "local"
    so = 1 + p["storage_overhead_pct"] / 100.0
    usable_need = d["diskGB"] * d["growthFactor"] * so
    extra = best["spare"]["extraAvgVms"]
    spec = [
        ("Cấu hình mỗi host", f"{prof['sockets']} CPU × {prof['coresPerSocket']} core = {prof['cores']} core ({n(prof['cores'] * max(1.0, p['ht_factor']))} thread) · {ram(prof['ramGB'])} RAM"),
        ("Số host", f"{best['hostsTotal']} host = {best['hostsBase']} theo tải + {best['redundancy']} dự phòng"
                    + (f" + {best['hostsTotal'] - best['hostsComputed']} bổ sung theo lựa chọn của người lập" if best.get("hostsForced") else
                       (f" (tối thiểu {p['min_hosts']})" if best["hostsTotal"] > best["hostsBase"] + best["redundancy"] else ""))),
        ("Năng lực khả dụng / host", f"{n(ph['usableCores'])} core · {n(ph['logicalCpu'])} thread · {ram(ph['usableRamGB'])} RAM (đã trừ phần dành cho hypervisor)"),
        ("Tổng cụm", f"{n(best['totals']['cores'])} core · {ram(best['totals']['ramGB'])} RAM"),
        ("Tỷ lệ chia sẻ thực tế", f"CPU {ratio(best.get('vcpuPerCore'))} (vCPU : core vật lý) · RAM {ratio(d['ramPlannedGB'] / max(1, best['totals']['ramGB']))}"),
        ("Khi mất 1 host", f"CPU {pct(best['failure']['cpuPct'])} · RAM {pct(best['failure']['ramPct'])} — tối đa cho phép {n(p['max_util_pct'])}%"),
        ("Dư địa", ("Không còn dư địa cho VM mới" if (extra is not None and extra <= 0)
                    else f"Chứa thêm ~{n(extra)} VM cỡ trung bình; nhận thêm ~{n(max(0, run['extraGrowthPct'] or 0))}% tải ngoài +{n(p['growth_pct'])}% đã dự phòng")),
        ("NUMA", f"{prof['coresPerSocket']} core · {ram(numa['ramPerSocketGB'])} mỗi CPU — "
                 + ("mọi VM nằm gọn trong một CPU" if numa["ok"] else f"{len(numa['wideCpu']) + len(numa['wideRam'])} nhóm VM vượt một CPU")),
        ("License", f"{n(lic['coresTotal'])} {lic.get('unit', 'core')} ({n(lic['coresPerHost'])} / host) · {platforms.edition_of(params)}"),
        ("Lưu trữ", (f"{platforms.get(params)['hci']}: cần {cap(usable_need)} dùng được → {cap(d['storageNeededGB'])} thô (×{n(p['storage_protection_factor'], 0 if p['storage_protection_factor'] == int(p['storage_protection_factor']) else 2)}), "
                     f"~{cap(best['storage'].get('perHostNeededGB'))} thô / host; cấu hình {cap(prof['diskGB'])} / host"
                     if local else f"SAN dùng chung: cấp {cap(d['storageNeededGB'])} (gồm tăng trưởng và {n(p['storage_overhead_pct'])}% dự trữ)")),
    ]
    spec_html = _kv_table([(k, esc(v)) for k, v in spec], key_w="26%")
    bal = adv["balance"]
    if bal["strandedCpuPct"] >= 15:
        bal_txt = (f"RAM quyết định số host; khi RAM chạm mức tối đa, khoảng {n(bal['strandedCpuPct'])}% CPU vẫn trống "
                   f"(thực tế chỉ {ratio(best.get('vcpuPerCore'))} vCPU trên mỗi core). Có thể dùng CPU ít core hơn để giảm license — xem phương án tham khảo ở mục 4.")
    elif bal["strandedRamPct"] >= 15:
        bal_txt = f"CPU quyết định số host; khoảng {n(bal['strandedRamPct'])}% RAM còn trống khi CPU chạm mức tối đa."
    else:
        bal_txt = f"CPU ({pct(bal['cpuUtilPct'])}) và RAM ({pct(bal['ramUtilPct'])}) được dùng cân đối."
    parts = [f'<div class="sub-h">{num}.1 Thông số cụm</div>{spec_html}']
    k = 2
    if show.get("bom", True):
        parts.append(f'<div class="sub-h">{num}.{k} Cấu hình phần cứng tham chiếu (BOM sơ bộ)</div>{_bom(best, params, result)}')
        k += 1
    if show.get("arch", True):
        parts.append(f'<div class="keep"><div class="sub-h">{num}.{k} Kiến trúc logic</div><div class="chart-card">{svg_architecture(best, params, result)}'
                     f'<div class="small-note" style="margin-top:2px;">Mỗi host nối 4 mạng tách VLAN, mỗi mạng 2 đường dự phòng. {esc(_arch_note(params))}</div></div></div>')
        k += 1
    if show.get("alloc", True):
        parts.append(f'<div class="keep"><div class="sub-h">{num}.{k} Phân bổ năng lực</div><div class="chart-card">{_stackbar(best, d, p)}</div>'
                     f'{_gauges(best, p)}<div class="callout" style="margin-top:6px;"><b>Cân đối CPU / RAM.</b> {esc(vn(bal_txt))}</div></div>')
    return "".join(parts)


def _tco_comparable(r, result) -> bool:
    """An option's TCO is comparable only if it has the same cost components as the others
    (an engine-suggested option has no hardware price — leaving it in would look cheaper)."""
    c = r["advisory"]["costs"]
    if not c.get("tco"):
        return False
    any_hw = any(x["advisory"]["costs"].get("hardware") is not None for x in result["results"] if not x.get("suggested"))
    return not (any_hw and c.get("hardware") is None)


def _tco(result, best, currency) -> str:
    c = best["advisory"]["costs"]
    years, inc = c["years"], c["warrantyIncludedYears"]
    rows = []
    for y in c["yearly"]:
        war = ("Trong bảo hành" if y["inWarranty"] else ("—" if y["warranty"] is None else money_short(y["warranty"], currency)))
        rows.append([f"<b>Năm {y['year']}</b>", money_short(y["hardware"], currency) if y["hardware"] else ("—" if y["hardware"] is None else "0"),
                     war, money_short(y["license"], currency) if y["license"] is not None else "—",
                     f"<b>{money_short(y['total'], currency)}</b>", money_short(y["cumulative"], currency)])
    rows.append({"style": f' style="background:{COLORS["surface2"]};font-weight:700;"',
                 "cells": [f"Tổng {years} năm", money_short(c["hardware"], currency), money_short(c["warranty"], currency),
                           money_short(c["license"], currency), money_short(c["tco"], currency), ""]})
    year_tbl = _table(["Năm", "Phần cứng", "Bảo hành", "License", "Tổng năm", "Lũy kế"], rows, cls="num-table",
                      aligns=["left", "right", "right", "right", "right", "right"])
    leg = _legend([(COST_COLORS["hw"], "Phần cứng"), (COST_COLORS["war"], "Gia hạn bảo hành"), (COST_COLORS["lic"], "License"),
                   (COLORS["primary_dark"], "Lũy kế (trục phải)")])
    opts = [r for r in sorted(result["results"], key=lambda x: (x.get("suggested", False), x["rank"] or 99)) if _tco_comparable(r, result)]
    cmp_rows = []
    for r in opts:
        cc = r["advisory"]["costs"]
        style = f' style="background:{COLORS["ok_bg"]};font-weight:700;"' if r["recommended"] else ""
        cmp_rows.append({"style": style, "cells": [esc(r["profile"]["name"]), f"{r['hostsTotal']}", money_short(cc["hardware"], currency),
                                                   money_short(cc["warranty"], currency), money_short(cc["license"], currency),
                                                   f"<b>{money_short(cc['tco'], currency)}</b>", money_short(cc["perVmMonth"], currency)]})
    cmp_tbl = _table(["Phương án", "Host", "Phần cứng", "Gia hạn BH", f"License {years} năm", f"TCO {years} năm", "/ VM / tháng"], cmp_rows,
                     cls="num-table", aligns=["left", "center", "right", "right", "right", "right", "right"])
    bars = svg_cost_bars([{"label": r["profile"]["name"], "hw": r["advisory"]["costs"]["hardware"], "war": r["advisory"]["costs"]["warranty"],
                           "lic": r["advisory"]["costs"]["license"], "best": r["recommended"]} for r in opts], currency, width=680)
    ext = f"năm {inc + 1}–{years}" if years > inc + 1 else (f"năm {years}" if years > inc else "")
    intro = (f"Phần cứng mua năm 1, đã gồm bảo hành {inc} năm"
             + (f"; gia hạn bảo hành {ext} tính theo "
                + ("% giá host mỗi năm" if c["warrantyMode"] == "pct" else "chi phí mỗi host mỗi năm") if ext else "")
             + "; license tính theo từng năm. Đơn vị: " + esc(currency) + ".")
    miss = ""
    if c.get("warrantyMissingYears"):
        miss = (f'<div class="callout" style="margin-top:6px;border-left-color:{COLORS["warn"]};"><b>Chưa đủ dữ liệu.</b> Chưa nhập chi phí gia hạn bảo hành năm '
                f'{", ".join(str(y) for y in c["warrantyMissingYears"])} — TCO chưa gồm khoản này.</div>')
    return f"""
    <div class="section-desc">{intro}</div>
    <div class="keep"><div class="sub-h">TCO theo năm — {esc(best['profile']['name'])} × {best['hostsTotal']} host</div>{year_tbl}{miss}</div>
    <div class="chart-card keep" style="margin-top:8px;"><div class="chart-card-title">Chi phí theo năm và lũy kế</div>{svg_yearly_tco(c['yearly'], currency)}{leg}</div>
    {"" if len(opts) < 2 else f'<div class="keep"><div class="sub-h">So sánh TCO giữa các phương án</div>{cmp_tbl}<div class="chart-card" style="margin-top:6px;">{bars}{_legend([(COST_COLORS["hw"], "Phần cứng"), (COST_COLORS["war"], "Gia hạn bảo hành"), (COST_COLORS["lic"], "License")])}</div></div>'}
    <div class="small-note">TCO chỉ gồm phần cứng, gia hạn bảo hành và license theo đơn giá đã nhập; chưa gồm dịch vụ triển khai, điện, không gian và chi phí vận hành.
    {"Phương án tham khảo chưa có đơn giá phần cứng nên không đưa vào so sánh TCO." if any(r.get("suggested") and not _tco_comparable(r, result) for r in result["results"]) else ""}</div>"""


def _scale(result, best_idx, params) -> str:
    a = result["analysis"]
    sens, proj = a["sensitivity"], a["projection"]
    best_name = result["results"][best_idx]["profile"]["name"]
    sw = sens["growth"]
    xs = sw["x"]
    cur = xs.index(sw["current"]) if sw["current"] in xs else None
    series = [{"name": s["name"], "color": SERIES_COLORS[i % len(SERIES_COLORS)], "values": s["hosts"]} for i, s in enumerate(sw["series"])]
    lines = svg_lines([f"{n(x)}%" for x in xs], series, width=330, height=160, current_idx=cur, y_label="host")
    leg = _legend([(s["color"], s["name"]) for s in series])
    pts = proj["points"] if proj else []
    proj_svg = _vn_html(svg_projection(pts, proj["installed"] if proj else 0, width=330, height=160))

    def flat_note(key: str, label: str, fmt) -> str:
        s = sens[key]
        idx = next((i for i, x in enumerate(s["series"]) if x["name"] == best_name), 0)
        hosts = s["series"][idx]["hosts"]
        lo, hi = fmt(s["x"][0]), fmt(s["x"][-1])
        if min(hosts) == max(hosts):
            return f"{label} từ {lo} đến {hi}: số host không đổi ({hosts[0]} host)."
        return f"{label} từ {lo} đến {hi}: số host thay đổi từ {max(hosts)} xuống {min(hosts)}." if hosts[0] >= hosts[-1] else \
            f"{label} từ {lo} đến {hi}: số host thay đổi từ {min(hosts)} lên {max(hosts)}."

    cpu_note = flat_note("cpuRatio", "Tỷ lệ chia sẻ CPU", lambda x: f"{n(x, 1).rstrip('0').rstrip(',')} vCPU/thread")
    if sens["cpuRatio"] and min(next(s for s in sens["cpuRatio"]["series"] if s["name"] == best_name)["hosts"]) == \
            max(next(s for s in sens["cpuRatio"]["series"] if s["name"] == best_name)["hosts"]):
        cpu_note += " RAM là yếu tố quyết định nên thay đổi giả định CPU không ảnh hưởng đến số host."
    util_note = flat_note("maxUtil", "Mức dùng tối đa mỗi host", lambda x: f"{n(x)}%")
    same = params.growth_pct == params.annual_growth_pct
    growth_note = (f"Dự phòng tăng trưởng +{n(params.growth_pct)}% (đã gồm trong cấu hình) và tăng trưởng {n(params.annual_growth_pct)}%/năm là hai cách nhìn của cùng một nhu cầu, "
                   f"không cộng dồn: dự báo tính lại từ nhu cầu ban đầu"
                   + (", nên năm thứ 1 của dự báo tương đương phần dự phòng đã tính." if same else "."))
    return f"""
    <table class="grid2 keep"><tr>
      <td><div class="chart-card"><div class="chart-card-title">Số host cần theo năm — {esc(best_name)}</div>{proj_svg}
        {_legend([(COLORS['primary'], 'Host cần'), (COLORS['crit'], 'Vượt số host đề xuất'), (COLORS['ok'], 'Số host đề xuất')])}</div></td>
      <td><div class="chart-card"><div class="chart-card-title">Số host theo mức dự phòng tăng trưởng</div>{lines}{leg}</div></td>
    </tr></table>
    <div class="callout" style="margin-top:8px;"><b>Thời điểm mở rộng.</b> {esc(vn(a.get('expandText') or ''))}<br/>{esc(vn(growth_note))}</div>
    <ul class="tight" style="margin-top:6px;"><li>{esc(vn(cpu_note))}</li><li>{esc(vn(util_note))}</li></ul>"""


def _findings(fnd: List[Dict[str, str]]) -> str:
    rows = []
    for x in fnd:
        cls, lbl = SEV_PILL.get(x["severity"], ("na", x["severity"]))
        rows.append([_pill(lbl, cls), esc(x["area"]),
                     f'<b>{esc(vn(x["title"]))}</b><div class="muted small">{esc(vn(x["detail"]))}</div>', esc(vn(x["action"]))])
    return _table(["Mức độ", "Lĩnh vực", "Nhận định", "Khuyến nghị"], rows, cls="findings-table",
                  widths=["13%", "13%", "42%", "32%"])


def _plan(steps) -> str:
    rows = [[f'<b>{esc(s["phase"])}</b>', esc(s["when"]), esc(vn(s["what"]))] for s in steps]
    return _table(["Giai đoạn", "Thời điểm", "Nội dung"], rows, cls="plan-table", widths=["24%", "18%", "58%"])


def _method(result, best, params, num: str = "9") -> str:
    rows = [[esc(k), esc(vn(v)), esc(vn(m))] for k, v, m in sizing.assumption_rows(params)]
    t1 = _table(["Hạng mục", "Giá trị", "Ý nghĩa"], rows, cls="method-table", widths=["24%", "32%", "44%"])
    if not best:
        return f'<div class="sub-h">Bảng {num}.1 – Giả định</div>' + t1
    d, p = result["demand"], result["params"]
    prof, ph, calc, f = best["profile"], best["perHost"], best["calc"], best["failure"]
    ht = max(1.0, p["ht_factor"])
    g = d["growthFactor"]
    so = 1 + p["storage_overhead_pct"] / 100.0
    prot = d["storageProtectionFactor"]
    warn = lambda v: ' <span class="warn-txt">⚠ sát ngưỡng</span>' if (v is not None and v >= p["max_util_pct"] - 2) else ""  # noqa: E731
    stor_formula = (f"{n(d['diskGB'])} GB × {n(g, 2)} × {n(so, 2)}" + (f" × {n(prot, 2).rstrip('0').rstrip(',')}" if p["storage_mode"] == "local" else ""))
    rows2 = [
        ["1. Nhu cầu sau tăng trưởng", f"Yêu cầu × {n(g, 2)}", f"{n(d['vcpu'])} → {n(d['vcpuPlanned'])} vCPU", f"{n(d['ramGB'])} → {n(d['ramPlannedGB'])} GB"],
        ["2. Năng lực quy hoạch / host",
         f"CPU: ({prof['cores']} − {n(p['reserve_cores'])}) × {n(ht)} × {n(p['cpu_ratio'])} × {n(p['max_util_pct'])}%<br/>"
         f"RAM: ({n(prof['ramGB'])} − {n(p['reserve_ram_gb'])}) × {n(p['ram_ratio'])} × {n(p['max_util_pct'])}%",
         f"{n(ph['vcpuCapacity'])} vCPU", f"{n(ph['vramCapacityGB'], 1)} GB"],
        ["3. Số host theo tải", "Bước 1 ÷ Bước 2, làm tròn lên",
         f"{n(calc['cpuHostsRaw'], 2)} → {best['hostsForCpu']}", f"{n(calc['ramHostsRaw'], 2)} → {best['hostsForRam']}"],
        ["4. Cộng dự phòng", f"max(CPU, RAM) + {best['redundancy']}" + (f", tối thiểu {p['min_hosts']}" if p["min_hosts"] > best["hostsBase"] + best["redundancy"] else ""),
         {"span": (f"{best['hostsComputed']} host theo tính toán → <b>{best['hostsTotal']} host</b> theo lựa chọn của người lập"
                   if best.get("hostsForced") else f"<b>{best['hostsTotal']} host</b>")}, None],
        [f"5. Kiểm tra mất {best['redundancy']} host", f"Nhu cầu ÷ năng lực khả dụng {f['hostsLeft']} host còn lại",
         pct(f["cpuPct"]) + warn(f["cpuPct"]), pct(f["ramPct"]) + warn(f["ramPct"])],
        ["6. Lưu trữ", stor_formula,
         {"span": f"{cap(d['storageNeededGB'])}" + (f" thô (~{cap(best['storage'].get('perHostNeededGB'))} / host)" if p["storage_mode"] == "local" else "")}, None],
    ]
    body = ""
    for r in rows2:
        if isinstance(r[2], dict):
            body += f'<tr><td>{r[0]}</td><td>{r[1]}</td><td colspan="2" style="text-align:center;">{r[2]["span"]}</td></tr>'
        else:
            body += f'<tr><td>{r[0]}</td><td>{r[1]}</td><td style="text-align:right;">{r[2]}</td><td style="text-align:right;">{r[3]}</td></tr>'
    t2 = (f'<table class="method-table"><thead><tr><th style="width:26%;">Bước</th><th>Công thức</th>'
          f'<th style="width:17%;text-align:right;">CPU</th><th style="width:17%;text-align:right;">RAM</th></tr></thead><tbody>{body}</tbody></table>')
    return (f'<div class="sub-h">Bảng {num}.1 – Giả định</div>{t1}'
            f'<div class="keep"><div class="sub-h">Bảng {num}.2 – Cách tính (phương án {esc(prof["name"])})</div>{t2}</div>'
            f'<div class="small-note">Dung lượng: 1 TiB = 1.024 GB. "Thô" là dung lượng trước khi trừ phần bảo vệ dữ liệu của lưu trữ HCI; "dùng được" là sau khi trừ.</div>')


def _vm_appendix(vms: List[Dict[str, Any]], d: Dict[str, Any]) -> str:
    notes = [_clean_note(v["name"], v.get("note") or "") for v in vms]
    has_note = any(notes)
    rows = []
    for v, nt in zip(vms, notes):
        q = v["qty"]
        cells = [esc(v["name"]), n(q), n(v["vcpu"]), n(v["ramGB"]), n(v["diskGB"]), n(q * v["vcpu"]), n(q * v["ramGB"]), n(q * v["diskGB"])]
        if has_note:
            cells.append(f'<span class="muted">{esc(nt)}</span>')
        rows.append(cells)
    tot = ["<b>Tổng</b>", f"<b>{n(d['vmCount'])}</b>", "", "", "", f"<b>{n(d['vcpu'])}</b>", f"<b>{n(d['ramGB'])}</b>", f"<b>{n(d['diskGB'])}</b>"]
    if has_note:
        tot.append("")
    rows.append({"style": f' style="background:{COLORS["surface2"]};"', "cells": tot})
    header = ["Tên VM / nhóm", "SL", "vCPU / VM", "RAM / VM (GB)", "Disk / VM (GB)", "Tổng vCPU", "Tổng RAM (GB)", "Tổng disk (GB)"]
    aligns = ["left"] + ["right"] * 7
    if has_note:
        header.append("Vai trò / ghi chú")
        aligns.append("left")
    return _table(header, rows, cls="appendix-table", aligns=aligns)


def _glossary(params=None) -> str:
    terms = _table(["Thuật ngữ", "Giải thích"], [[f"<b>{esc(k)}</b>", esc(v)] for k, v in GLOSSARY], cls="method-table glossary", widths=["24%", "76%"])
    keys = ["vmware", "nutanix", "hyperv", "proxmox"]
    cur = getattr(params, "platform", "vmware")
    head = ["Khái niệm chung"] + [f"<b>{esc(platforms.PLATFORMS[k]['label'])}</b>" if k == cur else esc(platforms.PLATFORMS[k]["label"]) for k in keys]
    rows = [[f"<b>{esc(c)}</b>"] + [esc(platforms.PLATFORMS[k][field]) for k in keys] for c, field in platforms.CONCEPTS]
    mapping = _table(head, rows, cls="method-table glossary map-table", widths=["22%", "19.5%", "19.5%", "19.5%", "19.5%"])
    return (terms + '<div class="keep"><div class="sub-h">Tên gọi tương ứng trên các nền tảng</div>' + mapping
            + '<div class="small-note">Báo cáo dùng khái niệm chung; tên sản phẩm cụ thể theo nền tảng được chọn'
            + (f' (<b>{esc(platforms.get(params)["label"])}</b>)' if cur != "generic" else '') + '.</div></div>')


def _existing_chart(existing: List[Dict[str, Any]]) -> str:
    rows = existing[:10]
    labels = [e["name"] for e in rows]
    val = lambda e, k: e.get(k) if e.get(k) is not None else 0  # noqa: E731
    cpu = svg_bar_chart(labels, [{"name": "Trước", "color": "#93c5fd", "values": [val(e, "cpuBefore") for e in rows]},
                                 {"name": "Sau", "color": COLORS["primary"], "values": [val(e, "cpuAfter") for e in rows]}],
                        width=330, height=160, threshold=100, threshold_label="Ngưỡng", unit="%")
    ramc = svg_bar_chart(labels, [{"name": "Trước", "color": "#67e8f9", "values": [val(e, "ramBefore") for e in rows]},
                                  {"name": "Sau", "color": COLORS["accent"], "values": [val(e, "ramAfter") for e in rows]}],
                         width=330, height=160, threshold=100, threshold_label="Ngưỡng", unit="%")
    return f"""<table class="grid2 keep"><tr>
      <td><div class="chart-card"><div class="chart-card-title">CPU (% ngưỡng chính sách) trước / sau tiếp nhận</div>{cpu}{_legend([("#93c5fd", "Trước"), (COLORS["primary"], "Sau")])}</div></td>
      <td><div class="chart-card"><div class="chart-card-title">RAM (% ngưỡng chính sách) trước / sau tiếp nhận</div>{ramc}{_legend([("#67e8f9", "Trước"), (COLORS["accent"], "Sau")])}</div></td>
    </tr></table>"""


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------
def _css(header_left: str, header_right: str, date_txt: str) -> str:
    c = COLORS
    q = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')  # noqa: E731
    return f"""
@page {{
  size: A4; margin: 18mm 15mm 16mm 15mm;
  @top-left {{ text-transform:none; content: "{q(header_left)}"; font-family:'Roboto','Noto Sans',sans-serif; font-size:8px; color:{c['faint']}; letter-spacing:.03em; }}
  @top-right {{ text-transform:none; content: "{q(header_right)}"; font-family:'Roboto','Noto Sans',sans-serif; font-size:8px; color:{c['faint']}; }}
  @bottom-left {{ text-transform:none; content: "Ngày lập {q(date_txt)}"; font-family:'Roboto','Noto Sans',sans-serif; font-size:8px; color:{c['faint']}; }}
  @bottom-right {{ text-transform:none; content: "Trang " counter(page) " / " counter(pages); font-family:'Roboto','Noto Sans',sans-serif; font-size:8px; color:{c['faint']}; }}
}}
@page cover {{ @top-left {{ content:none; }} @top-right {{ content:none; }} @bottom-left {{ content:none; }} @bottom-right {{ content:none; }} }}
*, *::before, *::after {{ box-sizing:border-box; }}
html, body {{ margin:0; padding:0; }}
.section, .chart-card, table, .callout, .section-desc, .exec-brief, .note-line, .sub-h {{ margin-left:0; margin-right:0; }}
.section {{ margin-top:16px; break-inside:auto; }}
.section-title {{ border-left:none; padding-left:0; font-size:13.5px; margin-bottom:6px; break-after:avoid; page-break-after:avoid; }}
.section-desc {{ font-size:9px; color:{c['muted']}; margin:0 0 8px 0; padding-left:0; break-after:avoid; }}
.sec-num {{ display:inline-block; min-width:18px; height:18px; line-height:18px; text-align:center; border-radius:3px; background:{c['primary_dark']}; color:#fff; font-size:9.5px; margin-right:7px; padding:0 3px; }}
.sub-h {{ font-size:10.4px; font-weight:700; color:{c['primary_dark']}; margin:12px 0 5px; break-after:avoid; }}
.appendix {{ break-before:page; page-break-before:always; }}
.keep, .chart-card, .callout, .exec-brief, .kv-table, .cmp-table, .score-table, .bom-table, .grid2, .grid4, .stack-wrap, .glossary {{ break-inside:avoid; page-break-inside:avoid; }}
.chart-card {{ width:100%; margin-bottom:0; padding:8px 10px; }}
.callout {{ background:{c['surface']}; border:1px solid {c['border']}; border-left:3px solid {c['accent']}; border-radius:0 5px 5px 0; padding:7px 10px; font-size:9.3px; line-height:1.5; }}
.callout b {{ color:{c['primary_dark']}; }}
.callout.note {{ border-left-color:{c['primary_dark']}; background:#f5f8fe; }}
.note-line {{ margin-top:8px; font-size:9.2px; color:{c['muted']}; }}
.muted {{ color:{c['muted']}; }} .small {{ font-size:8.2px; }} .warn-txt {{ color:{c['warn']}; font-weight:700; white-space:nowrap; }}
table {{ width:100%; }}
table.grid2, table.grid4, table.inner, table.cv-grid {{ border-collapse:separate; border-spacing:0; }}
table.grid2 > tbody > tr > td, table.grid2 > tr > td {{ width:50%; padding:0; vertical-align:top; border:none; }}
table.grid2 td + td {{ padding-left:8px !important; }}
table.grid4 td {{ width:25%; padding:0 3px; border:none; vertical-align:top; }}
table.grid4 td:first-child {{ padding-left:0; }} table.grid4 td:last-child {{ padding-right:0; }}
table.inner td {{ border:none; padding:0; vertical-align:middle; }}
td.muted-cell {{ font-size:8.6px; color:{c['muted']}; line-height:1.5; padding-left:8px !important; }}
tbody tr:nth-child(even) {{ background:transparent; }}
.kv-table th {{ text-align:left; vertical-align:top; background:{c['surface2']}; color:{c['muted']}; font-size:8.4px; text-transform:none; letter-spacing:0; font-weight:700; }}
.kv-table td {{ font-size:9px; vertical-align:top; }}
ul.tight {{ margin:0; padding-left:14px; font-size:9px; }} ul.tight li {{ margin:1px 0; }}
.num-table td, .cmp-table td, .score-table td, .appendix-table td {{ white-space:nowrap; }}
.cmp-table th, .score-table th {{ font-size:7.4px; white-space:normal; vertical-align:bottom; }}
.cmp-table td {{ font-size:8.4px; padding:4px 5px; }}
.cmp-table td:first-child {{ white-space:nowrap; }}
.score-table td {{ font-size:8.4px; padding:3.5px 5px; }}
.appendix-table td {{ font-size:8.3px; padding:3.5px 5px; }}
.appendix-table td:first-child, .appendix-table td:last-child {{ white-space:normal; }}
.findings-table td {{ vertical-align:top; font-size:8.8px; }}
.findings-table td:first-child {{ white-space:nowrap; }}
.plan-table td, .method-table td, .bom-table td {{ vertical-align:top; font-size:8.8px; }}
.map-table td {{ font-size:8.2px; }}
.legend {{ margin-top:4px; }}
.leg-row {{ font-size:8.2px; margin:2px 0; white-space:nowrap; }}
.radar-swatch {{ display:inline-block; width:8px; height:8px; border-radius:2px; margin-right:5px; vertical-align:middle; }}
table.stack-wrap {{ border-collapse:collapse; }}
table.stack-wrap td {{ border:none; padding:3px 0; }}
table.stack-wrap td.label {{ width:40px; font-size:9px; font-weight:700; }}
table.stackbar {{ width:100%; table-layout:fixed; border-collapse:collapse; border:1px solid {c['border']}; }}
table.stackbar td.seg {{ padding:0; height:18px; overflow:hidden; white-space:nowrap; font-size:8px; font-weight:700; text-align:center; border:none; }}
.gauge-card {{ padding:6px 2px 2px; }}
/* cover */
.cover-page {{ page:cover; position:relative; height:262mm; page-break-after:always; }}
.cv-top {{ display:flex; justify-content:space-between; align-items:center; border-bottom:2px solid {c['primary_dark']}; padding-bottom:8px; min-height:44px; }}
.cv-top .cover-logo {{ max-height:46px; max-width:170px; }}
.cv-brand {{ font-size:11px; font-weight:800; letter-spacing:.06em; color:{c['primary_dark']}; }}
.cv-main {{ margin-top:30mm; }}
.cv-title {{ font-size:30px; font-weight:800; color:{c['primary_dark']}; line-height:1.15; margin:4px 0 4px; }}
.cv-project {{ font-size:15px; font-weight:700; color:{c['ink']}; margin-bottom:10px; }}
.cv-sub {{ font-size:10.5px; color:{c['muted']}; line-height:1.5; margin-bottom:8px; }}
table.cv-grid {{ margin-top:16mm; }}
table.cv-grid > tbody > tr > td, table.cv-grid > tr > td {{ border:none; padding:0; vertical-align:top; }}
.cv-h {{ font-size:8.5px; font-weight:700; letter-spacing:.1em; text-transform:uppercase; color:{c['accent']}; margin-bottom:4px; }}
.cv-meta td {{ border-bottom:1px solid {c['divider']}; padding:3px 4px; font-size:9px; }}
.cv-meta td:first-child {{ color:{c['muted']}; width:42%; }}
ol.toc {{ margin:0; padding-left:0; list-style:none; font-size:9.4px; line-height:1.8; }}
ol.toc a {{ color:{c['ink']}; text-decoration:none; }}
ol.toc a::after {{ content: leader('.') target-counter(attr(href), page); color:{c['muted']}; }}
.toc-num {{ display:inline-block; width:16px; color:{c['primary_dark']}; font-weight:700; }}
.cv-bottom {{ position:absolute; left:0; right:0; bottom:0; }}
.cv-hist th, .cv-hist td {{ font-size:8.4px; padding:3px 5px; }}
table.cv-sign {{ border-collapse:collapse; }}
table.cv-sign td {{ width:33.3%; border:1px solid {c['border']}; padding:5px 7px; vertical-align:top; }}
.sign-role {{ font-size:8px; font-weight:700; color:{c['muted']}; text-transform:uppercase; letter-spacing:.06em; }}
.sign-space {{ height:42px; }}
.sign-name {{ font-size:9px; font-weight:700; border-top:1px dashed {c['border']}; padding-top:3px; min-height:14px; }}
"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
SECTION_KEYS = ["scope", "existing", "demand", "options", "scores", "tco", "design", "bom", "arch", "alloc", "scale",
                "risks", "plan", "method", "vms", "glossary"]
SEV_ORDER = {"critical": 0, "warning": 1, "info": 2, "ok": 3}


def _apply_report_options(result: Dict[str, Any], opts: Dict[str, Any]) -> None:
    """Content edits by the person preparing the report: headline, hidden / extra findings.
    Counts and readiness are recomputed from what is actually printed."""
    a = result["analysis"]
    if (opts.get("headline") or "").strip():
        result["recommendation"]["headline"] = opts["headline"].strip()
    hidden = set(opts.get("hiddenFindings") or [])
    fnd = [f for f in a["findings"] if f.get("id") not in hidden]
    for i, x in enumerate(opts.get("extraFindings") or []):
        if not (x.get("title") or "").strip():
            continue
        sev = x.get("severity") if x.get("severity") in SEV_ORDER else "info"
        fnd.append({"severity": sev, "area": (x.get("area") or "Khác").strip(), "title": x["title"].strip(),
                    "detail": (x.get("detail") or "").strip(), "action": (x.get("action") or "").strip(), "id": f"custom-{i}", "custom": True})
    fnd.sort(key=lambda f: SEV_ORDER.get(f["severity"], 9))
    a["findings"] = fnd
    a["findingCounts"] = {k: sum(1 for f in fnd if f["severity"] == k) for k in SEV_ORDER}
    c = a["findingCounts"]
    a["readiness"] = "crit" if c["critical"] else ("warn" if c["warning"] else "ok")


def render_full_html(vms: List[sizing.VmSpec], params: sizing.SizingParams, profiles: List[sizing.HostProfile],
                     existing: Optional[List[Dict[str, Any]]] = None, policy_txt: str = "", currency: str = "VND",
                     logo_data_url: Optional[str] = None, prepared_by: str = "", mode: str = "new",
                     target_cluster: Optional[str] = None, include_new: bool = True, include_existing: bool = True,
                     customer_name: str = "", prepared_org: str = "", document_code: str = "",
                     version: str = "", approved_by: str = "", customer_contact: str = "", version_note: str = "",
                     input_source: str = "", input_date: str = "", out_of_scope: Optional[List[str]] = None,
                     report_options: Optional[Dict[str, Any]] = None, **_ignored) -> str:
    opts = dict(report_options or {})
    sections = opts.get("sections") or {}
    on = lambda k: sections.get(k, True) is not False  # noqa: E731  — every section is on unless explicitly turned off

    result = sizing.build_sizing(vms, params, profiles)
    _apply_report_options(result, opts)
    best_idx = result["recommendedIndex"]
    best = result["results"][best_idx] if best_idx is not None else None
    existing = existing or []
    generated = _now_str()
    date_txt = generated.split(" ")[-1] if " " in generated else generated  # "HH:MM dd/mm/yyyy" → date only
    env = params.environment.strip()
    project = env or "dự án mới"
    doc = {"org": prepared_org.strip(), "customer": customer_name.strip(), "contact": customer_contact.strip(),
           "code": document_code.strip(), "version": (version or "1.0").strip(), "date": date_txt,
           "preparedBy": prepared_by.strip(), "approvedBy": approved_by.strip(), "versionNote": version_note.strip(),
           "inputSource": input_source.strip(), "inputDate": input_date.strip(), "outOfScope": out_of_scope or []}
    a = result["analysis"]
    target = _pick_target(existing, target_cluster) if mode == "reuse" else None
    has_new = bool(best) and (mode != "reuse" or include_new)

    # Section plan: (id, title, subtitle, builder(num) -> html, cls, numbered)
    fc = a["findingCounts"]
    plan: List[tuple] = [("sec-summary", "Tóm tắt", "", None, "", True)]
    if on("scope"):
        plan.append(("sec-scope", "Phạm vi & dữ liệu đầu vào", "", lambda num: _scope(result, params, doc), "", True))
    if mode == "reuse" and on("existing"):
        def _existing_body(num):
            if not existing:
                return '<div class="small-note">Không có dữ liệu cluster hiện hữu (chưa đồng bộ vCenter hoặc chưa import host/VM).</div>'
            return _vn_html(_existing_chart(existing) + _existing_table(existing, policy_txt, highlight=target["name"] if target else None)
                            + (_target_detail(target, result["demand"]) if target else ""))
        plan.append(("sec-existing", "Hạ tầng hiện hữu", "Khả năng tiếp nhận hệ thống mới trên các cụm đang vận hành; dòng tô màu là cụm được đề xuất.",
                     _existing_body, "", True))
    if on("demand"):
        plan.append(("sec-demand", "Nhu cầu tài nguyên", "", lambda num: _demand(result, params, best), "", True))
    if has_new and on("options"):
        plan.append(("sec-options", "So sánh phương án" + (" (trang bị mới)" if mode == "reuse" else ""), "",
                     lambda num: _options(result, currency, {"scores": on("scores")})
                     + f'<div class="callout" style="margin-top:8px;"><b>Phương án đề xuất.</b> {esc(vn(result["recommendation"]["detail"]))}</div>', "", True))
    if has_new and on("design"):
        plan.append(("sec-design", "Cấu hình đề xuất", esc(best["profile"]["name"]) + f" × {best['hostsTotal']} host",
                     lambda num: _design(result, best, params, num, {"bom": on("bom"), "arch": on("arch"), "alloc": on("alloc")}), "", True))
    if has_new and on("tco") and best["advisory"]["costs"].get("tco"):
        plan.append(("sec-tco", f"Chi phí & TCO {best['advisory']['costs']['years']} năm", "", lambda num: _tco(result, best, currency), "", True))
    if has_new and on("scale"):
        plan.append(("sec-scale", "Khả năng mở rộng", "", lambda num: _scale(result, best_idx, params), "", True))
    if on("risks"):
        plan.append(("sec-risks", "Rủi ro & khuyến nghị",
                     f"{fc.get('critical', 0)} nghiêm trọng · {fc.get('warning', 0)} cảnh báo · {fc.get('info', 0)} lưu ý · {fc.get('ok', 0)} đạt",
                     lambda num: _findings(a["findings"]) if a["findings"] else '<div class="small-note">Không có nhận định.</div>', "", True))
    if on("plan"):
        plan.append(("sec-plan", "Kế hoạch triển khai", "", lambda num: _plan(a["nextSteps"]), "", True))
    if on("method"):
        plan.append(("sec-method", "Giả định & cách tính", "", lambda num: _method(result, best, params, num), "", True))
    vm_dicts = [{"name": v.name, "qty": v.qty, "vcpu": v.vcpu, "ramGB": v.ram_gb, "diskGB": v.disk_gb, "note": v.note} for v in vms]
    appendices = []
    if on("vms"):
        appendices.append(("app-vms", "Danh sách máy ảo", "Theo dữ liệu khách hàng cung cấp.", lambda num: _vm_appendix(vm_dicts, result["demand"])))
    if on("glossary"):
        appendices.append(("app-glossary", "Thuật ngữ", "", lambda num: _glossary(params)))

    numbered = []
    for i, item in enumerate(plan, 1):
        numbered.append((str(i),) + item)
    risks_num = next((nm for nm, sid, *_ in numbered if sid == "sec-risks"), None)
    letters = "ABCDEFGH"
    app_items = [(letters[i], sid, f"Phụ lục {letters[i]}. {t}", sub, fn) for i, (sid, t, sub, fn) in enumerate(appendices)]
    glossary_letter = next((ltr for ltr, sid, *_ in app_items if sid == "app-glossary"), None)

    toc = [(sid, nm, t) for nm, sid, t, *_ in numbered] + [(sid, ltr, title) for ltr, sid, title, _, _ in app_items]
    logo_html = f'<img class="cover-logo" src="{logo_data_url}" alt="logo"/>' if logo_data_url else ""
    cover = _cover(result, best, params, doc, logo_html, toc)

    secs = []
    for nm, sid, title, sub, fn, cls, _ in numbered:
        if sid == "sec-summary":
            body = _summary(result, best, existing, currency, mode, target, include_new, include_existing, project,
                            {**opts, "risksNum": risks_num})
        else:
            body = fn(nm)
        if sid == "sec-scope" and glossary_letter != "B":
            body = body.replace("Thuật ngữ: xem Phụ lục B.", f"Thuật ngữ: xem Phụ lục {glossary_letter}." if glossary_letter else "")
        secs.append(_section(sid, nm, title, sub, body, cls))
    for i, (ltr, sid, title, sub, fn) in enumerate(app_items):
        secs.append(_section(sid, "", title, sub, fn(ltr), "appendix" if i == 0 else ""))

    header_left = " · ".join(x for x in [doc["org"], "Đề xuất cấu hình máy chủ" + (f" – {doc['customer']}" if doc["customer"] else "")] if x)
    header_right = " · ".join(x for x in [doc["code"], f"Phiên bản {doc['version']}"] if x)
    css = _base_css("Đề xuất cấu hình máy chủ", date_txt) + _exec_visual_css() + _planning_css() + _css(header_left, header_right, date_txt)
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><title>Đề xuất cấu hình máy chủ</title><style>{css}</style></head>
<body>{cover}{''.join(secs)}</body></html>"""


def build_full_pdf(*args, **kwargs) -> bytes:
    return html_to_pdf_bytes(render_full_html(*args, **kwargs))
