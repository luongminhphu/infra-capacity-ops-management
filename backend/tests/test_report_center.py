"""CLAUDE.md rule 10: every report of every feature is gathered in Tài liệu → Báo cáo (Trung tâm báo cáo).

Each export of app/exports.py names where it sits on the page (`center`). This test reads the page and its script and
fails when a new export is registered without a button / shortcut there, or when a button points to nothing."""
import re
from pathlib import Path

from app.exports import EXPORTS

ROOT = Path(__file__).resolve().parents[2] / "frontend"
HTML = (ROOT / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "js" / "views" / "reports.js").read_text(encoding="utf-8")


def _reports_section() -> str:
    start = HTML.index('id="viewReports"')
    end = HTML.index("</section>\n", HTML.index('id="rptStatus"'))
    return HTML[start:end]


SECTION = _reports_section()
BUTTONS = set(re.findall(r'data-report="([a-z0-9-]+)"', SECTION))
SPEND = set(re.findall(r'data-spend-export="([a-z]+)"', SECTION))
NAV = set(re.findall(r'data-nav-link="([a-z0-9-]+)"', SECTION))
ACTIONS = set(re.findall(r"^\s{2}'([a-z0-9-]+)': \{ label:", JS, re.M))


def test_every_export_is_reachable_from_the_report_centre():
    missing = []
    for e in EXPORTS:
        assert e.get("center"), f"{e['id']}: add `center` in app/exports.py (CLAUDE.md rule 10)"
        for c in e["center"]:
            if c.startswith("record:"):
                ok = c[len("record:"):] in NAV
            elif c.startswith("spend:"):
                ok = c[len("spend:"):] in SPEND
            else:
                ok = c in BUTTONS and c in ACTIONS
            if not ok:
                missing.append(f"{e['id']} → {c}")
    assert not missing, "Not on Tài liệu → Báo cáo (index.html #viewReports + reports.js RPT_ACTIONS): " + ", ".join(missing)


def test_every_report_button_has_an_action_and_an_export():
    assert BUTTONS <= ACTIONS, f"buttons without action: {sorted(BUTTONS - ACTIONS)}"
    declared = {c for e in EXPORTS for c in e["center"] if ":" not in c}
    extra = BUTTONS - declared - {"sizing-template"}          # Excel template to fill in, not a report
    assert not extra, f"report buttons not declared in app/exports.py `center`: {sorted(extra)}"
