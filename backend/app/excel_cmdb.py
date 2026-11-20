"""Excel "Sổ CI": every configuration item the caller may see (cmdb/query.hidden_types) with its keys and link counts.

Sheets: Tổng hợp (CI per type × status, links strong / weak) · CI (one row per CI) · Liên kết (one row per link, capped).
Bulk reads: CIs, keys and links in three queries (no per-CI query)."""
import io
import json
from collections import defaultdict
from typing import Any, Dict, List, Optional

from openpyxl import Workbook
from sqlalchemy import func
from sqlalchemy.orm import Session, aliased

from .cmdb.models import CI_TYPES, Ci, CiKey, CiLink
from .cmdb.query import hidden_types
from .excel_style import _sheet_title, _widths, _write_rows
from .models import User

MAX_CIS = 100_000
MAX_LINKS = 100_000
TYPE_LABELS = {"vm": "VM", "hypervisor_host": "Host ảo hoá", "cluster": "Cluster", "physical_server": "Máy chủ vật lý",
               "datastore": "Datastore", "storage_array": "Mảng lưu trữ", "storage_volume": "Volume / LUN",
               "backup_client": "Client backup", "dc_asset": "Thiết bị DC", "opex_item": "Dòng Opex"}
ATTR_KEYS = ("vcenterKey", "cluster", "host", "vendor", "model", "serviceTag", "omeName", "location", "category", "dsType",
             "nbuKey", "powerOn", "vcpu", "ramGB", "endDate")


def _iso(t) -> Optional[str]:
    return t.strftime("%Y-%m-%d %H:%M") if t else None


def build_xlsx(db: Session, user: User, type_: Optional[str] = None, status: str = "active") -> bytes:
    hidden = hidden_types(db, user)
    q = db.query(Ci).filter(Ci.type.notin_(hidden) if hidden else Ci.id.isnot(None))
    if type_:
        q = q.filter(Ci.type == type_)
    if status != "all":
        q = q.filter(Ci.status == status)
    cis: List[Ci] = q.order_by(Ci.type, Ci.name, Ci.id).limit(MAX_CIS).all()
    ids = {c.id for c in cis}
    keys: Dict[int, List[str]] = defaultdict(list)
    for cid, kind, val in db.query(CiKey.ci_id, CiKey.kind, CiKey.value_norm).order_by(CiKey.ci_id, CiKey.kind):
        if cid in ids:
            keys[cid].append(f"{kind}: {val}")
    out_n: Dict[int, int] = defaultdict(int)
    in_n: Dict[int, int] = defaultdict(int)
    by_type: Dict[str, Dict[str, int]] = defaultdict(lambda: {"active": 0, "retired": 0})
    for c in cis:
        by_type[c.type][c.status if c.status in ("active", "retired") else "active"] += 1
    src, dst = aliased(Ci), aliased(Ci)
    link_rows = []
    weak = strong = 0
    for l, a, b in (db.query(CiLink, src, dst).join(src, src.id == CiLink.from_ci).join(dst, dst.id == CiLink.to_ci)
                    .order_by(CiLink.id).limit(MAX_LINKS)):
        if a.id not in ids or b.id not in ids:
            continue
        out_n[a.id] += 1
        in_n[b.id] += 1
        weak += l.confidence == "weak"
        strong += l.confidence == "strong"
        link_rows.append([TYPE_LABELS.get(a.type, a.type), a.name, l.relation, TYPE_LABELS.get(b.type, b.type), b.name,
                          "Yếu (theo tên / IP)" if l.confidence == "weak" else "Mạnh", l.source,
                          json.loads(l.attrs or "{}").get("matchedBy"), _iso(l.last_seen)])
    wb = Workbook()
    ws = wb.active
    ws.title = "Tổng hợp"
    sub = f"{'Loại ' + TYPE_LABELS.get(type_, type_) if type_ else 'Mọi loại được xem'} · trạng thái {status}"
    r = _sheet_title(ws, "Sổ CI", sub, 4)
    rows = [[TYPE_LABELS.get(t, t), by_type[t]["active"], by_type[t]["retired"], by_type[t]["active"] + by_type[t]["retired"]]
            for t in CI_TYPES if t in by_type]
    rows.append(["Tổng", sum(x[1] for x in rows), sum(x[2] for x in rows), sum(x[3] for x in rows)])
    last = _write_rows(ws, r, ["Loại CI", "Đang dùng", "Đã ngừng thấy", "Tổng"], rows)
    _write_rows(ws, last + 2, ["Liên kết", "Số lượng", "", ""], [["Mạnh (khoá cứng: serial, NAA, quan hệ kiểm kê)", strong, None, None],
                                                                 ["Yếu (ghép theo tên / IP)", weak, None, None]])
    _widths(ws, {"A": 46, "B": 12, "C": 14, "D": 10})

    w = wb.create_sheet("CI")
    r = _sheet_title(w, "CI", "Một dòng một CI; khoá chuẩn dùng để ghép giữa các nguồn", 11)
    _write_rows(w, r, ["ID", "Loại", "Tên", "Trạng thái", "Nguồn", "Khoá nguồn", "Thấy lần đầu", "Thấy lần cuối", "Khoá chuẩn",
                       "Liên kết ra / vào", "Thuộc tính"],
                [[c.id, TYPE_LABELS.get(c.type, c.type), c.name, "Đang dùng" if c.status == "active" else "Đã ngừng thấy", c.source_kind,
                  c.source_key, _iso(c.first_seen), _iso(c.last_seen), "; ".join(keys.get(c.id, [])) or None,
                  f"{out_n.get(c.id, 0)} / {in_n.get(c.id, 0)}",
                  "; ".join(f"{k}={v}" for k, v in json.loads(c.attrs or "{}").items() if k in ATTR_KEYS and v not in (None, "")) or None]
                 for c in cis], wrap_cols=(9, 11))
    _widths(w, {"A": 8, "B": 16, "C": 34, "D": 14, "E": 10, "F": 26, "G": 16, "H": 16, "I": 48, "J": 14, "K": 48})
    w.freeze_panes = w.cell(row=r + 1, column=4)

    w = wb.create_sheet("Liên kết")
    r = _sheet_title(w, "Liên kết CI", f"{len(link_rows):,} liên kết".replace(",", "."), 9)
    _write_rows(w, r, ["Loại nguồn", "CI nguồn", "Quan hệ", "Loại đích", "CI đích", "Độ tin cậy", "Nguồn dữ liệu", "Ghép theo",
                       "Thấy lần cuối"], link_rows)
    _widths(w, {"A": 16, "B": 32, "C": 14, "D": 16, "E": 32, "F": 18, "G": 12, "H": 12, "I": 16})
    w.freeze_panes = w.cell(row=r + 1, column=1)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
