import logging
import datetime as dt
from typing import Optional

from fastapi.encoders import jsonable_encoder
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import capacity, data_access, history, planning, report_pdf, report_planning
from ..config import get_settings
from ..database import get_db
from ..models import ReportLogo, User
from ..features import ensure_feature, require_feature
from ..security import get_current_user

from ..scope import scope_keys as _scope_keys, scope_label as _scope_label, split_csv as _split_csv

logger = logging.getLogger("vco.routers.reports")
router = APIRouter(prefix="/api/reports", tags=["reports"])








@router.get("/pdf")
def export_pdf(
    view: str = Query(default="executive", pattern="^(executive|technical|planning)$"),
    vcenter: Optional[str] = Query(default="all"),
    types: Optional[str] = Query(default=None, description="Comma-separated finding types to include (omit = all types)"),
    clusters: Optional[str] = Query(default=None, description="Comma-separated cluster names to include (omit = every cluster)"),
    hosts: Optional[str] = Query(default=None, description="Comma-separated host names to include (omit = every host in scope)"),
    storage: Optional[str] = Query(default=None, description="'1'/'true' to include Storage criteria, '0'/'false' to exclude (omit = use saved setting)"),
    months: int = Query(default=12, description="Snapshot-history window for the trend section / VM appendix: 3, 6 or 12 months"),
    horizon: int = Query(default=36, ge=6, le=60, description="[planning] projection horizon in months"),
    growth: Optional[str] = Query(default=None, description="[planning] comma-separated quarterly growth %% scenarios, e.g. 5,10,20"),
    node_cores: Optional[int] = Query(default=None, ge=1, description="[planning] physical cores of a new node (omit = largest host per cluster)"),
    node_ram: Optional[int] = Query(default=None, ge=1, description="[planning] RAM GB of a new node (omit = largest host per cluster)"),
    lead: int = Query(default=3, ge=0, le=12, description="[planning] procurement + deployment lead time in months"),
    target: float = Query(default=80.0, ge=50, le=100, description="[planning] planning target as %% of the overcommit threshold"),
    ai: bool = Query(default=False, description="[executive|technical] add a 'Tổng kết (AI soạn)' box (AI task report_summary)"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if view == "planning":
        ensure_feature(db, "planning")
    settings = get_settings()
    policy = data_access.get_effective_policy(db, settings)
    if storage is not None:
        # Explicit per-export override from the UI toggle; otherwise the
        # persisted app setting (include_storage) decides.
        policy.include_storage = capacity.parse_bool(storage, default=policy.include_storage)
    host_rows, vm_rows, run_meta = data_access.get_latest_combined(db, _scope_keys(vcenter))

    if not host_rows and not vm_rows:
        raise HTTPException(status_code=404, detail="Chưa có dữ liệu để tạo báo cáo. Hãy đồng bộ hoặc import dữ liệu trước.")

    def _display_list(items: list, limit: int = 5) -> str:
        shown = ", ".join(items[:limit])
        return shown if len(items) <= limit else f"{shown} và {len(items) - limit} khác"

    cluster_list = _split_csv(clusters)
    host_list = _split_csv(hosts)
    scope_parts = [_scope_label(vcenter)]
    if cluster_list is not None:
        host_rows = [h for h in host_rows if h.get("cluster") in cluster_list]
        vm_rows = [v for v in vm_rows if v.get("cluster") in cluster_list]
        scope_parts.append(f"Cluster: {_display_list(cluster_list)}" if cluster_list else "Cluster: (không chọn cluster nào)")
    if host_list is not None:
        host_rows = [h for h in host_rows if h.get("host") in host_list]
        vm_rows = [v for v in vm_rows if v.get("host") in host_list]
        scope_parts.append(f"Host: {_display_list(host_list)}" if host_list else "Host: (không chọn host nào)")

    if (cluster_list is not None or host_list is not None) and not host_rows and not vm_rows:
        raise HTTPException(status_code=404, detail="Không có dữ liệu phù hợp với phạm vi cluster/host đã chọn.")

    scope_label = " · ".join(scope_parts)

    include_types = None
    if types:
        requested = {t.strip() for t in types.split(",") if t.strip()}
        include_types = [t for t in report_pdf.ALL_FINDING_TYPES if t in requested]

    logo_row = db.query(ReportLogo).filter(ReportLogo.id == 1).first()
    logo_data_url = f"data:{logo_row.mime_type};base64,{logo_row.data_base64}" if logo_row else None

    # Snapshot history is always per cluster (one cluster = one project team)
    # and follows the same cluster scope as the rest of the report. Host
    # narrowing applies to the VM added/removed appendix; the CPU/RAM series
    # stay at cluster level (that is the unit snapshots are aggregated at).
    months = history.normalize_months(months)
    scope_keys = _scope_keys(vcenter)
    try:
        history_data = history.get_resource_history(db, scope_keys, months, "cluster", policy, clusters=cluster_list)
        vm_changes = history.get_vm_changes(db, scope_keys, months, clusters=cluster_list, hosts=host_list)
    except Exception:  # pragma: no cover - history must never block the report
        history_data, vm_changes = None, None

    try:
        if view == "planning":
            growth_list = [float(g) for g in _split_csv(growth) or []] or None
            params = planning.PlanningParams(horizon_months=horizon, growth=growth_list, node_cores=node_cores,
                                             node_ram_gb=node_ram, lead_months=lead, target_pct=target)
            pdf_bytes = report_planning.build_planning_pdf(host_rows, vm_rows, policy, scope_label, params, logo_data_url,
                                                           history=history_data)
        else:
            if ai:
                from .ai_admin import pdf_ai_box
                from ..ai_tasks import brief_facts
                pdf_ai_box(db, user, view, lambda: {"pham_vi": scope_label, **brief_facts(
                    report_pdf.build_management_brief(host_rows, vm_rows, policy))})
            pdf_bytes = report_pdf.build_report_pdf(view, host_rows, vm_rows, policy, scope_label, run_meta, include_types, logo_data_url,
                                                    history=history_data, vm_changes=vm_changes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Tham số quy hoạch không hợp lệ: {exc}")
    except Exception as exc:  # noqa: BLE001 — details go to the log, not to the client
        logger.exception("PDF report render failed (view=%s)", view)
        raise HTTPException(status_code=500, detail="Không thể tạo PDF. Vui lòng thử lại hoặc báo quản trị viên kiểm tra log.") from exc

    label = {"executive": "van_hanh", "technical": "ky_thuat", "planning": "quy_hoach"}[view]
    filename = f"ico_report_{label}_{dt.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf"
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/brief")
def management_brief(
    vcenter: Optional[str] = Query(default="all"),
    storage: Optional[str] = Query(default=None, description="'1'/'true' to include Storage criteria, '0'/'false' to exclude (omit = use saved setting)"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Management-friendly brief (headline, key points, recommendations,
    decision register) for the web Executive View -- same logic as the
    Executive PDF."""
    settings = get_settings()
    policy = data_access.get_effective_policy(db, settings)
    if storage is not None:
        policy.include_storage = capacity.parse_bool(storage, default=policy.include_storage)
    host_rows, vm_rows, _run_meta = data_access.get_latest_combined(db, _scope_keys(vcenter))
    if not host_rows and not vm_rows:
        return {"available": False, "scope": _scope_label(vcenter)}
    brief = report_pdf.build_management_brief(host_rows, vm_rows, policy)
    brief["available"] = True
    brief["scope"] = _scope_label(vcenter)
    return brief


def _finite_or_none(v):
    import math
    return v if isinstance(v, (int, float)) and math.isfinite(v) else None


@router.get("/planning", dependencies=[Depends(require_feature("planning"))])
def planning_result(
    vcenter: Optional[str] = Query(default="all"),
    horizon: int = Query(default=36, ge=6, le=60),
    growth: Optional[str] = Query(default=None),
    node_cores: Optional[int] = Query(default=None, ge=1),
    node_ram: Optional[int] = Query(default=None, ge=1),
    lead: int = Query(default=3, ge=0, le=12),
    target: float = Query(default=80.0, ge=50, le=100),
    months: int = Query(default=12),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Quy hoạch tài nguyên on screen (same engine as the planning PDF): totals per scenario,
    per-cluster need under the base scenario, and the decision list."""
    policy = data_access.get_effective_policy(db, get_settings())
    host_rows, vm_rows, _ = data_access.get_latest_combined(db, _scope_keys(vcenter))
    if not host_rows:
        raise HTTPException(status_code=404, detail="Chưa có dữ liệu host để quy hoạch. Hãy đồng bộ hoặc import dữ liệu trước.")
    import math
    try:
        growth_list = [float(g) for g in (_split_csv(growth) or [])[:6]] or None
        if growth_list and any(not math.isfinite(g) or not -50 <= g <= 500 for g in growth_list):
            raise ValueError("tăng trưởng mỗi quý phải từ -50% đến 500%")
        params = planning.PlanningParams(horizon_months=horizon, growth=growth_list, node_cores=node_cores,
                                         node_ram_gb=node_ram, lead_months=lead, target_pct=target)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Tham số quy hoạch không hợp lệ: {exc}") from exc
    try:
        hist = history.get_resource_history(db, _scope_keys(vcenter), history.normalize_months(months), "cluster", policy)
    except Exception:  # noqa: BLE001 — history is optional (trend scenario only)
        hist = None
    plan = planning.build_plan(host_rows, vm_rows, policy, params, history=hist)
    recs = planning.plan_recommendations(plan)
    base = plan["baseIndex"]
    return jsonable_encoder({
        "scope": _scope_label(vcenter), "params": plan["params"], "scenarios": plan["scenarios"], "baseIndex": base,
        "totals": [{k: t[k] for k in ("scenario", "finalNodes", "addCores", "addRamGB", "firstNeedLabel", "orderByLabel")} for t in plan["totals"]],
        "clusters": [{"name": c["name"], "nodeSpec": c["nodeSpec"], "hosts": c["model"]["hostCount"],
                      "cpuPct": _finite_or_none(c["model"]["cpuPct"]), "ramPct": _finite_or_none(c["model"]["ramPct"]),
                      **{k: c["scenarios"][base].get(k) for k in ("firstNeedLabel", "orderByLabel", "ramUpgrade")},
                      "nodes": c["scenarios"][base]["final"]["nodes"], "binding": c["scenarios"][base]["final"].get("binding")}
                     for c in plan["clusters"]],
        "recommendations": recs,
    })
