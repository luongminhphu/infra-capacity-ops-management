"""Office renovation (cải tạo văn phòng) — standard cost groups, KPIs, history comparison and the
reader of the "Summarize" comparison workbook. Pure / deterministic: no database, no AI.

KPIs follow the company workbook: cost per network node and per meeting room with VC are
ROUNDUP(total / count, 0); a project without data shows "không có số liệu" (never 0, never
divided)."""
import io
import math
import re
import statistics
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .quotes import norm_text, parse_number, to_int

WORKSTREAMS = ("cabling", "network", "vc")
WS_LABELS = {"cabling": "IT Facility — Cabling (gồm Hub)", "network": "IT Network", "vc": "Video Conference"}
KIND_LABELS = {"estimate": "Dự toán", "awarded": "Báo giá trúng", "actual": "Thực chi"}
THRESHOLD = 0.15

# Standard cost groups, from the company workbook (Summarize). Aliases map vendor line descriptions.
DEFAULT_GROUPS: Dict[str, List[Tuple[str, List[str]]]] = {
    "cabling": [
        ("CABLING MATERIALS", ["cáp mạng", "cat6", "cat6a", "commscope", "patch panel", "outlet", "ổ cắm mạng", "cáp đồng"]),
        ("CÁP QUANG", ["fiber", "fibre", "cáp quang", "odf", "single mode", "multimode"]),
        ("PVC CONDUIT AND ACCESSORIES", ["ống pvc", "conduit", "máng cáp", "phụ kiện ống"]),
        ("RACK CABINET", ["rack", "tủ rack", "cabinet"]),
        ("ACCESS CONTROL", ["kiểm soát ra vào", "access control", "suprema", "elock", "khoá từ"]),
        ("CAMERA", ["camera", "cctv", "vivotek", "nvr"]),
        ("RAISE FLOOR FOR HUBROOM", ["sàn nâng", "raised floor", "raise floor"]),
        ("UPS", ["ups", "apc", "bộ lưu điện"]),
        ("AIR CONDITION FOR HUB ROOM", ["điều hoà", "điều hòa", "air condition", "daikin", "máy lạnh"]),
        ("INSTALL FEE", ["nhân công", "install", "lắp đặt", "thi công"]),
        ("ĐIỆN NGUỒN UPS CHO VC VÀ TIVI PHÒNG HỌP", ["điện nguồn", "nguồn ups cho vc", "điện cho tivi"]),
        ("DỊCH VỤ DI DỜI MÁY TÍNH", ["di dời", "relocation", "move pc"]),
        ("PROJECT MANAGEMENT", ["quản lý dự án", "project management", "pm fee"]),
        ("PHÁT SINH", ["phát sinh", "contingency", "chi phí thi công tạm"]),
    ],
    "network": [
        ("Access Point", ["access point", "wifi", "meraki", "ap "]),
        ("Switch", ["switch", "layer 3", "layer 2", "c9300", "c9350", "bản quyền", "license", "dna", "essential"]),
        ("SFP 10G", ["sfp", "module quang", "transceiver"]),
        ("Chi phí triển khai thiết bị IT & vật tư dây nhảy", ["triển khai", "dây nhảy", "patch cord", "cấu hình", "deployment"]),
    ],
    "vc": [
        ("Phòng họp có VC", ["(vc)", "video conference", "hội nghị truyền hình"]),
        ("Phòng họp chỉ trình chiếu", ["trình chiếu", "presentation", "không dây"]),
        ("Thiết bị / dịch vụ chung VC", ["license vc", "dịch vụ vc", "chung"]),
    ],
}


def roundup(total: Optional[int], count: Optional[int]) -> Optional[int]:
    if total is None or not count:
        return None
    return -(-int(total) // int(count))                          # integer ceil, same as ROUNDUP(x, 0) for x >= 0


def tags_of(text: Optional[str]) -> List[str]:
    return [t.strip() for t in re.split(r"[,;&/]+", text or "") if t.strip()]


def same_config(a: Optional[str], b: Optional[str]) -> bool:
    return {norm_text(t) for t in tags_of(a)} == {norm_text(t) for t in tags_of(b)}


# ---------------------------------------------------------------- mapping lines to groups
def match_group(text: str, groups: Sequence[Tuple[int, str, str]]) -> Optional[int]:
    """groups: (id, name, aliases-text). Exact name first, then the longest alias contained in the text."""
    t = norm_text(text)
    if not t:
        return None
    for gid, name, _ in groups:
        if norm_text(name) == t or t.startswith(norm_text(name) + " ") or norm_text(name).startswith(t + " "):
            return gid
    # The alias found EARLIEST in the text wins ("phòng IS (VC) (nếu … trình chiếu …)" is a VC room);
    # on the same position the longest alias wins.
    best, best_key = None, None
    padded = f" {t} "
    for gid, name, aliases in groups:
        for a in [name] + [x for x in (aliases or "").splitlines() if x.strip()]:
            na = norm_text(a)
            if not na:
                continue
            pos = padded.find(f" {na} ")
            if pos < 0 and len(na) >= 5:
                pos = t.find(na)
            if pos >= 0 and (best_key is None or (pos, -len(na)) < best_key):
                best, best_key = gid, (pos, -len(na))
    return best


# ---------------------------------------------------------------- KPIs and history comparison
@dataclass
class ProjectView:
    id: int
    code: str
    label: str                      # "FL6 (2026)"
    year: int
    nodes: Optional[int]
    headcount: Optional[int]
    vc_rooms: Optional[int]
    access_points: Optional[int]
    config: Optional[str]
    amounts: Dict[str, Dict[int, Dict[str, int]]]   # ws -> group_id -> kind -> amount


PREFERENCE_HISTORY = ("actual", "awarded")                # user decision: compare history by thực chi
PREFERENCE_CURRENT = ("actual", "awarded", "estimate")


def pick(kinds: Dict[str, int], order: Sequence[str]) -> Tuple[Optional[int], Optional[str]]:
    for k in order:
        if kinds.get(k) is not None:
            return kinds[k], k
    return None, None


def ws_total(p: ProjectView, ws: str, order: Sequence[str]) -> Tuple[Optional[int], Dict[int, Tuple[int, str]]]:
    per: Dict[int, Tuple[int, str]] = {}
    for gid, kinds in p.amounts.get(ws, {}).items():
        v, k = pick(kinds, order)
        if v is not None:
            per[gid] = (v, k)
    return (sum(v for v, _ in per.values()) if per else None), per


def kpis(ws: str, total: Optional[int], p: ProjectView) -> Dict[str, Optional[int]]:
    if ws == "cabling":
        return {"perNode": roundup(total, p.nodes), "perHead": roundup(total, p.headcount)}
    if ws == "network":
        return {"perNode": roundup(total, p.nodes), "perAp": roundup(total, p.access_points)}
    return {"perRoom": roundup(total, p.vc_rooms)}


KPI_LABELS = {"perNode": "đ / node mạng", "perHead": "đ / nhân sự", "perAp": "đ / access point", "perRoom": "đ / phòng họp VC"}
MAIN_KPI = {"cabling": "perNode", "network": "perNode", "vc": "perRoom"}


def _median(xs: List[int]) -> Optional[int]:
    return int(statistics.median(xs)) if xs else None


def compare(current: ProjectView, history: Sequence[ProjectView], groups: Dict[str, List[Tuple[int, str]]],
            threshold: float = THRESHOLD) -> Dict[str, Any]:
    """Current project vs previous projects, per workstream: totals, KPIs, per group, alerts.
    History values use thực chi, else báo giá trúng (never dự toán)."""
    out: Dict[str, Any] = {}
    for ws in WORKSTREAMS:
        cur_total, cur_per = ws_total(current, ws, PREFERENCE_CURRENT)
        cur_kpi = kpis(ws, cur_total, current)
        cols = []
        for h in history:
            t, per = ws_total(h, ws, PREFERENCE_HISTORY)
            cols.append({"projectId": h.id, "code": h.code, "label": h.label, "config": h.config, "total": t,
                         "kpi": kpis(ws, t, h), "sameConfig": same_config(h.config, current.config),
                         "groups": {gid: v for gid, (v, _) in per.items()},
                         "kinds": sorted({k for _, k in per.values()}), "nodes": h.nodes, "headcount": h.headcount,
                         "count": h.vc_rooms if ws == "vc" else h.nodes})
        main = MAIN_KPI[ws]
        same = [c["kpi"][main] for c in cols if c["sameConfig"] and c["kpi"][main] is not None]
        anyc = [c["kpi"][main] for c in cols if c["kpi"][main] is not None]
        basis = same if same else anyc
        med = _median(basis)
        alerts = []
        dev = None
        if cur_kpi[main] is not None and med:
            dev = round((cur_kpi[main] - med) * 100 / med, 1)
            if abs(dev) > threshold * 100:
                word = "cao hơn" if dev > 0 else "thấp hơn"
                msg = (f"{WS_LABELS[ws]}: {cur_kpi[main]:,} {KPI_LABELS[main]}, {word} trung vị lịch sử {med:,} "
                       f"({dev:+.1f}%).").replace(",", ".")
                if not same:
                    cfgs = sorted({c['config'] for c in cols if c['config']})
                    msg += f" Khác cấu hình ({current.config or 'chưa ghi'} so với {', '.join(cfgs) or 'chưa ghi'}) — không kết luận đắt / rẻ."
                alerts.append({"level": "info" if not same else ("warn" if abs(dev) <= 2 * threshold * 100 else "crit"), "message": msg})
        elif cur_total is not None and not anyc:
            alerts.append({"level": "info", "message": f"{WS_LABELS[ws]}: chưa có số liệu lịch sử để so sánh."})
        rows = []
        for gid, name in groups.get(ws, []):
            hv = [c["groups"].get(gid) for c in cols]
            hist_vals = [v for v in hv if v is not None]
            cv = cur_per.get(gid, (None, None))[0]
            gmed = _median(hist_vals)
            rows.append({"groupId": gid, "name": name, "current": cv, "currentKind": cur_per.get(gid, (None, None))[1],
                         "history": hv, "median": gmed, "min": min(hist_vals) if hist_vals else None,
                         "max": max(hist_vals) if hist_vals else None,
                         "deviationPct": round((cv - gmed) * 100 / gmed, 1) if cv is not None and gmed else None})
        out[ws] = {"total": cur_total, "kinds": sorted({k for _, k in cur_per.values()}), "kpi": cur_kpi, "mainKpi": main,
                   "median": med, "deviationPct": dev, "sameConfigCount": len(same), "history": cols, "rows": rows, "alerts": alerts}
    return out


# ---------------------------------------------------------------- reading the "Summarize" workbook
_FLOOR = re.compile(r"\b(FL\s*\d{1,3}[A-Z]?|L\s*\d{1,3}|T(?:ầng|ang)\s*\d{1,3})\b", re.I)


def parse_header(text: Any) -> Dict[str, Any]:
    """'FL12 \\n(62 nodes) - 24 NS' / 'FL6 \\n(10G & Wifi7)' -> floor, nodes, headcount, config."""
    s = str(text or "").replace("\n", " ")
    m = _FLOOR.search(s)
    floor = re.sub(r"\s+", "", m.group(1)).upper() if m else None
    nodes = re.search(r"(\d+)\s*nodes?", s, re.I)
    ns = re.search(r"(\d+)\s*NS\b", s)
    config = None
    for par in re.findall(r"\(([^)]*)\)", s):
        if not re.search(r"\d+\s*nodes?", par, re.I):
            config = par.strip()
    return {"floor": floor, "nodes": int(nodes.group(1)) if nodes else None, "headcount": int(ns.group(1)) if ns else None,
            "config": config}


def _ws_of(title: str) -> Optional[str]:
    t = norm_text(title)
    if "video" in t or "conference" in t or re.search(r"\bvc\b", t):
        return "vc"
    if "network" in t or "mang" in t.split():
        return "network"
    if "facility" in t or "cabling" in t or "ha tang" in t:
        return "cabling"
    return None


@dataclass
class ImportedProject:
    floor: str
    nodes: Optional[int] = None
    headcount: Optional[int] = None
    config: Optional[str] = None
    vc_rooms: Optional[int] = None
    meeting_rooms: Optional[int] = None
    amounts: Dict[str, Dict[str, int]] = field(default_factory=dict)    # ws -> label -> amount
    stated: Dict[str, int] = field(default_factory=dict)                # ws -> total printed in the block


@dataclass
class SummarizeResult:
    projects: List[ImportedProject] = field(default_factory=list)
    findings: List[Dict[str, Any]] = field(default_factory=list)
    message: str = ""


def _cells(ws_) -> List[List[Any]]:
    return [list(r) for r in ws_.iter_rows(max_row=400, max_col=40, values_only=True)]


def read_summarize(data: bytes) -> SummarizeResult:
    from openpyxl import load_workbook
    from .quotes import _guard_zip
    try:
        _guard_zip(data)
        wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    except Exception as exc:
        return SummarizeResult(message=f"Không đọc được file ({type(exc).__name__}).")
    res = SummarizeResult()
    by_floor: Dict[str, ImportedProject] = {}
    for sheet in wb.worksheets[:5]:
        rows = _cells(sheet)
        _read_sheet(rows, res, by_floor)
    res.projects = list(by_floor.values())
    if not res.projects:
        res.message = "Không tìm thấy bảng so sánh lịch sử (dòng tiêu đề “Components” với các cột tầng FL…)."
    return res


def _text(v: Any) -> str:
    return str(v).strip() if v not in (None, "") else ""


def _read_sheet(rows: List[List[Any]], res: SummarizeResult, by_floor: Dict[str, ImportedProject]) -> None:
    i = 0
    while i < len(rows):
        row = rows[i]
        # A comparison block: header row with "Components" followed by ≥ 1 column whose header names a floor.
        comp_cols = [j for j, v in enumerate(row) if norm_text(_text(v)) == "components"]
        hit = None
        for cj in comp_cols:
            floors = [(j, parse_header(v)) for j, v in enumerate(row) if j > cj and parse_header(v)["floor"]]
            if floors:
                hit = (cj, floors)
        if not hit:
            i += 1
            continue
        cj, floors = hit
        # Workstream: title of the left block on the row above (B8 "FL6 - IT FACILITY"); the right title is checked too.
        above = rows[i - 1] if i else []
        left = next((_text(v) for v in above[:cj] if _ws_of(_text(v))), "")
        right = next((_text(v) for v in above[cj - 1:] if "so sanh" in norm_text(_text(v))), "")
        ws = _ws_of(left) or _ws_of(right)
        if not ws:
            i += 1
            continue
        if right and _ws_of(right) and _ws_of(right) != ws:
            res.findings.append({"code": "LABEL", "message": f"Tiêu đề bảng so sánh “{right}” không khớp khối {WS_LABELS[ws]} (dòng {i})."})
        projects = []
        for j, h in floors:
            p = by_floor.setdefault(h["floor"], ImportedProject(floor=h["floor"]))
            p.nodes = p.nodes or h["nodes"]
            p.headcount = p.headcount or h["headcount"]
            p.config = p.config or h["config"]
            projects.append((j, p))
        # Left block columns (current floor detail): "Price" column on the same header row.
        price_col = next((j for j, v in enumerate(row[:cj]) if norm_text(_text(v)) in ("price", "don gia", "du toan")), None)
        left_comp = next((j for j, v in enumerate(row[:cj]) if norm_text(_text(v)) == "components"), None)
        k = i + 1
        lefts: Dict[str, Optional[int]] = {}
        while k < len(rows):
            r = rows[k]
            label = _text(r[cj]) if cj < len(r) else ""
            lab_n = norm_text(label)
            if not label:
                vals = [(p, to_int(parse_number(r[j]))) for j, p in projects if j < len(r)]
                if k == i + 1 and any(v is not None for _, v in vals):      # totals row right under the header
                    for p, v in vals:
                        if v is not None:
                            p.stated[ws] = v
                    if left_comp is not None and price_col is not None:
                        pass
                    k += 1
                    continue
                if not any(_text(x) for x in r):
                    break
                k += 1
                continue
            if lab_n.startswith("chi phi trung binh") or lab_n.startswith("tong") or "so sanh" in lab_n or lab_n == "components":
                if lab_n.startswith("chi phi trung binh"):
                    k += 1
                    continue
                break
            for j, p in projects:
                v = to_int(parse_number(r[j])) if j < len(r) else None
                if v is not None:
                    p.amounts.setdefault(ws, {})[label] = p.amounts.setdefault(ws, {}).get(label, 0) + v
            k += 1
        # Left block lines (dự toán of the current floor) for the estimate-vs-history check (#1 in the design).
        if left_comp is not None and price_col is not None:
            k2 = i + 1
            while k2 < len(rows):
                r = rows[k2]
                lab = _text(r[left_comp]) if left_comp < len(r) else ""
                if not lab and not any(_text(x) for x in r[:cj]):
                    break
                if lab and not norm_text(lab).startswith("chi phi trung binh"):
                    lefts[lab] = to_int(parse_number(r[price_col]))
                k2 += 1
        _check_block(ws, projects, lefts, rows, i, cj, price_col, res)
        if ws == "vc":
            for _, p in projects:
                vals = p.amounts.get("vc", {})
                p.meeting_rooms = sum(1 for v in vals.values() if v) or None
                p.vc_rooms = sum(1 for lab, v in vals.items() if v and "(vc)" in lab.lower()) or None
        i = k


def _check_block(ws: str, projects, lefts: Dict[str, Optional[int]], rows, i: int, cj: int, price_col, res: SummarizeResult) -> None:
    for _, p in projects:
        got = sum(p.amounts.get(ws, {}).values())
        st = p.stated.get(ws)
        if st is not None and p.amounts.get(ws) and abs(st - got) > 1000:
            res.findings.append({"code": "TOTAL", "floor": p.floor, "workstream": ws,
                                 "message": f"{p.floor} · {WS_LABELS[ws]}: tổng ghi {_vn(st)} khác cộng các nhóm {_vn(got)}."})
        if st == 0 or (st is None and not p.amounts.get(ws)):
            res.findings.append({"code": "NODATA", "floor": p.floor, "workstream": ws,
                                 "message": f"{p.floor} · {WS_LABELS[ws]}: không có số liệu — không dùng để so sánh (không ghi 0)."})
    if not lefts or price_col is None:
        return
    est_total = to_int(parse_number(rows[i + 1][price_col])) if i + 1 < len(rows) and price_col < len(rows[i + 1]) else None
    # The current floor = the comparison column whose amounts match the left block best.
    cur = None
    for _, p in projects:
        same = sum(1 for lab, v in lefts.items() if v is not None and p.amounts.get(ws, {}).get(lab) == v)
        if same and (cur is None or same > cur[1]):
            cur = (p, same)
    if not cur:
        return
    p = cur[0]
    hist = p.amounts.get(ws, {})
    gaps = [(lab, hist.get(lab)) for lab, v in lefts.items() if not v and hist.get(lab)]
    if est_total is not None and gaps:
        diff = sum(v for _, v in gaps)
        hist_total = p.stated.get(ws) or sum(hist.values())
        res.findings.append({
            "code": "ESTIMATE", "floor": p.floor, "workstream": ws, "amount": hist_total - est_total,
            "message": (f"{p.floor} · {WS_LABELS[ws]}: dự toán {_vn(est_total)} thấp hơn số so sánh {_vn(hist_total)}, lệch "
                        f"{_vn(hist_total - est_total)} — " + " + ".join(f"{lab} {_vn(v)}" for lab, v in gaps) +
                        " có trong bảng so sánh nhưng trống ở cột dự toán.")})


def _vn(v: Optional[int]) -> str:
    return "—" if v is None else f"{v:,}".replace(",", ".")
