"""Registry of every file export of ICO (standard Export menu, super app §3).

One row per export: what it is, where it lives (path / regex), its format, the module switch and
the role needed to run it, and whether a copy can go to Kho báo cáo. The report archive and the
GET /api/exports list both come from here — a new export is declared once."""
import re
from typing import Any, Dict, List, Optional

GROUP_LABELS = {"capacity": "Capacity", "backup": "Backup", "license": "License", "sizing": "Sizing máy chủ",
                "spend": "Ngân sách & chi phí", "renovation": "Cải tạo văn phòng", "bom": "Chuẩn hoá BOM", "inventory": "Tài sản",
                "owners": "Hệ thống & Owner"}

# role: None = any signed-in user · "spend:viewer" = spend role at least viewer
EXPORTS: List[Dict[str, Any]] = [
    {"id": "capacity-pdf", "group": "capacity", "label": "Báo cáo Capacity (Vận hành / Kỹ thuật / Quy hoạch)", "format": "pdf",
     "method": "GET", "path": "/api/reports/pdf", "rx": r"^/api/reports/pdf$", "feature": None, "role": None, "archive": True},
    {"id": "inventory-csv", "group": "inventory", "label": "Host / VM / phát hiện (CSV)", "format": "csv",
     "method": "GET", "path": "/api/export/csv", "rx": r"^/api/export/csv$", "feature": None, "role": None, "archive": False},
    {"id": "inventory-xlsx", "group": "inventory", "label": "Bảng đang xem (Excel)", "format": "xlsx",
     "method": "POST", "path": "/api/export/xlsx", "rx": r"^/api/export/xlsx$", "feature": None, "role": None, "archive": False},
    {"id": "backup-pdf", "group": "backup", "label": "Báo cáo Backup (PDF)", "format": "pdf",
     "method": "GET", "path": "/api/backup/report/pdf", "rx": r"^/api/backup/report/pdf$", "feature": "backup", "role": None, "archive": True},
    {"id": "backup-xlsx", "group": "backup", "label": "Dữ liệu Backup (Excel)", "format": "xlsx",
     "method": "GET", "path": "/api/backup/report/xlsx", "rx": r"^/api/backup/report/xlsx$", "feature": "backup", "role": None, "archive": False},
    {"id": "license-pdf", "group": "license", "label": "Tóm tắt License (PDF)", "format": "pdf",
     "method": "GET", "path": "/api/licensing/report/pdf", "rx": r"^/api/licensing/report/pdf$", "feature": "license", "role": None, "archive": True},
    {"id": "sizing-pdf", "group": "sizing", "label": "Đề xuất cấu hình máy chủ (PDF)", "format": "pdf",
     "method": "POST", "path": "/api/sizing/pdf", "rx": r"^/api/sizing/pdf$", "feature": "sizing", "role": None, "archive": True},
    {"id": "sizing-xlsx", "group": "sizing", "label": "Sizing (Excel)", "format": "xlsx",
     "method": "POST", "path": "/api/sizing/export", "rx": r"^/api/sizing/export$", "feature": "sizing", "role": None, "archive": False},
    {"id": "budget-pdf", "group": "spend", "label": "Kế hoạch ngân sách IT (PDF)", "format": "pdf",
     "method": "POST", "path": "/api/spend/reports/budget.pdf", "rx": r"^/api/spend/reports/budget\.pdf$", "feature": "spend", "role": "spend:viewer", "archive": True},
    {"id": "budget-xlsx", "group": "spend", "label": "Kế hoạch ngân sách IT (Excel)", "format": "xlsx",
     "method": "POST", "path": "/api/spend/reports/budget.xlsx", "rx": r"^/api/spend/reports/budget\.xlsx$", "feature": "spend", "role": "spend:viewer", "archive": False},
    {"id": "budget-plan-xlsx", "group": "spend", "label": "Kế hoạch ngân sách năm sau (Excel)", "format": "xlsx",
     "method": "GET", "path": "/api/spend/plans/{id}/export.xlsx", "rx": r"^/api/spend/plans/\d+/export\.xlsx$",
     "feature": "spend", "role": "spend:viewer", "archive": False},
    {"id": "renovation-pdf", "group": "renovation", "label": "Cải tạo văn phòng (PDF)", "format": "pdf",
     "method": "GET", "path": "/api/spend/renovations/{code}/export.pdf", "rx": r"^/api/spend/renovations/[^/]+/export\.pdf$",
     "feature": "spend", "role": "spend:viewer", "archive": True},
    {"id": "renovation-xlsx", "group": "renovation", "label": "Cải tạo văn phòng (Excel)", "format": "xlsx",
     "method": "GET", "path": "/api/spend/renovations/{code}/export.xlsx", "rx": r"^/api/spend/renovations/[^/]+/export\.xlsx$",
     "feature": "spend", "role": "spend:viewer", "archive": False},
    {"id": "systems-xlsx", "group": "owners", "label": "Hệ thống & Owner (Excel, nhập lại được)", "format": "xlsx",
     "method": "GET", "path": "/api/systems/export.xlsx", "rx": r"^/api/systems/export\.xlsx$", "feature": None, "role": None, "archive": False},
    {"id": "bom-xlsx", "group": "bom", "label": "BOM chuẩn hoá (Excel)", "format": "xlsx",
     "method": "POST", "path": "/api/bom/export", "rx": r"^/api/bom/export$", "feature": "bom", "role": None, "archive": False},
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
