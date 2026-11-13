"""Host sizing API: calculate, import the Dev team's VM list, download a
template, and export the sizing result to Excel."""
import datetime as dt
import io
import unicodedata
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from .. import platforms, report_sizing, sizing, sizing_advisory
from ..database import get_db
from ..models import ReportLogo, User
from ..security import get_current_user

from ..features import require_feature
router = APIRouter(prefix="/api/sizing", tags=["sizing"], dependencies=[Depends(require_feature("sizing"))])

MAX_VMS = 5000
MAX_PROFILES = 8
MAX_UPLOAD = 5 * 1024 * 1024


class VmIn(BaseModel):
    name: str = ""
    qty: int = Field(default=1, ge=0, le=100000)
    vcpu: float = Field(default=0, ge=0, le=1024)
    ramGB: float = Field(default=0, ge=0, le=65536)
    diskGB: float = Field(default=0, ge=0)
    note: str = ""


class ProfileIn(BaseModel):
    name: str = ""
    sockets: int = Field(default=2, ge=1, le=8)
    coresPerSocket: int = Field(default=16, ge=1, le=256)
    ramGB: float = Field(default=512, ge=1, le=65536)
    diskGB: float = Field(default=0, ge=0)
    unitPrice: Optional[float] = Field(default=None, ge=0)


class AdvisoryIn(BaseModel):
    """Advisory thresholds (see sizing_advisory.AdvisoryThresholds); every field optional → engine default."""
    compute_max_gb_per_vcpu: float = Field(default=3.0, gt=0, le=64)
    memory_min_gb_per_vcpu: float = Field(default=6.0, gt=0, le=128)
    stranded_warn_pct: float = Field(default=35.0, ge=0, le=100)
    balance_ok_gap_pct: float = Field(default=15.0, ge=0, le=100)
    balance_min_load_pct: float = Field(default=40.0, ge=0, le=100)
    db_cpu_per_core_max: float = Field(default=4.0, gt=0, le=40)
    high_cpu_per_core_warn: float = Field(default=6.0, gt=0, le=80)
    ram_ratio_warn: float = Field(default=1.0, gt=0, le=4)
    low_runway_pct: float = Field(default=10.0, ge=0, le=500)
    failover_comfort_pct: float = Field(default=60.0, ge=0, le=99)
    cheaper_alt_pct: float = Field(default=3.0, ge=0, le=100)
    cluster_info_hosts: int = Field(default=32, ge=2, le=1000)
    cluster_warn_hosts: int = Field(default=64, ge=2, le=1000)
    failover_margin_pct: float = Field(default=2.0, ge=0, le=50)
    vsan_free_min_pct: float = Field(default=25.0, ge=0, le=80)
    db_keywords: str = Field(default=sizing_advisory.DEFAULT_DB_KEYWORDS, max_length=800)
    dc_keywords: str = Field(default=sizing_advisory.DEFAULT_DC_KEYWORDS, max_length=400)
    backup_keywords: str = Field(default=sizing_advisory.DEFAULT_BACKUP_KEYWORDS, max_length=400)

    @model_validator(mode="after")
    def _ordered(self):
        if self.memory_min_gb_per_vcpu < self.compute_max_gb_per_vcpu:
            raise ValueError("Ngưỡng 'thiên RAM' phải ≥ ngưỡng 'thiên CPU' (GB RAM / vCPU).")
        if self.cluster_warn_hosts < self.cluster_info_hosts:
            raise ValueError("Ngưỡng cảnh báo cluster lớn phải ≥ ngưỡng gợi ý chia cluster.")
        return self


class ParamsIn(BaseModel):
    cpu_ratio: float = Field(default=3.0, gt=0, le=20)
    ht_factor: float = Field(default=2.0, ge=1, le=4)
    ram_ratio: float = Field(default=1.0, gt=0, le=4)
    max_util_pct: float = Field(default=80, ge=10, le=100)
    growth_pct: float = Field(default=20, ge=0, le=500)
    redundancy: int = Field(default=1, ge=0, le=4)
    min_hosts: int = Field(default=2, ge=1, le=64)
    reserve_cores: float = Field(default=2, ge=0, le=32)
    reserve_ram_gb: float = Field(default=16, ge=0, le=512)
    storage_mode: str = Field(default="san", pattern="^(san|local)$")
    storage_overhead_pct: float = Field(default=25, ge=0, le=300)
    storage_protection_factor: float = Field(default=1.0, ge=1, le=4)
    environment: str = Field(default="", max_length=120)
    annual_growth_pct: float = Field(default=15, ge=0, le=300)
    horizon_years: int = Field(default=3, ge=1, le=10)
    tco_years: int = Field(default=5, ge=1, le=10)
    warranty_included_years: int = Field(default=3, ge=0, le=10)
    warranty_ext_mode: str = Field(default="amount", pattern="^(amount|pct)$")
    warranty_ext_values: List[Optional[float]] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _warranty_values(self):
        if any(v is not None and v < 0 for v in self.warranty_ext_values):
            raise ValueError("Chi phí gia hạn bảo hành không được âm.")
        if self.warranty_ext_mode == "pct" and any(v is not None and v > 100 for v in self.warranty_ext_values):
            raise ValueError("Tỷ lệ gia hạn bảo hành phải trong khoảng 0–100% giá host / năm.")
        return self
    license_price_per_core: Optional[float] = Field(default=None, ge=0)
    license_min_cores_per_socket: int = Field(default=16, ge=0, le=128)
    optimize_for: str = Field(default="hosts", pattern="^(hosts|cost)$")
    platform: str = Field(default="vmware", pattern="^(vmware|nutanix|hyperv|proxmox|generic)$")
    license_edition: str = Field(default="VMware vSphere Foundation (VVF)", max_length=120)
    suggest_variants: bool = True
    pick_profile: str = Field(default="", max_length=40, pattern=r"^(\d+x\d+x[\d.]+)?$")
    host_override: int = Field(default=0, ge=0, le=500)
    advisory: AdvisoryIn = AdvisoryIn()


class ExistingClusterIn(BaseModel):
    """Absorption check computed by the UI from current vCenter data (optional, PDF only)."""
    name: str
    hostCount: int = 0
    cpuBefore: Optional[float] = None
    cpuAfter: Optional[float] = None
    ramBefore: Optional[float] = None
    ramAfter: Optional[float] = None
    n1CpuPct: Optional[float] = None
    n1RamPct: Optional[float] = None
    n1Ok: Optional[bool] = None
    cls: str = "na"
    totalCores: Optional[float] = None
    totalRamGB: Optional[float] = None
    usedVcpu: Optional[float] = None
    usedRamGB: Optional[float] = None
    poweredOnCount: Optional[int] = None
    cpuOcAfter: Optional[float] = None
    ramOcAfter: Optional[float] = None
    extraAvgVms: Optional[int] = None


class ExtraFindingIn(BaseModel):
    severity: str = Field(default="info", pattern="^(critical|warning|info|ok)$")
    area: str = Field(default="Khác", max_length=40)
    title: str = Field(default="", max_length=200)
    detail: str = Field(default="", max_length=600)
    action: str = Field(default="", max_length=400)


class ReportOptionsIn(BaseModel):
    """Adjustments made after the calculation, applied to the full PDF report only."""
    sections: Dict[str, bool] = Field(default_factory=dict)       # e.g. {"bom": false, "glossary": false}
    headline: str = Field(default="", max_length=400)            # replaces the automatic conclusion sentence
    note: str = Field(default="", max_length=2000)               # "Ghi chú của người lập" in the summary
    selectionNote: str = Field(default="", max_length=400)       # why the option / host count was changed
    hiddenFindings: List[str] = Field(default_factory=list, max_length=60)
    extraFindings: List[ExtraFindingIn] = Field(default_factory=list, max_length=20)


class SizingPayload(BaseModel):
    vms: List[VmIn]
    params: ParamsIn = ParamsIn()
    profiles: List[ProfileIn]
    existing: Optional[List[ExistingClusterIn]] = None
    policyText: str = Field(default="", max_length=120)
    currency: str = Field(default="VND", max_length=8)
    preparedBy: str = Field(default="", max_length=80)
    # White-label fields for reports delivered to customers / partners
    customerName: str = Field(default="", max_length=120)
    preparedOrg: str = Field(default="", max_length=120)
    documentCode: str = Field(default="", max_length=60)
    # Document control & scope (full report)
    customerContact: str = Field(default="", max_length=120)
    version: str = Field(default="1.0", max_length=20)
    versionNote: str = Field(default="", max_length=200)
    approvedBy: str = Field(default="", max_length=80)
    inputSource: str = Field(default="", max_length=200)
    inputDate: str = Field(default="", max_length=40)
    outOfScope: Optional[List[str]] = Field(default=None, max_length=20)
    # 'full' = báo cáo tư vấn đầy đủ (default) · 'brief' = báo cáo tư vấn rút gọn (same template, compact content)
    detail: str = Field(default="full", pattern="^(full|brief)$")
    # Optional per-report logo (data URL, same formats/limit as Settings → logo); empty = default report logo
    logoDataUrl: Optional[str] = Field(default=None, max_length=3_000_000)
    reportOptions: Optional[ReportOptionsIn] = None
    # PDF content: 'new' = buy new servers (default) · 'reuse' = place the project on an existing cluster
    mode: str = Field(default="new", pattern="^(new|reuse)$")
    targetCluster: Optional[str] = Field(default=None, max_length=120)
    includeNewOption: bool = True      # reuse mode: append the buy-new comparison as option B
    includeExisting: bool = True       # new mode: append the existing-cluster absorption check


def _inputs(payload: SizingPayload):
    if len(payload.vms) > MAX_VMS:
        raise HTTPException(status_code=413, detail=f"Quá nhiều dòng VM ({len(payload.vms)}); giới hạn {MAX_VMS}.")
    if not payload.profiles:
        raise HTTPException(status_code=422, detail="Cần ít nhất một cấu hình host ứng viên.")
    if len(payload.profiles) > MAX_PROFILES:
        raise HTTPException(status_code=413, detail=f"Tối đa {MAX_PROFILES} cấu hình host ứng viên.")
    vms = [sizing.VmSpec(name=v.name or f"VM-{i+1:02d}", qty=v.qty, vcpu=v.vcpu, ram_gb=v.ramGB, disk_gb=v.diskGB, note=v.note)
           for i, v in enumerate(payload.vms)]
    profiles = [sizing.HostProfile(
        name=p.name or f"{p.sockets}×{p.coresPerSocket}C / {int(p.ramGB)} GB",
        sockets=p.sockets, cores_per_socket=p.coresPerSocket, ram_gb=p.ramGB, disk_gb=p.diskGB, unit_price=p.unitPrice,
    ) for p in payload.profiles]
    params = sizing.SizingParams(**payload.params.model_dump())
    return vms, params, profiles


def _build(payload: SizingPayload) -> Dict[str, Any]:
    vms, params, profiles = _inputs(payload)
    return sizing.build_sizing(vms, params, profiles)


@router.post("/pdf")
def sizing_pdf(payload: SizingPayload, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    vms, params, profiles = _inputs(payload)
    if not vms or not any(v.qty > 0 for v in vms):
        raise HTTPException(status_code=422, detail="Danh sách VM trống — không có gì để lập đề xuất.")
    logo_data_url = _override_logo(payload.logoDataUrl)
    if logo_data_url is None:
        logo_row = db.query(ReportLogo).filter(ReportLogo.id == 1).first()
        logo_data_url = f"data:{logo_row.mime_type};base64,{logo_row.data_base64}" if logo_row else None
    existing = [e.model_dump() for e in (payload.existing or [])][:20]
    try:
        pdf_bytes = report_sizing.build_sizing_pdf(vms, params, profiles, existing=existing, policy_txt=payload.policyText,
                                                   currency=payload.currency, logo_data_url=logo_data_url, prepared_by=payload.preparedBy,
                                                   mode=payload.mode, target_cluster=payload.targetCluster,
                                                   include_new=payload.includeNewOption, include_existing=payload.includeExisting,
                                                   detail=payload.detail, customer_name=payload.customerName,
                                                   prepared_org=payload.preparedOrg, document_code=payload.documentCode,
                                                   version=payload.version, version_note=payload.versionNote, approved_by=payload.approvedBy,
                                                   customer_contact=payload.customerContact, input_source=payload.inputSource,
                                                   input_date=payload.inputDate, out_of_scope=[x[:200] for x in (payload.outOfScope or [])],
                                                   report_options=payload.reportOptions.model_dump() if payload.reportOptions else None)
    except Exception as exc:  # pragma: no cover - surfaced to the UI
        raise HTTPException(status_code=500, detail=f"Không thể tạo PDF: {exc}")
    label = _ascii_slug(params.environment, fallback="")
    kind = ("tan_dung_hien_huu" if payload.mode == "reuse" else "trang_bi_moi") + ("_rut_gon" if payload.detail == "brief" else "")
    filename = f"ICO_De_xuat_cau_hinh_may_chu_{(label + '_') if label else ''}{kind}_{dt.datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
    return StreamingResponse(iter([pdf_bytes]), media_type="application/pdf", headers=_attachment_headers(filename))


def _override_logo(data_url: Optional[str]) -> Optional[str]:
    """Validate a per-report logo override; None when not provided."""
    if not data_url or not data_url.strip():
        return None
    import base64
    from .settings import DATA_URL_RE, MAX_LOGO_BYTES
    m = DATA_URL_RE.match(data_url.strip())
    if not m:
        raise HTTPException(status_code=422, detail="Logo không hợp lệ. Chỉ chấp nhận PNG, JPEG, SVG hoặc WEBP.")
    try:
        raw = base64.b64decode(m.group(2), validate=True)
    except Exception:
        raise HTTPException(status_code=422, detail="Không giải mã được dữ liệu logo.")
    if len(raw) > MAX_LOGO_BYTES:
        raise HTTPException(status_code=413, detail=f"Logo vượt quá {MAX_LOGO_BYTES // (1024 * 1024)}MB.")
    return f"data:{m.group(1).lower()};base64,{m.group(2)}"


@router.get("/defaults")
def sizing_defaults(user: User = Depends(get_current_user)):
    params = sizing.SizingParams()
    return {"params": {**params.__dict__, "advisory": params.advisory.__dict__.copy()}, "profiles": sizing.DEFAULT_PROFILES,
            "sampleVms": sizing.TEMPLATE_ROWS,
            "advisoryMeta": [{"key": k, "label": lbl, "unit": u} for k, lbl, u in sizing_advisory.THRESHOLD_META],
            "platforms": platforms.meta()}


@router.post("/calc")
def sizing_calc(payload: SizingPayload, user: User = Depends(get_current_user)):
    return _build(payload)


@router.post("/import")
async def sizing_import(file: UploadFile = File(...), user: User = Depends(get_current_user)):
    content = await file.read()
    if len(content) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="File vượt quá 5 MB.")
    try:
        parsed = sizing.parse_vm_file(file.filename or "", content)
    except Exception as exc:  # noqa: BLE001 — surface a readable message to the UI
        raise HTTPException(status_code=400, detail=f"Không đọc được file: {exc}")
    return parsed


def _ascii_slug(text: Optional[str], fallback: str = "may_chu", limit: int = 40) -> str:
    """ASCII-only file-name fragment. HTTP headers are latin-1, so Vietnamese
    project names ("Dự án Đầu tư / CRM 2026") must be transliterated, not just
    filtered with str.isalnum() (which accepts Unicode letters)."""
    text = (text or "").replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in text.strip()).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug[:limit].strip("_") or fallback


def _attachment_headers(filename: str) -> Dict[str, str]:
    safe = filename.encode("ascii", "ignore").decode("ascii").replace('"', "")
    return {"Content-Disposition": f'attachment; filename="{safe}"'}


def _xlsx_response(wb, base: str) -> StreamingResponse:
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    filename = f"{base}_{dt.datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return StreamingResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_attachment_headers(filename),
    )


def _style_header(ws, row: int = 1):
    from openpyxl.styles import Alignment, Font, PatternFill
    for cell in ws[row]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="074B9B")
        cell.alignment = Alignment(vertical="center", wrap_text=True)


def _autowidth(ws, min_w: int = 8, max_w: int = 60):
    from openpyxl.utils import get_column_letter
    for idx in range(1, ws.max_column + 1):
        longest = 0
        for r in range(1, ws.max_row + 1):
            v = ws.cell(row=r, column=idx).value
            longest = max(longest, len(str(v)) if v is not None else 0)
        ws.column_dimensions[get_column_letter(idx)].width = min(max_w, max(min_w, longest + 2))


@router.get("/template")
def sizing_template(user: User = Depends(get_current_user)):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "VM list"
    ws.append(["Tên VM", "Số lượng", "vCPU", "RAM (GB)", "Disk (GB)", "Ghi chú"])
    _style_header(ws)
    for r in sizing.TEMPLATE_ROWS:
        ws.append([r["name"], r["qty"], r["vcpu"], r["ramGB"], r["diskGB"], r["note"]])
    ws.freeze_panes = "A2"
    _autowidth(ws)
    guide = wb.create_sheet("Hướng dẫn")
    for line in [
        "Điền mỗi dòng một loại VM; cột 'Số lượng' cho phép khai nhiều VM cùng cấu hình.",
        "Đơn vị RAM và Disk là GB (có thể ghi '2 TB', '512 GB' — hệ thống tự quy đổi).",
        "Tên cột có thể viết tiếng Việt hoặc tiếng Anh (VM name / Qty / vCPU / RAM / Disk / Note).",
        "Lưu file .xlsx hoặc .csv rồi dùng nút 'Import Excel/CSV' trong mục Sizing máy chủ.",
    ]:
        guide.append([line])
    guide.column_dimensions["A"].width = 110
    return _xlsx_response(wb, "ICO_Sizing_mau_danh_sach_VM")


@router.post("/export")
def sizing_export(payload: SizingPayload, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Dashboard-style workbook (see excel_sizing.py): Dashboard with KPI tiles and native charts,
    then detail sheets as formatted Excel tables."""
    from .. import excel_sizing

    result = _build(payload)
    logo = _override_logo(payload.logoDataUrl)
    if logo is None:
        row = db.query(ReportLogo).filter(ReportLogo.id == 1).first()
        logo = f"data:{row.mime_type};base64,{row.data_base64}" if row else None
    meta = {"customerName": payload.customerName, "preparedOrg": payload.preparedOrg, "preparedBy": payload.preparedBy,
            "documentCode": payload.documentCode, "logoDataUrl": logo}
    vms = [v.model_dump() for v in payload.vms]
    wb = excel_sizing.build_workbook(result, vms, meta)
    params = result["params"]
    base = "ICO_Sizing" + (("_" + _ascii_slug(params.get("environment"), fallback="")) if (params.get("environment") or "").strip() else "")
    return _xlsx_response(wb, base)
