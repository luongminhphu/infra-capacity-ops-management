"""Hệ thống & Owner — Excel import / export (same template both ways).

Sheet "Hệ thống": Mã · Tên hệ thống · Dự án · Phòng ban · Owner (email) · Tên owner · Người thay thế (email) ·
                  Quản lý trực tiếp (email) · Mức độ quan trọng · Môi trường · Ghi chú
Sheet "VM":       VM · vCenter · Hệ thống (mã hoặc tên)

Reading goes through shared/xlsx_io (zip-bomb guard, read-only, bounded). Preview = parse + plan (no write);
commit = parse + plan + apply in one transaction. Rows with an error are reported and skipped."""
import datetime as dt
import io
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..models import User
from ..shared.text import norm_text
from ..shared.xlsx_io import XlsxError, read_rows
from . import service
from .models import System, SystemMember

SYS_SHEET, VM_SHEET = "Hệ thống", "VM"
SYS_HEAD = ["Mã", "Tên hệ thống", "Dự án", "Phòng ban", "Owner (email)", "Tên owner", "Backup (email)", "Tên backup",
            "Quản lý trực tiếp (email)", "Support (email)", "Tên support", "Liên hệ support", "Mức độ quan trọng", "Môi trường",
            "Ghi chú"]
VM_HEAD = ["VM", "vCenter", "Hệ thống (mã hoặc tên)"]
SYS_COLS = {"ma": "code", "ma he thong": "code", "ten he thong": "name", "ten": "name", "he thong": "name", "du an": "project",
            "phong ban": "department", "owner email": "ownerEmail", "owner": "ownerEmail", "email owner": "ownerEmail",
            "ten owner": "ownerName", "nguoi thay the email": "deputyEmail", "nguoi thay the": "deputyEmail", "deputy": "deputyEmail",
            "backup email": "deputyEmail", "backup": "deputyEmail", "ten backup": "deputyName", "ten nguoi thay the": "deputyName",
            "support email": "supportEmails", "support": "supportEmails", "email support": "supportEmails",
            "ten support": "supportName", "nhom support": "supportName", "lien he support": "supportContact",
            "quan ly truc tiep email": "managerEmail", "quan ly truc tiep": "managerEmail", "manager": "managerEmail",
            "muc do quan trong": "criticality", "criticality": "criticality", "moi truong": "env", "env": "env", "ghi chu": "note"}
VM_COLS = {"vm": "vm", "ten vm": "vm", "vcenter": "vcenter", "he thong ma hoac ten": "system", "he thong": "system",
           "ma he thong": "system", "ma hoac ten he thong": "system"}
MAX_ROWS = 5000


class ImportError_(ValueError):
    pass


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return " ".join(str(v).split())


def _table(rows: List[List[Any]], cols: Dict[str, str], must: str) -> Tuple[int, Dict[int, str]]:
    """Header row (first of the first 10 rows that names the `must` field) → (row index, column → field)."""
    for i, r in enumerate(rows[:10]):
        m = {j: cols[norm_text(_cell(v))] for j, v in enumerate(r) if norm_text(_cell(v)) in cols}
        if must in m.values():
            return i, m
    raise ImportError_(f"Không thấy dòng tiêu đề (cần cột {must}).")


def parse(data: bytes) -> Dict[str, Any]:
    try:
        sheets = read_rows(data, max_sheets=10, max_rows=MAX_ROWS + 20, max_cols=20)
    except XlsxError as exc:
        raise ImportError_(str(exc)) from None
    by = {norm_text(name): rows for name, rows in sheets}
    sys_rows, vm_rows = by.get(norm_text(SYS_SHEET)), by.get(norm_text(VM_SHEET))
    if sys_rows is None and vm_rows is None:
        raise ImportError_(f"Cần sheet “{SYS_SHEET}” và / hoặc “{VM_SHEET}”.")
    out: Dict[str, Any] = {"systems": [], "vms": []}
    if sys_rows is not None:
        h, m = _table(sys_rows, SYS_COLS, "name")
        for i, r in enumerate(sys_rows[h + 1:], h + 2):
            rec = {f: _cell(r[j]) if j < len(r) else "" for j, f in m.items()}
            if any(rec.values()):
                out["systems"].append({"row": i, **rec})
    if vm_rows is not None:
        h, m = _table(vm_rows, VM_COLS, "vm")
        for i, r in enumerate(vm_rows[h + 1:], h + 2):
            rec = {f: _cell(r[j]) if j < len(r) else "" for j, f in m.items()}
            if any(rec.values()):
                out["vms"].append({"row": i, "vm": rec.get("vm", ""), "vcenter": rec.get("vcenter", ""), "system": rec.get("system", "")})
    if len(out["systems"]) > MAX_ROWS or len(out["vms"]) > MAX_ROWS:
        raise ImportError_(f"Tối đa {MAX_ROWS} dòng mỗi sheet.")
    return out


def plan(db: Session, parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Validate every row against the database; nothing is written."""
    by_code = {s.code.upper(): s for s in db.query(System)}
    by_name = {s.name_norm: s for s in by_code.values()}
    systems, errors = [], []
    file_names: Dict[str, int] = {}
    for rec in parsed["systems"]:
        data = {k: v for k, v in rec.items() if k not in ("row", "code")}
        try:
            vals = service.clean(data)
        except service.Invalid as exc:
            errors.append({"sheet": SYS_SHEET, "row": rec["row"], "ref": rec.get("name") or rec.get("code"), "error": str(exc)})
            continue
        code, nn = rec.get("code", "").upper(), norm_text(vals["name"])
        cur = by_code.get(code) if code else by_name.get(nn)
        if code and cur is None:
            errors.append({"sheet": SYS_SHEET, "row": rec["row"], "ref": code, "error": f"Không có hệ thống mã {code} (để trống mã để tạo mới)."})
            continue
        other = by_name.get(nn)
        if other is not None and cur is not None and other.id != cur.id or nn in file_names:
            errors.append({"sheet": SYS_SHEET, "row": rec["row"], "ref": vals["name"], "error": f"Tên “{vals['name']}” trùng hệ thống khác."})
            continue
        file_names[nn] = rec["row"]
        changes = {c: v for c, v in vals.items() if cur is None or getattr(cur, c) != v}
        systems.append({"row": rec["row"], "id": cur.id if cur else None, "code": cur.code if cur else None, "name": vals["name"],
                        "values": vals, "action": "new" if cur is None else ("update" if changes else "same"),
                        "ownerEmail": vals.get("owner_email"), "changes": sorted(changes) if cur else []})
    ok_names = {norm_text(s["name"]): s for s in systems}
    ok_codes = {s["code"]: s for s in systems if s["code"]}
    bad_refs = {norm_text(e["ref"] or "") for e in errors} | {(e["ref"] or "").upper() for e in errors}
    found = service.find_vms(db, [(r["vm"].lower(), r["vcenter"]) for r in parsed["vms"]])
    cur_members = {m.ci_id: m for m in db.query(SystemMember)}
    vms, seen = [], {}
    for r in parsed["vms"]:
        ref, err = r["system"], None
        target = ok_codes.get(ref.upper()) or ok_names.get(norm_text(ref)) or by_code.get(ref.upper()) or by_name.get(norm_text(ref))
        cands = found.get((r["vm"].lower(), r["vcenter"]), [])
        if not r["vm"]:
            err = "Thiếu tên VM."
        elif not ref:
            err = "Thiếu mã hoặc tên hệ thống."
        elif target is None:
            err = (f"Hệ thống “{ref}” ở sheet {SYS_SHEET} có lỗi." if norm_text(ref) in bad_refs or ref.upper() in bad_refs
                   else f"Không có hệ thống “{ref}”.")
        elif not cands:
            err = f"Không có VM “{r['vm']}”{' trên ' + r['vcenter'] if r['vcenter'] else ''} trong dữ liệu mới nhất."
        elif len(cands) > 1:
            err = f"VM “{r['vm']}” có ở nhiều vCenter — điền cột vCenter."
        if err:
            errors.append({"sheet": VM_SHEET, "row": r["row"], "ref": r["vm"] or ref, "error": err})
            continue
        ci = cands[0]
        if ci.id in seen:
            errors.append({"sheet": VM_SHEET, "row": r["row"], "ref": r["vm"], "error": f"VM lặp lại (dòng {seen[ci.id]})."})
            continue
        seen[ci.id] = r["row"]
        tid = target["id"] if isinstance(target, dict) else target.id
        tname = target["name"] if isinstance(target, dict) else target.name
        m = cur_members.get(ci.id)
        action = "new" if m is None else ("same" if m.system_id == tid and tid is not None and m.source == "manual" else "move")
        vms.append({"row": r["row"], "ciId": ci.id, "vm": ci.name, "system": tname, "systemRef": ref, "systemId": tid,
                    "action": action, "fromSystemId": m.system_id if m is not None else None})
    return {"systems": systems, "vms": vms, "errors": errors}


def public_plan(p: Dict[str, Any]) -> Dict[str, Any]:
    cnt = lambda rows, a: sum(1 for r in rows if r["action"] == a)  # noqa: E731
    return {"counts": {"systemsNew": cnt(p["systems"], "new"), "systemsUpdate": cnt(p["systems"], "update"),
                       "systemsSame": cnt(p["systems"], "same"), "vmsNew": cnt(p["vms"], "new"), "vmsMove": cnt(p["vms"], "move"),
                       "vmsSame": cnt(p["vms"], "same"), "errors": len(p["errors"]),
                       "rows": sum(1 for r in p["systems"] + p["vms"] if r["action"] != "same")},
            "systems": [{k: r[k] for k in ("row", "code", "name", "action", "ownerEmail", "changes")} for r in p["systems"]][:500],
            "vms": [{k: r[k] for k in ("row", "vm", "system", "action")} for r in p["vms"]][:500],
            "errors": p["errors"][:500]}


def apply(db: Session, user: User, p: Dict[str, Any], ip: Optional[str] = None) -> Dict[str, int]:
    """Write the plan (caller commits). New systems get their SYS code; VM rows become manual assignments."""
    ids: Dict[str, int] = {}
    created = updated = 0
    for r in p["systems"]:
        if r["action"] == "new":
            s = service.create(db, user, _camel(r["values"]), ip)
            created += 1
        else:
            s = db.get(System, r["id"])
            if r["action"] == "update":
                service.update_system(db, user, s, _camel(r["values"]), ip)
                updated += 1
        ids[norm_text(r["name"])] = s.id
    db.flush()
    by_system: Dict[int, List[int]] = {}
    for v in p["vms"]:
        if v["action"] == "same":
            continue
        sid = v["systemId"] or ids.get(norm_text(v["system"]))
        by_system.setdefault(sid, []).append(v["ciId"])
    added = moved = 0
    for sid, cis in by_system.items():
        res = service.assign(db, user, db.get(System, sid), cis, move=True, ip=ip, audit_action="owners.member.import")
        added, moved = added + res["added"], moved + res["moved"]
    service.audit(db, user, "owners.import", ip, systemsNew=created, systemsUpdated=updated, vmsAdded=added, vmsMoved=moved,
                  errors=len(p["errors"]))
    return {"systemsNew": created, "systemsUpdated": updated, "vmsAdded": added, "vmsMoved": moved, "errors": len(p["errors"])}


def _camel(vals: Dict[str, Any]) -> Dict[str, Any]:
    inv = {v: k for k, v in service.FIELDS.items()}
    return {inv[c]: v for c, v in vals.items() if c in inv}


# ---------------------------------------------------------------- export (same template)
def export(db: Session) -> bytes:
    from openpyxl import Workbook

    from ..excel_style import _sheet_title, _widths, _write_rows
    from ..cmdb.models import Ci
    wb = Workbook()
    ws = wb.active
    ws.title = SYS_SHEET
    systems = db.query(System).order_by(System.code).all()
    now = dt.datetime.utcnow() + dt.timedelta(hours=7)
    row = _sheet_title(ws, "Hệ thống & Owner", f"ICO · xuất lúc {now.strftime('%d/%m/%Y %H:%M')} · nhập lại được bằng chính file này", len(SYS_HEAD))
    _write_rows(ws, row, SYS_HEAD, [[s.code, s.name, s.project, s.department, s.owner_email, s.owner_name, s.deputy_email,
                                     s.deputy_name, s.manager_email, (s.support_emails or "").replace(",", ", ") or None,
                                     s.support_name, s.support_contact, s.criticality, s.env, s.note] for s in systems])
    _widths(ws, {"A": 11, "B": 34, "C": 22, "D": 22, "E": 30, "F": 22, "G": 30, "H": 22, "I": 30, "J": 40, "K": 24, "L": 28,
                 "M": 12, "N": 12, "O": 40})
    ws.freeze_panes = ws.cell(row=row + 1, column=1)
    wv = wb.create_sheet(VM_SHEET)
    rows = (db.query(Ci.name, Ci.attrs, System.code).join(SystemMember, SystemMember.ci_id == Ci.id)
            .join(System, System.id == SystemMember.system_id).order_by(System.code, Ci.name).all())
    import json
    r2 = _sheet_title(wv, "VM theo hệ thống", "Mỗi dòng một VM; cột Hệ thống nhận mã (SYS-…) hoặc tên", len(VM_HEAD))
    _write_rows(wv, r2, VM_HEAD, [[n, json.loads(a or "{}").get("vcenterKey") or "", code] for n, a, code in rows])
    _widths(wv, {"A": 40, "B": 16, "C": 24})
    wv.freeze_panes = wv.cell(row=r2 + 1, column=1)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
