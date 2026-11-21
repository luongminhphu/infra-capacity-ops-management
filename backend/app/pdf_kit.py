"""Toolkit every PDF report imports (one look for all reports, HSC colours, A4): CSS, header / tiles,
SVG charts, number formats, HTML → PDF. Report modules import from here, not from report_pdf's
internals; report_pdf.py keeps the capacity reports themselves and implements these for now.

Public names drop the leading underscore (base_css, now_str, stat_tiles_html…); the underscored
names stay importable during the move."""
from .report_pdf import (  # noqa: F401
    _fmt_short_date,
    AI_BOX,
    COLORS,
    PRIORITY_LABEL_VI,
    STATUS_COLOR,
    _base_css,
    _exec_visual_css,
    _finite,
    _nice_ceiling,
    _now_str,
    _stat_tiles_html,
    bar_cell,
    esc,
    fmt_int,
    fmt_num,
    fmt_pct,
    html_to_pdf_bytes,
    safe_url_fetcher,
    status_pill,
    svg_bar_chart,
    svg_donut,
    svg_gauge,
    svg_line_chart,
)

base_css = _base_css
exec_visual_css = _exec_visual_css
finite = _finite
nice_ceiling = _nice_ceiling
now_str = _now_str
stat_tiles_html = _stat_tiles_html

short_date = _fmt_short_date          # ISO date / datetime → dd/mm/yyyy ("—" when empty)


def vn(v, decimals: int = 0) -> str:
    """vi-VN number for report text, 0 decimals by default ("—" for None / NaN): the short form the report_*.py
    modules use everywhere (fmt_num defaults to 1 decimal)."""
    return fmt_num(v, decimals)


def data_table(head, rows, num: tuple = ()) -> str:
    """HSC data table: `head` plain text (escaped here), `rows` cells already escaped HTML, `num` = right-aligned
    column indexes."""
    th = "".join(f'<th class="{"num" if i in num else ""}">{esc(h)}</th>' for i, h in enumerate(head))
    tr = "".join("<tr>" + "".join(f'<td class="{"num" if i in num else ""}">{c}</td>' for i, c in enumerate(r)) + "</tr>"
                 for r in rows)
    return f'<table class="data-table"><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table>'


def level_pill(level: str) -> str:
    """Status mark of one of the four levels (shared/levels.py)."""
    from .shared.levels import LEVEL_TEXT
    return status_pill({"text": LEVEL_TEXT.get(level, "NA"), "cls": level if level in LEVEL_TEXT else "na"})
