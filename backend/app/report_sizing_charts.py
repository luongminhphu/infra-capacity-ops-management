"""Inline-SVG charts for the sizing consulting report.

Same visual language as report_pdf.py (soft plot background, white grid,
values printed on the marks, palette from COLORS) so the sizing report reads
as part of the same product. Everything is static SVG — WeasyPrint renders it
with no JavaScript and no network.
"""
from typing import Any, Dict, List, Optional

from .report_pdf import COLORS, _finite, _nice_ceiling, esc

FONT = 'font-family="Roboto, Noto Sans, sans-serif"'

# Categorical palette for candidate host profiles (max 8) — first two match the
# app's primary / accent, the rest are distinguishable on print.
SERIES_COLORS = ["#2681FF", "#0891b2", "#7c3aed", "#ca8a04", "#db2777", "#15803d", "#ea580c", "#475467"]

# Capacity-allocation segments (cluster stack chart)
SEG_COLORS = {
    "request": "#2681FF",
    "growth": "#93c5fd",
    "free": "#bbf7d0",
    "headroom": "#fde68a",
    "redundancy": "#fca5a5",
    "esxi": "#cbd5e1",
}

EMPTY = '<div style="color:#98a2b3;font-size:10px;padding:8px 0;">Không có dữ liệu.</div>'


def money_short(v: Optional[float], currency: str = "VND") -> str:
    if v is None or not _finite(v):
        return "—"
    cur = (currency or "").upper()
    a = abs(v)
    if cur == "VND":
        if a >= 1e9:
            return f"{v / 1e9:,.2f} tỷ".replace(",", "X").replace(".", ",").replace("X", ".")
        if a >= 1e6:
            return f"{v / 1e6:,.0f} tr".replace(",", ".")
        return f"{v:,.0f}".replace(",", ".")
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            return f"{v / div:,.1f}{suf} {currency}".strip()
    return f"{v:,.0f} {currency}".strip()


def svg_hbars(rows: List[Dict[str, Any]], width: int = 330, label_w: int = 92, row_h: int = 18,
              max_value: Optional[float] = None) -> str:
    """Horizontal bars. rows: [{label, value, color?, text?, sub?}] — `text` is printed after the bar."""
    rows = [r for r in rows if _finite(r.get("value"))]
    if not rows:
        return EMPTY
    mx = max_value or max(r["value"] for r in rows) or 1.0
    pad_r = 62
    plot_w = width - label_w - pad_r
    h = len(rows) * row_h + 4
    parts = [f'<svg width="{width}" height="{h}" viewBox="0 0 {width} {h}" {FONT}>']
    for i, r in enumerate(rows):
        y = 2 + i * row_h
        bh = row_h - 7
        bw = max(1.5, max(0.0, r["value"]) / mx * plot_w)
        name = str(r["label"])
        if len(name) > 18:
            name = name[:17] + "…"
        parts.append(f'<text x="{label_w - 5}" y="{y + bh / 2 + 3:.1f}" text-anchor="end" font-size="7.8" fill="{COLORS["ink"]}">{esc(name)}</text>')
        parts.append(f'<rect x="{label_w}" y="{y}" width="{plot_w}" height="{bh}" rx="2" fill="{COLORS["surface2"]}"/>')
        parts.append(f'<rect x="{label_w}" y="{y}" width="{bw:.1f}" height="{bh}" rx="2" fill="{r.get("color") or COLORS["primary"]}"/>')
        text = r.get("text") or f"{r['value']:.0f}"
        parts.append(f'<text x="{label_w + bw + 4:.1f}" y="{y + bh / 2 + 3:.1f}" font-size="7.4" font-weight="700" fill="{COLORS["muted"]}">{esc(text)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def svg_dual_hbars(rows: List[Dict[str, Any]], width: int = 330, label_w: int = 84) -> str:
    """Two thin bars per row (vCPU share / RAM share, 0-100%). rows: [{label, a, b}]."""
    if not rows:
        return EMPTY
    row_h = 21
    pad_r = 34
    plot_w = width - label_w - pad_r
    h = len(rows) * row_h + 4
    parts = [f'<svg width="{width}" height="{h}" viewBox="0 0 {width} {h}" {FONT}>']
    mx = max(max(r["a"], r["b"]) for r in rows) or 1.0
    mx = _nice_ceiling(mx)
    for i, r in enumerate(rows):
        y = 2 + i * row_h
        name = str(r["label"])
        if len(name) > 16:
            name = name[:15] + "…"
        parts.append(f'<text x="{label_w - 5}" y="{y + 10:.1f}" text-anchor="end" font-size="7.8" fill="{COLORS["ink"]}">{esc(name)}</text>')
        for j, (key, color) in enumerate((("a", COLORS["primary"]), ("b", COLORS["accent"]))):
            by = y + 2 + j * 8
            bw = max(1.0, r[key] / mx * plot_w)
            parts.append(f'<rect x="{label_w}" y="{by}" width="{bw:.1f}" height="6.5" rx="1.5" fill="{color}"/>')
            parts.append(f'<text x="{label_w + bw + 3:.1f}" y="{by + 5.6:.1f}" font-size="6.6" font-weight="700" fill="{COLORS["muted"]}">{r[key]:.0f}%</text>')
    parts.append("</svg>")
    return "".join(parts)


def svg_waterfall(steps: List[Dict[str, Any]], width: int = 330, height: int = 170, unit: str = "",
                  fmt=None) -> str:
    """Cumulative steps: first and last bars full height, middle bars float from the previous total."""
    if not steps:
        return EMPTY
    fmt = fmt or (lambda v: f"{v:,.0f}")
    mx = _nice_ceiling(max(s["value"] for s in steps) * 1.12)
    pad_l, pad_r, pad_t, pad_b = 40, 6, 14, 26
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b
    n = len(steps)
    gw = plot_w / n
    bw = gw * 0.58

    def y_of(v):
        return pad_t + plot_h * (1 - max(0.0, min(1.0, v / mx)))

    parts = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" {FONT}>',
             f'<rect x="{pad_l}" y="{pad_t}" width="{plot_w:.1f}" height="{plot_h:.1f}" rx="4" fill="{COLORS["surface2"]}"/>']
    for i in range(5):
        v = mx * i / 4
        y = y_of(v)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" y2="{y:.1f}" stroke="#fff" stroke-width="1.1"/>')
        parts.append(f'<text x="{pad_l - 4}" y="{y + 3:.1f}" text-anchor="end" font-size="7" fill="{COLORS["muted"]}">{esc(fmt(v))}</text>')
    prev = 0.0
    for i, s in enumerate(steps):
        x = pad_l + i * gw + (gw - bw) / 2
        v = s["value"]
        last = i == n - 1
        first = i == 0
        if first or last:
            lo, hi = 0.0, v
            color = COLORS["primary_dark"] if last else COLORS["primary"]
        else:
            lo, hi = min(prev, v), max(prev, v)
            color = COLORS["warn"] if v >= prev else COLORS["ok"]
        y1, y0 = y_of(hi), y_of(lo)
        parts.append(f'<rect x="{x:.1f}" y="{y1:.1f}" width="{bw:.1f}" height="{max(1.0, y0 - y1):.1f}" rx="2" fill="{color}"/>')
        if not first and i < n:
            px = pad_l + (i - 1) * gw + (gw - bw) / 2 + bw
            py = y_of(prev)
            parts.append(f'<line x1="{px:.1f}" y1="{py:.1f}" x2="{x:.1f}" y2="{py:.1f}" stroke="{COLORS["faint"]}" stroke-dasharray="2 2"/>')
        delta = "" if first or last else (("+" if v >= prev else "−") + fmt(abs(v - prev)))
        label = fmt(v) if (first or last) else delta
        parts.append(f'<text x="{x + bw / 2:.1f}" y="{y1 - 3:.1f}" text-anchor="middle" font-size="7.2" font-weight="700" fill="{COLORS["ink"]}">{esc(label)}</text>')
        words = str(s["label"]).split(" ")
        lines, cur = [], ""
        for w in words:
            if cur and len(cur) + len(w) > 13:
                lines.append(cur)
                cur = w
            else:
                cur = (cur + " " + w).strip()
        lines.append(cur)
        for li, ln in enumerate(lines[:2]):
            parts.append(f'<text x="{x + bw / 2:.1f}" y="{pad_t + plot_h + 9 + li * 8.5:.1f}" text-anchor="middle" font-size="6.9" fill="{COLORS["muted"]}">{esc(ln)}</text>')
        prev = v
    if unit:
        parts.append(f'<text x="{pad_l}" y="9" font-size="7" font-weight="700" fill="{COLORS["muted"]}">{esc(unit)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def svg_lines(x_labels: List[str], series: List[Dict[str, Any]], width: int = 330, height: int = 160,
              current_idx: Optional[int] = None, y_label: str = "", marker_y: Optional[float] = None) -> str:
    """Step-like integer line chart (host counts). series: [{name, color, values}]."""
    if not x_labels or not series:
        return EMPTY
    vals = [v for s in series for v in s["values"] if _finite(v)]
    mx = max(vals + [marker_y or 0, 1])
    mx = max(4, int(mx * 1.2 + 0.999))
    pad_l, pad_r, pad_t, pad_b = 26, 8, 12, 20
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b
    n = len(x_labels)

    def x_of(i):
        return pad_l + (plot_w * (i / (n - 1)) if n > 1 else plot_w / 2)

    def y_of(v):
        return pad_t + plot_h * (1 - v / mx)

    parts = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" {FONT}>',
             f'<rect x="{pad_l}" y="{pad_t}" width="{plot_w}" height="{plot_h}" rx="4" fill="{COLORS["surface2"]}"/>']
    step = max(1, mx // 4)
    for v in range(0, mx + 1, step):
        y = y_of(v)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" y2="{y:.1f}" stroke="#fff" stroke-width="1.1"/>')
        parts.append(f'<text x="{pad_l - 4}" y="{y + 3:.1f}" text-anchor="end" font-size="7" fill="{COLORS["muted"]}">{v}</text>')
    if current_idx is not None and 0 <= current_idx < n:
        cx = x_of(current_idx)
        parts.append(f'<line x1="{cx:.1f}" y1="{pad_t}" x2="{cx:.1f}" y2="{pad_t + plot_h}" stroke="{COLORS["primary_dark"]}" stroke-width="1" stroke-dasharray="3 2"/>')
        parts.append(f'<text x="{cx + 3:.1f}" y="{pad_t + 8}" font-size="6.8" font-weight="700" fill="{COLORS["primary_dark"]}">hiện tại</text>')
    if marker_y is not None:
        my = y_of(marker_y)
        parts.append(f'<line x1="{pad_l}" y1="{my:.1f}" x2="{width - pad_r}" y2="{my:.1f}" stroke="{COLORS["crit"]}" stroke-width="1.1" stroke-dasharray="4 3"/>')
    for i, lb in enumerate(x_labels):
        parts.append(f'<text x="{x_of(i):.1f}" y="{pad_t + plot_h + 10}" text-anchor="middle" font-size="6.9" fill="{COLORS["muted"]}">{esc(lb)}</text>')
    for si, s in enumerate(series):
        color = s.get("color") or SERIES_COLORS[si % len(SERIES_COLORS)]
        off = (si - (len(series) - 1) / 2) * 1.2  # tiny vertical offset so equal lines stay visible
        pts = [(x_of(i), y_of(v) + off) for i, v in enumerate(s["values"]) if _finite(v)]
        if not pts:
            continue
        d = " ".join(f"{'M' if k == 0 else 'L'} {x:.1f} {y:.1f}" for k, (x, y) in enumerate(pts))
        parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.8" stroke-linejoin="round"/>')
        for x, y in pts:
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.1" fill="#fff" stroke="{color}" stroke-width="1.4"/>')
    if y_label:
        parts.append(f'<text x="2" y="8" font-size="7" font-weight="700" fill="{COLORS["muted"]}">{esc(y_label)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def svg_projection(points: List[Dict[str, Any]], installed: int, width: int = 330, height: int = 160) -> str:
    """Bars: hosts needed per year; dashed line: hosts installed by this proposal."""
    if not points:
        return EMPTY
    mx = max(4, int(max([p["hosts"] for p in points] + [installed]) * 1.25 + 0.999))
    pad_l, pad_r, pad_t, pad_b = 24, 54, 12, 26
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b
    n = len(points)
    gw = plot_w / n
    bw = gw * 0.55

    def y_of(v):
        return pad_t + plot_h * (1 - v / mx)

    parts = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" {FONT}>',
             f'<rect x="{pad_l}" y="{pad_t}" width="{plot_w:.1f}" height="{plot_h}" rx="4" fill="{COLORS["surface2"]}"/>']
    step = max(1, mx // 4)
    for v in range(0, mx + 1, step):
        y = y_of(v)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{pad_l + plot_w:.1f}" y2="{y:.1f}" stroke="#fff" stroke-width="1.1"/>')
        parts.append(f'<text x="{pad_l - 4}" y="{y + 3:.1f}" text-anchor="end" font-size="7" fill="{COLORS["muted"]}">{v}</text>')
    for i, p in enumerate(points):
        x = pad_l + i * gw + (gw - bw) / 2
        over = p["hosts"] > installed
        color = COLORS["crit"] if over else COLORS["primary"]
        y = y_of(p["hosts"])
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{pad_t + plot_h - y:.1f}" rx="2.5" fill="{color}"/>')
        parts.append(f'<text x="{x + bw / 2:.1f}" y="{y - 3:.1f}" text-anchor="middle" font-size="7.4" font-weight="700" fill="{COLORS["ink"]}">{p["hosts"]}</text>')
        parts.append(f'<text x="{x + bw / 2:.1f}" y="{pad_t + plot_h + 9:.1f}" text-anchor="middle" font-size="7" font-weight="700" fill="{COLORS["muted"]}">{"Hiện tại" if p["year"] == 0 else "Năm " + str(p["year"])}</text>')
        parts.append(f'<text x="{x + bw / 2:.1f}" y="{pad_t + plot_h + 18:.1f}" text-anchor="middle" font-size="6.5" fill="{COLORS["faint"]}">+{p["growthPct"]:.0f}%</text>')
    iy = y_of(installed)
    parts.append(f'<line x1="{pad_l}" y1="{iy:.1f}" x2="{pad_l + plot_w:.1f}" y2="{iy:.1f}" stroke="{COLORS["ok"]}" stroke-width="1.5" stroke-dasharray="5 3"/>')
    parts.append(f'<text x="{pad_l + plot_w + 4:.1f}" y="{iy + 3:.1f}" font-size="7" font-weight="700" fill="{COLORS["ok"]}">Đã lắp {installed}</text>')
    parts.append("</svg>")
    return "".join(parts)


def svg_stack_bars(rows: List[Dict[str, Any]], width: int = 680, label_w: int = 70) -> str:
    """100% stacked horizontal bars. rows: [{label, segments:[{key, label, value}]}]."""
    if not rows:
        return EMPTY
    row_h = 26
    plot_w = width - label_w - 6
    h = len(rows) * row_h + 2
    parts = [f'<svg width="{width}" height="{h}" viewBox="0 0 {width} {h}" {FONT}>']
    for i, r in enumerate(rows):
        y = 2 + i * row_h
        bh = row_h - 8
        total = sum(max(0.0, s["value"]) for s in r["segments"]) or 1.0
        parts.append(f'<text x="{label_w - 6}" y="{y + bh / 2 + 3.5:.1f}" text-anchor="end" font-size="8.6" font-weight="700" fill="{COLORS["ink"]}">{esc(r["label"])}</text>')
        x = label_w
        for s in r["segments"]:
            w = max(0.0, s["value"]) / total * plot_w
            if w <= 0:
                continue
            parts.append(f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="{bh}" fill="{SEG_COLORS.get(s["key"], COLORS["faint"])}"><title>{esc(s["label"])}</title></rect>')
            pct = s["value"] / total * 100
            if w > 26:
                fg = "#ffffff" if s["key"] == "request" else COLORS["ink"]
                parts.append(f'<text x="{x + w / 2:.1f}" y="{y + bh / 2 + 3:.1f}" text-anchor="middle" font-size="7.4" font-weight="700" fill="{fg}">{pct:.0f}%</text>')
            x += w
        parts.append(f'<rect x="{label_w}" y="{y}" width="{plot_w}" height="{bh}" fill="none" stroke="{COLORS["border"]}" rx="2"/>')
    parts.append("</svg>")
    return "".join(parts)


COST_COLORS = {"hw": "#2681FF", "war": "#f59e0b", "lic": "#0891b2"}


def svg_cost_bars(rows: List[Dict[str, Any]], currency: str, width: int = 330) -> str:
    """Stacked TCO per candidate: hardware + warranty extension + licence. rows: [{label, hw, war, lic, best}]."""
    tot = lambda r: (r.get("hw") or 0) + (r.get("war") or 0) + (r.get("lic") or 0)  # noqa: E731
    rows = [r for r in rows if tot(r) > 0]
    if not rows:
        return EMPTY
    label_w, pad_r, row_h = 110, 62, 20
    plot_w = width - label_w - pad_r
    mx = max(tot(r) for r in rows) or 1.0
    h = len(rows) * row_h + 4
    parts = [f'<svg width="{width}" height="{h}" viewBox="0 0 {width} {h}" {FONT}>']
    for i, r in enumerate(rows):
        y = 2 + i * row_h
        bh = row_h - 7
        name = str(r["label"])[:22]
        weight = "800" if r.get("best") else "400"
        parts.append(f'<text x="{label_w - 5}" y="{y + bh / 2 + 3:.1f}" text-anchor="end" font-size="7.8" font-weight="{weight}" fill="{COLORS["ink"]}">{esc(name)}</text>')
        x = label_w
        for key in ("hw", "war", "lic"):
            w = (r.get(key) or 0) / mx * plot_w
            if w > 0:
                parts.append(f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="{bh}" fill="{COST_COLORS[key]}"/>')
                x += w
        parts.append(f'<text x="{x + 4:.1f}" y="{y + bh / 2 + 3:.1f}" font-size="7.4" font-weight="700" fill="{COLORS["ink"]}">{esc(money_short(tot(r), currency))}</text>')
    parts.append("</svg>")
    return "".join(parts)


def svg_yearly_tco(yearly: List[Dict[str, Any]], currency: str, width: int = 680, height: int = 190) -> str:
    """Stacked columns per year (hardware / warranty / licence) + cumulative line on a secondary axis."""
    if not yearly:
        return EMPTY
    pad_l, pad_r, pad_t, pad_b = 46, 58, 16, 30
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b
    n = len(yearly)
    gw = plot_w / n
    bw = min(64.0, gw * 0.52)
    mx = _nice_ceiling(max(y["total"] for y in yearly) * 1.15)
    cmx = _nice_ceiling(max(y["cumulative"] for y in yearly) * 1.08)

    def y_of(v, m):
        return pad_t + plot_h * (1 - max(0.0, min(1.0, v / m)))

    parts = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" {FONT}>',
             f'<rect x="{pad_l}" y="{pad_t}" width="{plot_w:.1f}" height="{plot_h:.1f}" rx="4" fill="{COLORS["surface2"]}"/>']
    for i in range(5):
        v = mx * i / 4
        y = y_of(v, mx)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{pad_l + plot_w:.1f}" y2="{y:.1f}" stroke="#fff" stroke-width="1.1"/>')
        parts.append(f'<text x="{pad_l - 4}" y="{y + 3:.1f}" text-anchor="end" font-size="7" fill="{COLORS["muted"]}">{esc(money_short(v, currency))}</text>')
        parts.append(f'<text x="{pad_l + plot_w + 4}" y="{y + 3:.1f}" font-size="7" fill="{COLORS["primary_dark"]}">{esc(money_short(cmx * i / 4, currency))}</text>')
    pts = []
    for i, yr in enumerate(yearly):
        cx = pad_l + gw * i + gw / 2
        x = cx - bw / 2
        base = pad_t + plot_h
        for key, val in (("hw", yr.get("hardware")), ("war", yr.get("warranty")), ("lic", yr.get("license"))):
            if val:
                hgt = val / mx * plot_h
                parts.append(f'<rect x="{x:.1f}" y="{base - hgt:.1f}" width="{bw:.1f}" height="{hgt:.1f}" fill="{COST_COLORS[key]}"/>')
                base -= hgt
        parts.append(f'<text x="{cx:.1f}" y="{base - 3:.1f}" text-anchor="middle" font-size="7.4" font-weight="700" fill="{COLORS["ink"]}">{esc(money_short(yr["total"], currency))}</text>')
        lbl = f"Năm {yr['year']}"
        parts.append(f'<text x="{cx:.1f}" y="{pad_t + plot_h + 10:.1f}" text-anchor="middle" font-size="7.4" font-weight="700" fill="{COLORS["muted"]}">{lbl}</text>')
        sub = "trong bảo hành" if yr.get("inWarranty") else ("gia hạn BH" if yr.get("warranty") else "chưa có giá BH")
        parts.append(f'<text x="{cx:.1f}" y="{pad_t + plot_h + 19:.1f}" text-anchor="middle" font-size="6.6" fill="{COLORS["faint"]}">{sub}</text>')
        pts.append((cx, y_of(yr["cumulative"], cmx)))
    d = " ".join(f"{'M' if k == 0 else 'L'} {x:.1f} {y:.1f}" for k, (x, y) in enumerate(pts))
    parts.append(f'<path d="{d}" fill="none" stroke="{COLORS["primary_dark"]}" stroke-width="1.6" stroke-dasharray="4 2"/>')
    for x, y in pts:
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.4" fill="#fff" stroke="{COLORS["primary_dark"]}" stroke-width="1.4"/>')
    parts.append(f'<text x="{pad_l + plot_w + 4}" y="{pad_t - 5}" font-size="6.8" font-weight="700" fill="{COLORS["primary_dark"]}">Lũy kế</text>')
    parts.append("</svg>")
    return "".join(parts)
