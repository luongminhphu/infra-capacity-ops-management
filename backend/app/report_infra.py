"""PDF "Sức khoẻ hạ tầng" (A4, HSC): datastore đầy / sắp đầy, mảng lưu trữ, snapshot VM, alarm vCenter, sức khoẻ host.

  Tổng kết → 1 Datastore → 2 Mảng lưu trữ → 3 Snapshot VM → 4 Alarm vCenter → 5 Sức khoẻ host
Numbers come from infra.report_data (same rows as the Sức khoẻ hạ tầng pages and the Excel)."""
from typing import Any, Dict, List

from .pdf_kit import base_css, esc, exec_visual_css, html_to_pdf_bytes, now_str, stat_tiles_html, status_pill

MAX_ROWS = 60                     # per section; the Excel has every row
PILL = {"crit": "crit", "bad": "bad", "warn": "warn", "ok": "ok"}
WORD = {"crit": "VƯỢT NGƯỠNG", "bad": "CẢNH BÁO", "warn": "THEO DÕI", "ok": "AN TOÀN"}
RANK = {"crit": 0, "bad": 1, "warn": 2, "ok": 3}


def _vn(v: Any, d: int = 0) -> str:
    if v is None:
        return "—"
    s = f"{float(v):,.{d}f}"
    return s.replace(",", "\0").replace(".", ",").replace("\0", ".")


def _date(iso: Any) -> str:
    s = str(iso or "")
    return f"{s[8:10]}/{s[5:7]}/{s[:4]}" if len(s) >= 10 else "—"


def _table(head: List[str], rows: List[List[str]], num: tuple = ()) -> str:
    th = "".join(f'<th class="{"num" if i in num else ""}">{esc(h)}</th>' for i, h in enumerate(head))
    tr = "".join("<tr>" + "".join(f'<td class="{"num" if i in num else ""}">{c}</td>' for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f'<table class="data-table"><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table>'


def _pill(level: str) -> str:
    return status_pill({"text": WORD.get(level, "NA"), "cls": PILL.get(level, "na")})


def _more(rows: list, shown: int) -> str:
    return f'<div class="small-note">… và {_vn(len(rows) - shown)} dòng khác — xem đủ trong file Excel.</div>' if len(rows) > shown else ""


def _issues(rows: list) -> list:
    return [r for r in rows if r.get("level") != "ok"]


def render_html(d: Dict[str, Any], username: str, scope_label: str = "") -> str:
    generated = now_str()
    ds, arrays, vols, sn, al, hh = d["datastores"], d["storage"]["arrays"], d["storage"]["volumes"], d["snapshots"], d["alarms"], d["health"]
    levels = [x["level"] for x in ds + arrays + sn + al + hh]
    worst = min(levels, key=lambda lv: RANK.get(lv, 3)) if levels else "ok"
    soonest = min((x["daysToFull"] for x in ds if x.get("daysToFull") is not None), default=None)
    ds_bad = [x for x in ds if x["level"] in ("crit", "bad")]
    red = sum(1 for a in al if a["status"] == "red")
    headline = (f"{_vn(len(ds))} datastore ({_vn(len(ds_bad))} cảnh báo trở lên), {_vn(len(arrays))} mảng lưu trữ, "
                f"{_vn(len(sn))} VM có snapshot, {_vn(len(al))} alarm ({_vn(red)} đỏ), {_vn(len(_issues(hh)))}/{_vn(len(hh))} host có vấn đề.")
    points = []
    if ds_bad:
        top = ds_bad[0]
        points.append(f"Datastore {top['name']} dùng {_vn(top['usedPct'], 1)}%"
                      + (f", đầy sau khoảng {_vn(top['daysToFull'])} ngày" if top.get("daysToFull") is not None else "")
                      + " — giải phóng dung lượng hoặc mở rộng.")
    elif soonest is not None:
        points.append(f"Datastore đầy sớm nhất sau khoảng {_vn(soonest)} ngày theo xu hướng 30 ngày.")
    else:
        points.append("Không datastore nào ở mức cảnh báo.")
    old = [x for x in sn if x["level"] in ("crit", "bad")]
    points.append(f"{_vn(len(old))} VM có snapshot cũ / lớn cần xoá sau khi xác nhận với owner." if old else "Không có snapshot vượt ngưỡng.")
    unack = sum(1 for a in al if not a["acknowledged"])
    points.append(f"{_vn(unack)} alarm vCenter chưa ai xác nhận." if unack else "Mọi alarm đã được xác nhận trên vCenter.")
    arr_bad = _issues(arrays)
    if arrays:
        points.append(f"{_vn(len(arr_bad))}/{_vn(len(arrays))} mảng lưu trữ có vấn đề." if arr_bad else "Mọi mảng lưu trữ ở mức an toàn.")
    lst = "".join(f'<li class="rec-sum-item"><span class="rec-num">{i}</span><span class="rec-sum-text">{esc(p)}</span></li>'
                  for i, p in enumerate(points, 1))
    tiles = stat_tiles_html([
        {"label": "Datastore cảnh báo", "value": _vn(len(ds_bad)), "sub": f"trên {_vn(len(ds))} datastore", "cls": "bad" if ds_bad else "ok"},
        {"label": "Đầy sớm nhất", "value": f"{_vn(soonest)} ngày" if soonest is not None else "—", "sub": "theo xu hướng 30 ngày",
         "cls": "warn" if soonest is not None and soonest < 90 else "na"},
        {"label": "Snapshot VM", "value": _vn(len(sn)), "sub": f"{_vn(sum((x['sizeGB'] or 0) for x in sn), 1)} GB", "cls": "bad" if old else "na"},
        {"label": "Alarm", "value": _vn(len(al)), "sub": f"{_vn(red)} đỏ · {_vn(unack)} chưa xác nhận", "cls": "crit" if red else "na"},
        {"label": "Host có vấn đề", "value": _vn(len(_issues(hh))), "sub": f"trên {_vn(len(hh))} host", "cls": "bad" if _issues(hh) else "ok"}])

    ds_rows = [[_pill(x["level"]), f"<b>{esc(x['name'])}</b><br><span class='small-note'>{esc(x['vcenterKey'])} · {esc(x['type'] or '')}</span>",
                esc(", ".join(x["clusters"][:3]) or "—"), _vn(x["capacityGB"]), _vn(x["usedPct"], 1), _vn(x["provisionedPct"], 1),
                _vn(x["growthGBPerDay"], 2), f"{_vn(x['daysToFull'])}" if x.get("daysToFull") is not None else "—",
                esc("; ".join(x["reasons"]) or "—")] for x in ds[:MAX_ROWS]]
    ds_html = _table(["Mức", "Datastore", "Cluster", "Dung lượng (GB)", "Đã dùng (%)", "Cấp phát (%)", "Tăng (GB/ngày)", "Đầy sau (ngày)",
                      "Lý do"], ds_rows, (3, 4, 5, 6, 7)) + _more(ds, MAX_ROWS) if ds else '<div class="small-note">Chưa có dữ liệu datastore.</div>'

    arr_rows = [[_pill(a["level"]), f"<b>{esc(a['name'])}</b><br><span class='small-note'>{esc(a['vendor'])} {esc(a['model'] or '')}</span>",
                 _vn(a["capacityGB"]), _vn(a["usedPct"], 1), _vn(a["dataReduction"], 1), _vn(a["latencyReadMs"], 2),
                 _vn(a["latencyWriteMs"], 2), _vn(len(a["alerts"])), esc("; ".join(a["reasons"]) or "—")] for a in arrays]
    mapped = sum(1 for v in vols if v["datastores"])
    arr_html = (_table(["Mức", "Mảng", "Dung lượng (GB)", "Đã dùng (%)", "Giảm dữ liệu", "Độ trễ đọc (ms)", "Độ trễ ghi (ms)", "Alert", "Lý do"],
                       arr_rows, (2, 3, 4, 5, 6, 7))
                + f'<div class="small-note">{_vn(len(vols))} volume / LUN, {_vn(mapped)} đã ghép với datastore VMware (NAA / NFS).</div>'
                if arrays else '<div class="small-note">Chưa khai nguồn storage (Pure / NetApp).</div>')

    sn_rows = [[_pill(x["level"]), f"<b>{esc(x['vm'])}</b><br><span class='small-note'>{esc(x['vcenterKey'])} · {esc(x['cluster'] or '')}</span>",
                esc(x.get("ip") or "—"), _vn(x["count"]), _vn(x["ageDays"]), _vn(x["sizeGB"], 1), "Có" if x["backupLike"] else "",
                esc("; ".join(x["reasons"]) or "—")] for x in sn[:MAX_ROWS]]
    sn_html = _table(["Mức", "VM", "IP", "Số snapshot", "Cũ nhất (ngày)", "Dung lượng (GB)", "Của backup", "Lý do"], sn_rows, (3, 4, 5)) \
        + _more(sn, MAX_ROWS) if sn else '<div class="small-note">Không VM nào có snapshot.</div>'

    al_rows = [[_pill(a["level"]), esc(a["alarm"] or "—"), f"{esc(a['entity'] or '—')}<br><span class='small-note'>{esc(a['entityType'] or '')} · "
                f"{esc(a['vcenterKey'])}</span>", esc(a.get("ip") or "—"), esc(a["cluster"] or "—"), _date(a["at"]),
                "Đã xác nhận" if a["acknowledged"] else "Chưa"]
               for a in al[:MAX_ROWS]]
    al_html = _table(["Mức", "Alarm", "Đối tượng", "IP (VM)", "Cluster", "Từ ngày", "Xác nhận"], al_rows) + _more(al, MAX_ROWS) \
        if al else '<div class="small-note">Không có alarm đang kích hoạt.</div>'

    bad_hosts = _issues(hh)
    hh_rows = [[_pill(h["level"]), f"<b>{esc(h['host'])}</b><br><span class='small-note'>{esc(h['vcenterKey'])}</span>", esc(h["cluster"] or "—"),
                esc(h["connection"] or "—"), "Có" if h["maintenance"] else "", esc("; ".join(h["reasons"][:6]) or "—")] for h in bad_hosts[:MAX_ROWS]]
    hh_html = _table(["Mức", "Host", "Cluster", "Kết nối", "Bảo trì", "Cảm biến / lý do"], hh_rows) + _more(bad_hosts, MAX_ROWS) \
        if bad_hosts else f'<div class="small-note">{_vn(len(hh))} host, không host nào có cảm biến bất thường.</div>'

    scope = f"<span>Phạm vi: <b>{esc(scope_label)}</b></span>" if scope_label else "<span>Phạm vi: <b>toàn bộ</b></span>"
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><style>{base_css('Sức khoẻ hạ tầng', generated)}{exec_visual_css()}</style></head>
<body>
  <div class="cover-header"><div>
    <div class="eyebrow">Infrastructure Capacity Ops · Sức khoẻ hạ tầng · {esc(generated.split(' ')[-1])}</div>
    <div class="cover-title" style="margin-bottom:6px;">Sức khoẻ hạ tầng</div>
    <div class="meta-row">{scope}<span>Lập bởi: <b>{esc(username)}</b></span><span>Lúc: <b>{esc(generated)}</b></span></div>
  </div></div>
  <div class="exec-brief {PILL.get(worst, 'ok')}">
    <div class="exec-brief-head"><span class="status-pill {PILL.get(worst, 'ok')}">{esc(WORD.get(worst, ''))}</span><span class="exec-brief-title">Tổng kết — vấn đề &amp; việc cần làm</span></div>
    <div class="exec-brief-headline">{esc(headline)}</div>
    <ol class="rec-sum">{lst}</ol>
    <div class="stat-row" style="margin-bottom:0;">{tiles}</div>
  </div>
  <div class="section breakable"><div class="section-title">1. Datastore</div>
    <div class="section-sub">Sắp theo mức rồi theo % đã dùng; “Đầy sau” theo xu hướng tăng 30 ngày.</div>{ds_html}</div>
  <div class="section breakable"><div class="section-title">2. Mảng lưu trữ</div>{arr_html}</div>
  <div class="section breakable"><div class="section-title">3. Snapshot VM</div>
    <div class="section-sub">Snapshot tồn lâu làm đầy datastore và chậm I/O; snapshot của phần mềm backup đánh dấu “Của backup”.</div>{sn_html}</div>
  <div class="section breakable"><div class="section-title">4. Alarm vCenter</div>{al_html}</div>
  <div class="section breakable"><div class="section-title">5. Sức khoẻ host</div>
    <div class="section-sub">Chỉ host có vấn đề (mất kết nối, cảm biến vàng / đỏ).</div>{hh_html}</div>
</body></html>"""


def build_pdf(d: Dict[str, Any], username: str, scope_label: str = "") -> bytes:
    return html_to_pdf_bytes(render_html(d, username, scope_label))
