"""Toolkit every PDF report imports (one look for all reports, HSC colours, A4): CSS, header / tiles,
SVG charts, number formats, HTML → PDF. Report modules import from here, not from report_pdf's
internals; report_pdf.py keeps the capacity reports themselves and implements these for now.

Public names drop the leading underscore (base_css, now_str, stat_tiles_html…); the underscored
names stay importable during the move."""
from .report_pdf import (  # noqa: F401
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
