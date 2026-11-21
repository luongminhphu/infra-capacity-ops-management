"""Registry of every file export of ICO (standard Export menu, super app §3).

One row per export: what it is, where it lives (path / regex), its format, the module switch and
the role needed to run it, and whether a copy can go to Kho báo cáo. The report archive and the
GET /api/exports list both come from here — a new export is declared once."""
import re
from typing import Any, Dict, List, Optional

GROUP_LABELS = {"capacity": "Capacity", "backup": "Backup", "license": "License", "sizing": "Sizing máy chủ",
                "spend": "Ngân sách & chi phí", "renovation": "Cải tạo văn phòng", "bom": "Chuẩn hoá BOM", "inventory": "Tài sản",
                "owners": "Hệ thống & Owner", "hardware": "Phần cứng",
                "infra": "Sức khoẻ hạ tầng", "cmdb": "Sổ CI", "demand": "Nhu cầu hạ tầng"}

# role: None = any signed-in user · "spend:viewer" = spend role at least viewer
# center: where the export sits in Tài liệu → Báo cáo (Trung tâm báo cáo) — CLAUDE.md rule 10:
#   "<key>"          = button data-report="<key>" (action RPT_ACTIONS[key] in frontend/js/views/reports.js)
#   "spend:<fmt>"    = button data-spend-export="<fmt>" (Kế hoạch ngân sách)
#   "record:<page>"  = export of one record (plan, renovation, quote…): shortcut data-nav-link="<page>" on the
#                      "Báo cáo theo hồ sơ" card; the file itself is downloaded from that page
# tests/test_report_center.py fails when a new export is not reachable from the report centre.
EXPORTS: List[Dict[str, Any]] = [
    {"id": "capacity-pdf", "group": "capacity", "label": "Báo cáo Capacity (Vận hành / Kỹ thuật / Quy hoạch)", "format": "pdf",
     "method": "GET", "path": "/api/reports/pdf", "rx": r"^/api/reports/pdf$", "feature": None, "role": None, "archive": True, "center": ["cap-exec", "cap-tech", "plan-pdf"]},
    {"id": "inventory-csv", "group": "inventory", "label": "Host / VM / phát hiện (CSV)", "format": "csv",
     "method": "GET", "path": "/api/export/csv", "rx": r"^/api/export/csv$", "feature": None, "role": None, "archive": False, "center": ["cap-csv"]},
    {"id": "inventory-xlsx", "group": "inventory", "label": "Bảng đang xem (Excel)", "format": "xlsx",
     "method": "POST", "path": "/api/export/xlsx", "rx": r"^/api/export/xlsx$", "feature": None, "role": None, "archive": False, "center": ["inv-hosts", "inv-vms", "lic-assess", "lic-hw", "lic-os"]},
    {"id": "backup-pdf", "group": "backup", "label": "Báo cáo Backup (PDF)", "format": "pdf",
     "method": "GET", "path": "/api/backup/report/pdf", "rx": r"^/api/backup/report/pdf$", "feature": "backup", "role": None, "archive": True, "center": ["bk-pdf"]},
    {"id": "backup-xlsx", "group": "backup", "label": "Dữ liệu Backup (Excel)", "format": "xlsx",
     "method": "GET", "path": "/api/backup/report/xlsx", "rx": r"^/api/backup/report/xlsx$", "feature": "backup", "role": None, "archive": False, "center": ["bk-xlsx"]},
    {"id": "license-pdf", "group": "license", "label": "Tóm tắt License (PDF)", "format": "pdf",
     "method": "GET", "path": "/api/licensing/report/pdf", "rx": r"^/api/licensing/report/pdf$", "feature": "license", "role": None, "archive": True, "center": ["lic-pdf"]},
    {"id": "sizing-pdf", "group": "sizing", "label": "Đề xuất cấu hình máy chủ (PDF)", "format": "pdf",
     "method": "POST", "path": "/api/sizing/pdf", "rx": r"^/api/sizing/pdf$", "feature": "sizing", "role": None, "archive": True, "center": ["sizing-full", "sizing-brief"]},
    {"id": "sizing-xlsx", "group": "sizing", "label": "Sizing (Excel)", "format": "xlsx",
     "method": "POST", "path": "/api/sizing/export", "rx": r"^/api/sizing/export$", "feature": "sizing", "role": None, "archive": False, "center": ["sizing-xlsx"]},
    {"id": "budget-pdf", "group": "spend", "label": "Kế hoạch ngân sách IT (PDF)", "format": "pdf",
     "method": "POST", "path": "/api/spend/reports/budget.pdf", "rx": r"^/api/spend/reports/budget\.pdf$", "feature": "spend", "role": "spend:viewer", "archive": True, "center": ["spend:pdf"]},
    {"id": "budget-xlsx", "group": "spend", "label": "Kế hoạch ngân sách IT (Excel)", "format": "xlsx",
     "method": "POST", "path": "/api/spend/reports/budget.xlsx", "rx": r"^/api/spend/reports/budget\.xlsx$", "feature": "spend", "role": "spend:viewer", "archive": False, "center": ["spend:xlsx"]},
    {"id": "budget-plan-xlsx", "group": "spend", "label": "Kế hoạch ngân sách năm sau (Excel)", "format": "xlsx",
     "method": "GET", "path": "/api/spend/plans/{id}/export.xlsx", "rx": r"^/api/spend/plans/\d+/export\.xlsx$",
     "feature": "spend", "role": "spend:viewer", "archive": False, "center": ["record:spend-plan"]},
    {"id": "renovation-pdf", "group": "renovation", "label": "Cải tạo văn phòng (PDF)", "format": "pdf",
     "method": "GET", "path": "/api/spend/renovations/{code}/export.pdf", "rx": r"^/api/spend/renovations/[^/]+/export\.pdf$",
     "feature": "spend", "role": "spend:viewer", "archive": True, "center": ["record:spend-renovations"]},
    {"id": "renovation-xlsx", "group": "renovation", "label": "Cải tạo văn phòng (Excel)", "format": "xlsx",
     "method": "GET", "path": "/api/spend/renovations/{code}/export.xlsx", "rx": r"^/api/spend/renovations/[^/]+/export\.xlsx$",
     "feature": "spend", "role": "spend:viewer", "archive": False, "center": ["record:spend-renovations"]},
    {"id": "systems-xlsx", "group": "owners", "label": "Hệ thống & Owner (Excel, nhập lại được)", "format": "xlsx",
     "method": "GET", "path": "/api/systems/export.xlsx", "rx": r"^/api/systems/export\.xlsx$", "feature": None, "role": None, "archive": False, "center": ["sys-export"]},
    {"id": "annual-pdf", "group": "demand", "label": "Tổng kết nhu cầu năm & kế hoạch năm sau (PDF)", "format": "pdf",
     "method": "GET", "path": "/api/itsm/annual/report.pdf", "rx": r"^/api/itsm/annual/report\.pdf$", "feature": None, "role": None, "archive": True, "center": ["annual-pdf"]},
    {"id": "annual-xlsx", "group": "demand", "label": "Tổng kết nhu cầu năm & kế hoạch năm sau (Excel)", "format": "xlsx",
     "method": "GET", "path": "/api/itsm/annual/report.xlsx", "rx": r"^/api/itsm/annual/report\.xlsx$", "feature": None, "role": None, "archive": False, "center": ["annual-xlsx"]},
    {"id": "baseline-pdf", "group": "infra", "label": "Baseline cấu hình (PDF)", "format": "pdf",
     "method": "GET", "path": "/api/baseline/report.pdf", "rx": r"^/api/baseline/report\.pdf$", "feature": None, "role": None, "archive": True, "center": ["cfg-pdf"]},
    {"id": "baseline-xlsx", "group": "infra", "label": "Baseline cấu hình (Excel)", "format": "xlsx",
     "method": "GET", "path": "/api/baseline/report.xlsx", "rx": r"^/api/baseline/report\.xlsx$", "feature": None, "role": None, "archive": False, "center": ["cfg-xlsx"]},
    {"id": "hardware-pdf", "group": "hardware", "label": "Sức khoẻ phần cứng (PDF)", "format": "pdf",
     "method": "GET", "path": "/api/hardware/report.pdf", "rx": r"^/api/hardware/report\.pdf$", "feature": None, "role": None, "archive": True, "center": ["hw-pdf"]},
    {"id": "hardware-xlsx", "group": "hardware", "label": "Phần cứng — máy chủ, bảo hành, firmware (Excel)", "format": "xlsx",
     "method": "GET", "path": "/api/hardware/report.xlsx", "rx": r"^/api/hardware/report\.xlsx$", "feature": None, "role": None, "archive": False, "center": ["hw-xlsx"]},
    {"id": "infra-pdf", "group": "infra", "label": "Sức khoẻ hạ tầng (PDF)", "format": "pdf",
     "method": "GET", "path": "/api/infra/report.pdf", "rx": r"^/api/infra/report\.pdf$", "feature": None, "role": None, "archive": True, "center": ["infra-pdf"]},
    {"id": "infra-xlsx", "group": "infra", "label": "Sức khoẻ hạ tầng (Excel)", "format": "xlsx",
     "method": "GET", "path": "/api/infra/report.xlsx", "rx": r"^/api/infra/report\.xlsx$", "feature": None, "role": None, "archive": False, "center": ["infra-xlsx"]},
    {"id": "ci-xlsx", "group": "cmdb", "label": "Sổ CI — CI, khoá chuẩn, liên kết (Excel)", "format": "xlsx",
     "method": "GET", "path": "/api/ci/export.xlsx", "rx": r"^/api/ci/export\.xlsx$", "feature": None, "role": None, "archive": False, "center": ["ci-xlsx"]},
    {"id": "quote-compare-xlsx", "group": "spend", "label": "So sánh báo giá (Excel)", "format": "xlsx",
     "method": "GET", "path": "/api/spend/quotes/{code}/comparison.xlsx", "rx": r"^/api/spend/quotes/[^/]+/comparison\.xlsx$",
     "feature": "spend", "role": "spend:viewer", "archive": False, "center": ["record:spend-quotes"]},
    {"id": "quote-eval-xlsx", "group": "spend", "label": "Đánh giá báo giá (Excel)", "format": "xlsx",
     "method": "GET", "path": "/api/spend/quotes/{code}/evaluation.xlsx", "rx": r"^/api/spend/quotes/[^/]+/evaluation\.xlsx$",
     "feature": "spend", "role": "spend:viewer", "archive": False, "center": ["record:spend-quotes"]},
    {"id": "systems-vm-pdf", "group": "owners", "label": "Danh sách VM theo hệ thống (PDF)", "format": "pdf",
     "method": "GET", "path": "/api/systems/report.pdf", "rx": r"^/api/systems/report\.pdf$", "feature": None, "role": None, "archive": True, "center": ["sys-pdf"]},
    {"id": "systems-vm-xlsx", "group": "owners", "label": "Danh sách VM theo hệ thống (Excel)", "format": "xlsx",
     "method": "GET", "path": "/api/systems/report.xlsx", "rx": r"^/api/systems/report\.xlsx$", "feature": None, "role": None, "archive": False, "center": ["sys-xlsx"]},
    {"id": "review-pdf", "group": "owners", "label": "Kết quả rà soát owner (PDF)", "format": "pdf",
     "method": "GET", "path": "/api/reviews/{id}/report.pdf", "rx": r"^/api/reviews/\d+/report\.pdf$", "feature": None, "role": None, "archive": False, "center": ["rv-pdf"]},
    {"id": "review-xlsx", "group": "owners", "label": "Kết quả rà soát owner (Excel)", "format": "xlsx",
     "method": "GET", "path": "/api/reviews/{id}/report.xlsx", "rx": r"^/api/reviews/\d+/report\.xlsx$", "feature": None, "role": None, "archive": False, "center": ["rv-xlsx"]},
    {"id": "bom-xlsx", "group": "bom", "label": "BOM chuẩn hoá (Excel)", "format": "xlsx",
     "method": "POST", "path": "/api/bom/export", "rx": r"^/api/bom/export$", "feature": "bom", "role": None, "archive": False, "center": ["record:bom"]},
]
_RX = [(re.compile(e["rx"]), e) for e in EXPORTS]


def find(path: str) -> Optional[Dict[str, Any]]:
    return next((e for rx, e in _RX if rx.match(path)), None)


def allowed(e: Dict[str, Any], user, features: Dict[str, bool]) -> bool:
    from .features import is_on
    from .spend.roles import has_role
    if e["feature"] and not is_on(features, e["feature"]):
        return False
    if e["role"] and e["role"].startswith("spend:"):
        return has_role(user, e["role"].split(":", 1)[1])
    return True


def public(e: Dict[str, Any]) -> Dict[str, Any]:
    return {k: e[k] for k in ("id", "group", "label", "format", "method", "path", "archive")} | {"groupLabel": GROUP_LABELS[e["group"]]}
