"""Chuẩn hoá BOM — read a raw vendor Bill of Materials (Excel) and organise it by category.

Pipeline (all rule-based; AI is optional, see bom_ai in routers/bom.py):
1. read_rows   — pick the sheet / header row with the most item lines (part number, description, qty, price…).
2. split       — cut the lines into configurations: a title row (text, no qty / price) or a base-model line
                 (PowerEdge R760, Catalyst 9300, FortiGate 600F…) starts a new configuration.
3. classify    — score each configuration against category keywords (title counts more than components).
4. summarise   — category-specific extraction from the descriptions: server CPU / RAM / disk / NIC / HBA,
                 switch ports / license tier, firewall throughput / subscription, storage raw TB, SAN ports…

Quantities: vendors list component quantities either per unit (Dell) or for all units (HPE). `qtyMode`
is guessed ("total" when every component quantity is a multiple of the configuration quantity) and can be
changed by the user; summaries are always per unit."""
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .shared.text import norm_text, parse_number

class BomError(ValueError):
    """A problem with the uploaded file; the message is written by us and safe to show."""


MAX_ROWS = 5000
MAX_COLS = 40
MAX_CONFIGS = 200
MAX_ITEMS = 3000

CATEGORIES: List[Tuple[str, str]] = [
    ("server", "Máy chủ (Server)"),
    ("network", "Thiết bị mạng (Network)"),
    ("firewall", "Tường lửa (Firewall)"),
    ("storage", "Lưu trữ (Storage)"),
    ("san_switch", "SAN Switch"),
    ("load_balancer", "Cân bằng tải (Load Balancer)"),
    ("backup", "Sao lưu (Backup)"),
    ("accessory", "Phụ kiện (module quang, cáp)"),
    ("software", "Phần mềm & License"),
    ("service", "Dịch vụ & Bảo hành"),
    ("other", "Khác"),
]
CATEGORY_LABELS = dict(CATEGORIES)

# keyword → weight; matched on normalised lowercase text (accents removed)
KEYWORDS: Dict[str, List[str]] = {
    "server": ["server", "poweredge", "proliant", "thinksystem", "ucs c", "ucs b", "ucs x", "synergy", "rack server", "may chu", "server node",
               "xeon", "epyc", "rdimm", "lrdimm", "boss", "idrac", "ilo", "xcc"],
    "network": ["network", "switch", "catalyst", "nexus", "aruba", "juniper ex", "qfx", "access switch", "core switch", "distribution switch", "leaf", "spine",
                "ethernet switch", "switch layer", "wi fi", "802 11", "access point", "ap-6", "ap 6", "l3 switch", "l2 switch", "thiet bi mang", "router", "access point", "wireless", "s5248", "s4148",
                "cx 6", "cx 8", "ce6", "ce12", "huawei s", "dna advantage", "dna essentials"],
    "firewall": ["fortigate", "palo alto", "pa-", "checkpoint", "check point", "firewall", "tuong lua", "sophos", "ngfw", "utm", "srx",
                 "threat prevention", "fortiguard", "utp bundle"],
    "storage": ["powerstore", "powermax", "unity", "powervault", "me5", "msa", "primera", "alletra", "netapp", "fas", "aff a", "dorado",
                "oceanstor", "storage", "storage array", "san storage", "luu tru", "nas", "disk enclosure", "expansion enclosure", "storage controller"],
    "san_switch": ["mds 9", "brocade", "g620", "g720", "g730", "sn3600", "sn6600", "san switch", "fc switch", "fibre channel switch", "connectrix",
                   "ds-6", "ports on demand"],
    "load_balancer": ["big-ip", "f5", "netscaler", "citrix adc", "a10", "thunder", "load balancer", "can bang tai", "kemp", "avi", "ltm"],
    "accessory": ["transceiver", "direct attach", "dac", "sfp", "qsfp", "patch cord", "fiber cable", "cap quang", "mount bracket"],
    "backup": ["data domain", "powerprotect", "veeam", "commvault", "netbackup", "tape", "lto", "backup", "sao luu", "storeonce"],
    "software": ["vsphere", "vmware", "windows server", "sql server", "red hat", "rhel", "microsoft", "subscription", "phan mem", "license key",
                 "vcenter", "vcf", "vvf", "license", "licenses", "orchestrator", "automation platform", "nativeedge"],
    "service": ["installation", "deployment", "trien khai", "professional service", "prosupport", "care pack", "smartnet", "warranty",
                "bao hanh", "support service", "dao tao", "training", "lap dat", "cau hinh", "implementation"],
}
LAYOUT_LABELS = {"dell_configurator": "Dell Solutions Configurator", "cisco_ccw": "Cisco Commerce Workspace (CCW)",
                 "hpe_iris": "HPE Aruba (IRIS)",
                 "hpe": "HPE (One Config / báo giá)", "generic": "BOM thông thường"}
# Base-model patterns: a line matching one starts a new configuration (when the current one already has a model).
MODEL_RE = re.compile(
    r"\b(poweredge\s+[a-z]?\d{3,4}\w*|proliant\s+\w+\s*gen\s*\d+|thinksystem\s+\w+|ucs\s+[a-z]\d{3}\s*m\d|catalyst\s+\d{4}\w*|nexus\s+\d{4}\w*|"
    r"fortigate[-\s]?\d+[a-z]?\b|pa-\d{3,4}\w*|powerstore\s+\d{3,4}t?\b|powervault\s+me\d{4}\b|unity\s+xt\s*\d{3}f?\b|powermax\s*\d{4}\b|"
    r"mds\s*9\d{3}\w*|brocade\s+g\d{3}\w*|big-ip\s+[a-z]?\d+\w*|netscaler\s+(mpx|sdx)\s*\d+\w*|data\s*domain\s+dd\d+\w*|"
    r"aruba\s+cx\s*\d{4}\w*|s\d{4}-\w+|msa\s*\d{4}\w*|alletra\s+\d+\w*|primera\s+\w+)",
    re.IGNORECASE)

HEADER_KEYS = {
    "pn": ["part number", "part no", "part#", "p/n", "pn", "sku", "product number", "product no", "product", "item code", "ma hang", "ma sp", "model", "code"],
    "desc": ["description", "product description", "mo ta", "dien giai", "ten hang", "noi dung", "item description", "ten thiet bi", "hang muc", "specification"],
    "qty": ["qty", "quantity", "so luong", "sl", "q'ty", "units"],
    "unit": ["dvt", "don vi", "uom", "unit"],
    "price": ["unit price", "don gia", "price", "list price", "unit cost", "net price"],
    "total": ["total", "extended", "thanh tien", "amount", "ext price", "line total", "total price"],
}


@dataclass
class Item:
    pn: str = ""
    desc: str = ""
    unit: str = ""
    qty: Optional[Decimal] = None
    price: Optional[Decimal] = None
    total: Optional[Decimal] = None
    row: int = 0
    module: str = ""                     # component type given by the vendor tool (Dell: "Processor", "Hard Drives"…)


@dataclass
class Config:
    title: str
    items: List[Item] = field(default_factory=list)
    model_line: Optional[int] = None
    explicit_title: bool = False
    qty: Optional[int] = None            # set by layouts that state it (Dell configurator "Product Qty")
    price_basis: str = "total"           # "per_unit": line totals are for ONE unit (× qty for the configuration)
    unit_price: Optional[Decimal] = None # vendor's price of one unit, to cross-check the lines
    qty_mode: Optional[str] = None       # known from the layout (CCW / IRIS children: "total"); None = guess


# ---------------------------------------------------------------- reading
def _norm(v: Any) -> str:
    return norm_text(str(v)) if v is not None else ""


def _header_map(row: Sequence[Any]) -> Dict[str, int]:
    cells = [_norm(c) for c in row]
    found: Dict[str, int] = {}
    for key in ("total", "price", "qty", "desc", "pn", "unit"):            # specific first: "unit price" is price, not unit
        for i, c in enumerate(cells):
            if not c or i in found.values():
                continue
            if any(c == k or c.startswith(k + " ") or c.startswith(k + "(") or (len(k) > 3 and k in c) for k in HEADER_KEYS[key]):
                found.setdefault(key, i)
                break
    return found


def _cell(row: Sequence[Any], i: Optional[int]) -> Any:
    return row[i] if i is not None and i < len(row) else None


def _text(v: Any, n: int = 1000) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return " ".join(str(v).split())[:n]


def read_rows(data: bytes) -> List[Tuple[str, List[List[Any]]]]:
    """All sheets' rows (bounded, zip-bomb guarded): [(sheet, rows)]."""
    from .shared import xlsx_io
    return xlsx_io.read_rows(data, max_sheets=10, max_rows=MAX_ROWS, max_cols=MAX_COLS)


def _items_from(rows: List[List[Any]]) -> Tuple[Optional[int], Dict[str, int], List[Tuple[int, List[Any]]]]:
    """Header row index, column map and the data rows below it."""
    best: Tuple[int, Optional[int], Dict[str, int]] = (-1, None, {})
    for i, r in enumerate(rows[:60]):
        m = _header_map(r)
        score = len(m) + (2 if "desc" in m else 0) + (1 if "qty" in m else 0)
        if "desc" in m and score > best[0]:
            best = (score, i, m)
    _, h, m = best
    if h is None:
        return None, {}, []
    return h, m, [(j + 1, r) for j, r in enumerate(rows[h + 1:], start=h + 1)]


# ---------------------------------------------------------------- splitting
def parse(data: bytes) -> Dict[str, Any]:
    """Raw BOM bytes → structured preview (dict, JSON-ready). Raises ValueError with a safe message."""
    try:
        sheets = read_rows(data)
    except Exception as exc:  # noqa: BLE001 — malformed file
        raise BomError(f"Không đọc được file Excel ({type(exc).__name__}).") from None
    for name, rows in sheets:
        conf = _dell_configurator(name, rows) or _line_quote(name, rows)
        if conf is not None:
            return conf
    best = None
    for name, rows in sheets:
        h, m, body = _items_from(rows)
        if h is None:
            continue
        n = sum(1 for _, r in body if _text(_cell(r, m.get("desc"))) and parse_number(_cell(r, m.get("qty"))) is not None)
        if best is None or n > best[0]:
            best = (n, name, h, m, body)
    if not best or best[0] == 0:
        raise BomError("Không tìm thấy bảng BOM: cần dòng tiêu đề có cột Mô tả (Description) và Số lượng (Qty).")
    _, sheet, h, m, body = best
    configs, warnings, currency = _split(body, m)
    out_configs = [config_out(c, i) for i, c in enumerate(configs[:MAX_CONFIGS])]
    if len(configs) > MAX_CONFIGS:
        warnings.append(f"File có {len(configs)} cấu hình — chỉ lấy {MAX_CONFIGS} cấu hình đầu.")
    header_text = " ".join(_text(x) for x in sheets_header(sheets, sheet, h))
    if currency is None:
        currency = "USD" if re.search(r"\busd\b|\$", header_text, re.IGNORECASE) else "VND"
    alltext = " ".join(c.title + " " + " ".join(i.desc for i in c.items[:20]) for c in configs[:50]).lower()
    vendor = "HPE" if re.search(r"proliant|\bhpe\b|aruba|alletra|synergy", alltext) else ""
    return {"sheet": sheet, "headerRow": h + 1, "layout": "hpe" if vendor == "HPE" else "generic", "projectName": "",
            "columns": {k: v + 1 for k, v in m.items()}, "currency": currency,
            "categories": [{"key": k, "label": lbl} for k, lbl in CATEGORIES], "configs": out_configs, "warnings": warnings}


# ---------------------------------------------------------------- Dell Solutions Configurator export
_DELL_COLS = {"product name": "product", "product qty": "pqty", "unit price list": "uprice", "extended price list": "ext",
              "module name": "module", "option name": "option", "option list price": "oprice", "skus": "sku", "qty": "qty"}


def _dell_configurator(sheet: str, rows: List[List[Any]]) -> Optional[Dict[str, Any]]:
    """Product rows (system + qty + price) followed by option rows (module, option, SKU, qty per unit,
    option price for one unit). Returns the preview dict, or None when the layout does not match."""
    for h, r in enumerate(rows[:40]):
        names = {_norm(c): i for i, c in enumerate(r) if c is not None}
        m = {key: names[k] for k, key in _DELL_COLS.items() if k in names}
        if {"product", "option", "module", "oprice"} <= m.keys():
            break
    else:
        return None
    configs: List[Config] = []
    warnings: List[str] = []
    cur: Optional[Config] = None
    n_items = 0
    for rowno, r in enumerate(rows[h + 1:], start=h + 2):
        product = _text(_cell(r, m.get("product")), 200)
        option = _text(_cell(r, m.get("option")), 1000)
        if product:
            title = re.sub(r"\s*-\s*\[[^\]]*\]\s*$", "", product).strip()
            q = parse_number(_cell(r, m.get("pqty")))
            cur = Config(title=title[:200], explicit_title=True, qty=int(q) if q and q == int(q) and q > 0 else 1,
                         price_basis="per_unit", unit_price=parse_number(_cell(r, m.get("uprice"))))
            configs.append(cur)
            continue
        if not option or cur is None:
            continue
        if n_items >= MAX_ITEMS:
            warnings.append(f"Quá {MAX_ITEMS} dòng hàng — phần sau bị bỏ.")
            break
        qty = parse_number(_cell(r, m.get("qty"))) or Decimal(1)
        line = parse_number(_cell(r, m.get("oprice")))
        module = _text(_cell(r, m.get("module")), 120)
        cur.items.append(Item(pn=_text(_cell(r, m.get("sku")), 80), desc=option, unit="", qty=qty,
                              price=(line / qty).quantize(Decimal("0.01")) if line is not None and qty else None,
                              total=line, row=rowno, module=module))
        if cur.model_line is None and _norm(module) in ("base", "dell automation platform outcome base") or (cur.model_line is None and _norm(module).endswith(" base")):
            cur.model_line = len(cur.items) - 1
        n_items += 1
    configs = [c for c in configs if c.items]
    if not configs:
        return None
    for c in configs:
        s = sum((i.total for i in c.items if i.total is not None), Decimal(0))
        if c.unit_price is not None and abs(s - c.unit_price) > 1:
            warnings.append(f"{c.title}: tổng các option ({_fmt(s)}) khác giá 1 thiết bị trên file ({_fmt(c.unit_price)}).")
    heads = [str(r[0]) for r in rows[:h] if r and r[0] is not None]
    head = " ".join(heads)
    name = next((re.match(r"\s*solution name:\s*(.+)", x, re.IGNORECASE) for x in heads if re.match(r"\s*solution name:", x, re.IGNORECASE)), None)
    out = [config_out(c, i) for i, c in enumerate(configs[:MAX_CONFIGS])]
    return {"sheet": sheet, "headerRow": h + 1, "layout": "dell_configurator", "columns": {k: v + 1 for k, v in m.items()},
            "currency": "USD" if re.search(r"\$|\busd\b", head, re.IGNORECASE) else "VND", "projectName": name.group(1).strip()[:120] if name else "",
            "categories": [{"key": k, "label": lbl} for k, lbl in CATEGORIES], "configs": out, "warnings": warnings}


# ---------------------------------------------------------------- line-numbered quotes: Cisco CCW, HPE Aruba IRIS
_LINE_NO = re.compile(r"^\d+(\.\d+)*$")
_CHASSIS_EXCLUDE = re.compile(r"module|cable|power supply|license|licence|kit\b|sfp|qsfp|transceiver|stack|fan|rack|bracket|mount|"
                              r"\bsntc?\b|smart ?net|subscription|plug-n-play|\bspare\b", re.IGNORECASE)


def _quote_columns(cells: List[str]) -> Dict[str, Optional[int]]:
    def col(*names, contains=None, exclude=None):
        for i, c in enumerate(cells):
            if exclude and any(x in c for x in exclude):
                continue
            if c in names or (contains and all(w in c for w in contains)):
                return i
        return None
    unit = col(contains=["unit", "net"])
    ext = col(contains=["extended", "net"])
    if unit is None:
        unit = col("unit price", "price", contains=["unit", "price"], exclude=["list"])
    if ext is None:
        ext = col("total", "total price", "extended price", "ext price", "line total", exclude=["sub"])
    if unit is None:
        unit = col(contains=["unit", "list"])
    if ext is None:
        ext = col(contains=["extended", "list"])
    return {"line": col("line number", "line no", "line", "line #"), "pn": col("part number", "sku", "product number", contains=["part", "number"]),
            "desc": col("description", contains=["description"]), "qty": col("qty", "quantity"), "unit": unit, "ext": ext,
            "duration": col(contains=["duration"]), "vendor": col("manufacturer", "vendor")}


def _line_quote(sheet: str, rows: List[List[Any]]) -> Optional[Dict[str, Any]]:
    """Cisco CCW estimate (flat, or with line numbers 1.0 / 1.1) and HPE Aruba IRIS (Line# 1.0 / 1.1).
    A line x.0 is one device; its children follow. Child quantities are for ALL units of the device.
    In a flat Cisco estimate a new device starts at a chassis line (model pattern, not a module /
    licence / service / cable). Net price when present, otherwise the unit / list price."""
    for h, r in enumerate(rows[:40]):
        cells = [_norm(c) for c in r]
        m = _quote_columns(cells)
        if m["pn"] is not None and m["desc"] is not None and m["qty"] is not None and (
                m["line"] is not None or any("service duration" in c or "smart account" in c for c in cells)):
            break
    else:
        return None
    hierarchical = m["line"] is not None
    configs: List[Config] = []
    warnings: List[str] = []
    cur: Optional[Config] = None
    n_items, found, vendors = 0, 0, set()
    for rowno, r in enumerate(rows[h + 1:], start=h + 2):
        pn = _text(_cell(r, m["pn"]), 80)
        desc = _text(_cell(r, m["desc"]), 1000)
        no = _text(_cell(r, m["line"]), 20) if hierarchical else ""
        if hierarchical and not _LINE_NO.match(no):
            continue
        if not hierarchical and (not pn or not desc):
            continue
        qty = parse_number(_cell(r, m["qty"]))
        if qty is None:
            continue
        found += 1
        unit = parse_number(_cell(r, m["unit"])) if m["unit"] is not None else None
        ext = parse_number(_cell(r, m["ext"])) if m["ext"] is not None else None
        if ext is None and unit is not None:
            ext = unit * qty
        if m["vendor"] is not None and _cell(r, m["vendor"]):
            vendors.add(_text(_cell(r, m["vendor"]), 60))
        dur = _text(_cell(r, m["duration"]), 10) if m["duration"] is not None else ""
        if dur and dur not in ("0", "---", "-", "N/A"):
            desc = f"{desc} ({dur} tháng)"
        if hierarchical:
            top = re.fullmatch(r"\d+(\.0)?", no) is not None
        else:
            top = cur is None or (bool(MODEL_RE.search(desc)) and not _CHASSIS_EXCLUDE.search(desc) and not pn.upper().startswith("CON-")
                                  and not pn.endswith("="))
        if top:
            cur = Config(title=desc[:140], explicit_title=True, qty=_int(qty) or 1, price_basis="total", qty_mode="total")
            cur.model_line = 0
            configs.append(cur)
        if n_items >= MAX_ITEMS:
            warnings.append(f"Quá {MAX_ITEMS} dòng hàng — phần sau bị bỏ.")
            break
        cur.items.append(Item(pn=pn, desc=desc, qty=qty, price=unit, total=ext, row=rowno))
        n_items += 1
    if found < 2 or not configs:
        return None
    before = len(configs)
    configs = merge_identical(configs)
    if len(configs) < before:
        warnings.append(f"Gộp {before - len(configs)} cấu hình giống hệt nhau (cộng số lượng).")
    head = " ".join(_text(x) for rr in rows[:h + 1] for x in rr if x is not None)
    is_hpe = any("hewlett" in v.lower() or "aruba" in v.lower() for v in vendors) or re.search(r"aruba|\bhpe\b", " ".join(c.title for c in configs), re.IGNORECASE)
    layout = "hpe_iris" if is_hpe else "cisco_ccw"
    cur_txt = head + " " + " ".join(_text(x) for rr in rows[h + 1:h + 3] for x in rr if x is not None)
    number_titles(configs)
    out = [config_out(c, i) for i, c in enumerate(configs[:MAX_CONFIGS])]
    return {"sheet": sheet, "headerRow": h + 1, "layout": layout, "columns": {k: v + 1 for k, v in m.items() if v is not None},
            "currency": "VND" if re.search(r"\bvnd\b", cur_txt, re.IGNORECASE) else "USD", "projectName": "",
            "categories": [{"key": k, "label": lbl} for k, lbl in CATEGORIES], "configs": out, "warnings": warnings}


def _signature(c: Config) -> Tuple:
    n = config_qty(c)
    per = lambda q: (q / n) if c.price_basis == "total" and q is not None else q  # noqa: E731
    return (c.price_basis, tuple((i.pn.upper(), i.desc, per(i.qty), per(i.total)) for i in c.items))


def number_titles(configs: List[Config]) -> None:
    """Same model with different options: "… (cấu hình 1)", "… (cấu hình 2)" so they can be told apart."""
    by: Dict[str, List[Config]] = {}
    for c in configs:
        by.setdefault(c.title, []).append(c)
    for title, group in by.items():
        if len(group) > 1:
            for k, c in enumerate(group, 1):
                c.title = f"{title[:120]} (cấu hình {k})"


def merge_identical(configs: List[Config]) -> List[Config]:
    """Same device with the same options listed several times (IRIS / CCW line per site): one
    configuration with the quantities added up."""
    out: List[Config] = []
    seen: Dict[Tuple, Config] = {}
    for c in configs:
        sig = _signature(c)
        if sig in seen:
            base = seen[sig]
            k = config_qty(c)
            if base.price_basis == "total":
                for a, b in zip(base.items, c.items):
                    a.qty = (a.qty or Decimal(0)) + (b.qty or Decimal(0))
                    a.total = (a.total or Decimal(0)) + (b.total or Decimal(0)) if (a.total is not None or b.total is not None) else None
            base.qty = config_qty(base) + k
            continue
        seen[sig] = c
        out.append(c)
    return out


def sheets_header(sheets, name, h):
    for n, rows in sheets:
        if n == name:
            return rows[h]
    return []


def _split(body: List[Tuple[int, List[Any]]], m: Dict[str, int]) -> Tuple[List[Config], List[str], Optional[str]]:
    configs: List[Config] = []
    warnings: List[str] = []
    currency = None
    cur: Optional[Config] = None
    n_items = 0
    blank_run = 0
    for rowno, r in body:
        desc = _text(_cell(r, m.get("desc")))
        pn = _text(_cell(r, m.get("pn")), 80)
        qty = parse_number(_cell(r, m.get("qty")))
        price = parse_number(_cell(r, m.get("price")))
        total = parse_number(_cell(r, m.get("total")))
        first = _text(next((x for x in r if x not in (None, "")), None), 300)
        if not desc and not pn and qty is None:
            blank_run += 1
            if blank_run >= 30 and configs:
                break
            if first and not any(isinstance(x, (int, float)) for x in r if x is not None):
                desc = first                                    # title written in another column
            else:
                continue
        blank_run = 0
        if currency is None and any(isinstance(x, str) and re.search(r"\$|\busd\b", x, re.IGNORECASE) for x in (_cell(r, m.get("price")), _cell(r, m.get("total")))):
            currency = "USD"
        low = _norm(desc)
        if re.match(r"^(tong|total|grand total|sub ?total|cong|vat|thue)\b", low) and qty is None:
            continue                                            # vendor subtotal / VAT lines: recomputed by us
        is_title = qty is None and price is None and total is None and not pn and bool(desc)
        if is_title:
            if cur is None or cur.items or not cur.explicit_title:
                cur = Config(title=desc[:200], explicit_title=True)
                configs.append(cur)
            else:
                cur.title = f"{cur.title} — {desc}"[:200]
            continue
        is_model = bool(MODEL_RE.search(desc)) and (qty is not None)
        if cur is None or (is_model and cur.model_line is not None and not _same_model(cur, desc)):
            cur = Config(title=_model_title(desc) if is_model else "Cấu hình chưa đặt tên")
            configs.append(cur)
        if is_model and cur.model_line is None:
            cur.model_line = len(cur.items)
            if not cur.explicit_title:
                cur.title = _model_title(desc)
        if n_items >= MAX_ITEMS:
            warnings.append(f"Quá {MAX_ITEMS} dòng hàng — phần sau bị bỏ.")
            break
        if total is None and qty is not None and price is not None:
            total = qty * price
        cur.items.append(Item(pn=pn, desc=desc, unit=_text(_cell(r, m.get("unit")), 20), qty=qty, price=price, total=total, row=rowno))
        n_items += 1
    configs = [c for c in configs if c.items]
    return configs, warnings, currency


def _model_title(desc: str) -> str:
    m = MODEL_RE.search(desc)
    return (m.group(0).strip() if m else desc)[:120]


def _same_model(c: Config, desc: str) -> bool:
    m = c.items[c.model_line].desc if c.model_line is not None else ""
    return _norm(_model_title(m)) == _norm(_model_title(desc))


# ---------------------------------------------------------------- classify + summarise
def classify(title: str, descs: Sequence[str]) -> str:
    t = _norm(title)
    body = " ".join(_norm(d) for d in descs[:200])
    scores = {}
    for cat, words in KEYWORDS.items():
        s = 0
        for w in words:
            w = _norm(w) if "-" in w else w
            if w in t:
                s += 5
            s += min(body.count(w), 5)
        scores[cat] = s
    # licences / services only win when nothing physical scores
    if re.search(r"subscription|licen[cs]e|\bsaas\b", t) and not MODEL_RE.search(title):
        return "software"                                   # "Aruba Central … Subscription", "DNA … Term license"
    if re.search(r"transceiver|direct attach|\bdac\b|patch cord|\bcable\b|mount|bracket", t) and len(descs) <= 2:
        return "accessory"                                  # a lone optic / cable / bracket line                                   # "Aruba Central … Subscription", "DNA … Term license"
    hw = {k: v for k, v in scores.items() if k not in ("software", "service")}
    best_hw = max(hw, key=hw.get)
    if hw[best_hw] >= 3:
        if best_hw == "network" and scores["san_switch"] >= hw[best_hw] - 2 and scores["san_switch"] >= 5:
            return "san_switch"
        return best_hw
    best = max(scores, key=scores.get)
    return best if scores[best] >= 2 else "other"


def _int(d: Optional[Decimal]) -> Optional[int]:
    return int(d) if d is not None and d == int(d) else None


def config_qty(c: Config) -> int:
    if c.qty:
        return c.qty
    if c.model_line is not None:
        q = _int(c.items[c.model_line].qty)
        if q and q > 0:
            return q
    return 1


def guess_qty_mode(c: Config, n: int) -> str:
    if c.price_basis == "per_unit" or n <= 1:
        return "per_unit"
    comps = [_int(i.qty) for k, i in enumerate(c.items) if k != c.model_line and i.qty is not None]
    comps = [q for q in comps if q]
    return "total" if comps and all(q % n == 0 for q in comps) and any(q > n for q in comps) else "per_unit"


def _per_unit(q: Optional[Decimal], n: int, mode: str) -> Decimal:
    q = q if q is not None else Decimal(1)
    return q / n if mode == "total" and n > 1 else q


def _fmt(d: Decimal) -> str:
    return str(int(d)) if d == int(d) else f"{d.normalize()}"


_GB = re.compile(r"(\d+(?:[.,]\d+)?)\s*(tb|gb)\b", re.IGNORECASE)
_PORTS = re.compile(r"\b(single|dual|quad|one|two|four|\d{1,2})[\s-]*(?:port|p)\b", re.IGNORECASE)
_SPEED = re.compile(r"(?<![\d.])(\d{1,3}(?:\.5)?(?:/\d{1,3}(?:\.5)?)*)\s*(?:gbe|ge|gb(?:/s)?|gbps|g)\b(?!\s*(?:ram|memory|dimm|byte))", re.IGNORECASE)
_WORDNUM = {"single": 1, "one": 1, "dual": 2, "two": 2, "quad": 4, "four": 4}


def _ports_per_card(desc: str) -> Optional[int]:
    m = _PORTS.search(desc)
    if not m:
        return None
    w = m.group(1).lower()
    return _WORDNUM.get(w) or int(w)


def _size_gb(desc: str) -> Optional[Decimal]:
    m = _GB.search(desc)
    if not m:
        return None
    v = Decimal(m.group(1).replace(",", "."))
    return v * 1000 if m.group(2).lower() == "tb" else v


def _join(parts: List[str]) -> str:
    return "; ".join(p for p in parts if p)


def summarise(cat: str, c: Config, n: int, mode: str) -> List[List[str]]:
    """Ordered [label, value] rows describing ONE unit of the configuration."""
    comps = [(i, _per_unit(i.qty, n, mode)) for k, i in enumerate(c.items) if k != c.model_line and not is_empty(i)]
    model = c.items[c.model_line].desc if c.model_line is not None else c.title
    rows: List[List[str]] = [["Model", _text(model, 200)], ["Số lượng", f"{n}"]]
    low = lambda i: _norm(f"{i.desc} {i.pn}")  # noqa: E731  (Cisco service SKUs are recognised by their PN: CON-SNT-…)

    def pick(pattern: str, exclude: str = "", kind: Optional[str] = None) -> List[Tuple[Item, Decimal]]:
        """Lines of one component type: by the vendor's module name when the file has one (Dell
        configurator), otherwise by matching the description."""
        rx, ex = re.compile(pattern), re.compile(exclude) if exclude else None
        out = []
        for i, q in comps:
            mk = _module_kind(i.module) if i.module else None
            if kind and mk:
                if mk == kind:
                    out.append((i, q))
            elif rx.search(low(i)) and not (ex and ex.search(low(i))):
                out.append((i, q))
        return out

    def listing(found, unit_fn=None) -> str:
        merged: Dict[str, Decimal] = {}
        for i, q in found:                                   # "Processor" + "Additional Processor" → 2 × …
            key = _text(i.desc, 90)
            merged[key] = merged.get(key, Decimal(0)) + q
        return _join([f"{_fmt(q)} × {d}" for d, q in merged.items()][:4])

    warranty = pick(r"prosupport|warranty|bao hanh|care pack|smartnet|smart net|\bsnt|con snt|con sso|con ecmu|con 3|success track|tech care|"
                    r"foundation care|proactive care|\bsvc\b|support|maintenance|bao tri", r"license|licence|subscription|bundle", "warranty")
    lic = pick(r"license|licence|subscription|advantage|essentials|advanced|basic|premier|enterprise|foundation|bundle")

    if cat == "server":
        cpu = pick(r"xeon|epyc|processor|\bcpu\b", r"heatsink|tan nhiet|fan", "cpu")
        ram = pick(r"(dimm|memory|\bram\b)", r"ssd|hdd|nvme", "ram")
        disk = [(i, q * _m2_count(i.desc)) for i, q in pick(r"ssd|hdd|nvme|\bsas\b|sata|hard drive|\bdisk\b|m 2|boss", r"backplane|cable|carrier|blank|^(?!.*(m 2|boss)).*controller", "disk")]
        nic = pick(r"(ethernet|\bnic\b|ocp|network adapter|\bgbe\b|base-t|sfp)", r"\bfc\b|fibre|hba|cable|transceiver|optic|riser|password", "nic")
        hba = pick(r"\bhba\b|fibre channel|\bfc\b", r"cable|transceiver|optic", "hba")
        psu = pick(r"power supply|\bpsu\b", r"cord|cable", "psu")
        optic = pick(r"sfp|qsfp|transceiver|optic", r"cable", "optic")
        ram_gb = sum((_size_gb(i.desc) or 0) * q for i, q in ram)
        disk_txt = _join([f"{_fmt(q)} × {(_GB.search(i.desc).group(0) if _GB.search(i.desc) else '')} {_disk_kind(i.desc)}".strip() for i, q in disk][:4])
        nic_ports = [(i, q, _ports_per_card(i.desc), _speed(i.desc)) for i, q in nic]
        hba_ports = [(i, q, _ports_per_card(i.desc), _speed(i.desc)) for i, q in hba]
        rows += [["CPU", listing(cpu)],
                 ["RAM", f"{_fmt(ram_gb)} GB ({listing(ram)})" if ram_gb else listing(ram)],
                 ["Ổ đĩa", disk_txt],
                 ["Cổng mạng", _ports_txt(nic_ports)],
                 ["Cổng HBA (FC)", _ports_txt(hba_ports)],
                 ["Module quang", listing(optic)],
                 ["Nguồn", listing(psu)]]
    elif cat in ("network", "san_switch"):
        ports = _port_groups(" ".join([model] + [i.desc for i, _ in comps]))
        sfp = pick(r"sfp|qsfp|transceiver|optic|module quang")
        rows += [["Cổng", ports or listing(pick(r"port"))],
                 ["Tốc độ", _join(sorted({s for s in (_speed(i.desc, cat == "san_switch") for i in c.items) if s}, key=_speed_key))],
                 ["Module quang (SFP)", _join([f"{_fmt(q)} × {_text(i.desc, 60)}" for i, q in sfp][:4])],
                 ["License", _license_tier(" ".join(i.desc for i in c.items)) or listing(lic)],
                 ["Nguồn", listing(pick(r"power supply|\bpsu\b", r"cord|cable"))]]
    elif cat == "firewall":
        alltext = " ".join(i.desc for i in c.items)
        rows += [["Throughput", _join(sorted(set(re.findall(r"\d+(?:[.,]\d+)?\s*gbps", alltext, re.IGNORECASE)))[:3])],
                 ["Cổng", _port_groups(alltext)],
                 ["Bộ license / subscription", listing(lic)],
                 ["Triển khai", "Cặp HA (2 thiết bị)" if n == 2 else (f"{n} thiết bị" if n > 1 else "1 thiết bị")]]
    elif cat == "storage":
        drives = pick(r"ssd|hdd|nvme|drive|\bdisk\b", r"enclosure|controller|cable|blank|carrier")
        raw_gb = sum((_size_gb(i.desc) or 0) * q for i, q in drives)
        ctrl = pick(r"controller|bo dieu khien|node")
        ports = pick(r"\bfc\b|fibre|iscsi|\bgbe\b|nvme/tcp|io module|mezz", r"cable|transceiver")
        rows += [["Bộ điều khiển", listing(ctrl)],
                 ["Ổ đĩa", _join([f"{_fmt(q)} × {(_GB.search(i.desc).group(0) if _GB.search(i.desc) else _text(i.desc, 40))} {_disk_kind(i.desc)}".strip() for i, q in drives][:4])],
                 ["Dung lượng thô", f"{_fmt((raw_gb / 1000).quantize(Decimal('0.01')))} TB" if raw_gb else ""],
                 ["Cổng kết nối host", listing(ports)],
                 ["Khay mở rộng", listing(pick(r"enclosure|expansion|dae|shelf"))],
                 ["Phần mềm / license", listing(lic)]]
    elif cat == "load_balancer":
        alltext = " ".join(i.desc for i in c.items)
        rows += [["Throughput", _join(sorted(set(re.findall(r"\d+(?:[.,]\d+)?\s*gbps", alltext, re.IGNORECASE)))[:3])],
                 ["Cổng", _port_groups(alltext)],
                 ["License / module", _license_tier(alltext) or listing(lic)],
                 ["Triển khai", "Cặp HA (2 thiết bị)" if n == 2 else (f"{n} thiết bị" if n > 1 else "1 thiết bị")]]
    else:
        if cat in ("software", "service") and c.model_line is not None:      # the first line is a product too
            comps = [(c.items[c.model_line], _per_unit(c.items[c.model_line].qty, n, mode))] + comps
            rows[0][1] = _text(c.title, 200)
        main = [(i, q) for i, q in comps if not (i.module and _module_kind(i.module) == "misc") and not re.match(r"(for |subscription duration)", _norm(i.desc))]
        rows += [["Thành phần chính", listing(main[:5]) if main else ""]]
    if cat not in ("service", "software"):
        rows.append(["Bảo hành / hỗ trợ", listing(warranty)])
    return [r for r in rows if r[1]]


_EMPTY = re.compile(r"^(no|none|not included|included:|without|decline)\b|^included:|factory integrat|^(hpe )?integration of|^localization", re.IGNORECASE)


def is_empty(i: Item) -> bool:
    """Placeholder lines with no cost: configurator "No Operating System" / "NONE", HPE "#0D1 Factory
    Integrated" or localization codes."""
    zero = i.total is None or abs(i.total) <= Decimal("0.05")
    return zero and (bool(_EMPTY.search(i.desc.strip())) or bool(re.match(r"^#?0d1$|^ab[a-z]$", i.pn.strip(), re.IGNORECASE)))


_MODULE_KINDS = [("optic", r"optic|transceiver|sfp module"), ("misc", r"ship|regulatory|eccn|password|bezel|rail|kvm|label|media kit|country"),
                 ("cpu", r"processor"), ("ram", r"memory capacity|^memory$"), ("disk", r"hard drive|boot optimized|^drives?$|storage drives"),
                 ("hba", r"fibre channel|\bhba\b|host bus"), ("nic", r"network adapter|network card|\blom\b|ocp 3 0 network"),
                 ("psu", r"power supply"), ("warranty", r"services? .*support|extended service|hardware support|prosupport|warranty")]


def _module_kind(module: str) -> Optional[str]:
    m = _norm(module)
    return next((k for k, rx in _MODULE_KINDS if re.search(rx, m)), None)


def _m2_count(desc: str) -> int:
    m = re.search(r"(\d+)\s*(?:x\s*)?m\.2", desc, re.IGNORECASE)
    return int(m.group(1)) if m else 1


def _speed_key(s: str) -> float:
    return float(s.split("/")[0].rstrip("GbEFC "))


def _disk_kind(desc: str) -> str:
    d = desc.lower()
    if "m.2" in d or "boss" in d:
        return "M.2 (BOSS)" if "boss" in d else "M.2"
    kind = "NVMe" if "nvme" in d else "SSD" if "ssd" in d else "HDD" if any(x in d for x in ("hdd", "7.2k", "10k", "15k", "nl-sas", "nl sas")) else ""
    bus = "SAS" if re.search(r"\bsas\b", d) else "SATA" if "sata" in d else ""
    return " ".join(x for x in (kind, bus) if x)


def _speed(desc: str, fc: bool = False) -> Optional[str]:
    m = _SPEED.search(desc)
    if not m:
        return None
    s = m.group(1)
    if not any(float(x) in (1, 2.5, 5, 8, 10, 16, 25, 32, 40, 50, 64, 100, 128, 200, 400) for x in s.split("/")):
        return None
    fc = fc or re.search(r"\bfc\b|fibre|hba|san switch|brocade|mds", desc, re.IGNORECASE)
    return f"{s}Gb FC" if fc else f"{s}GbE"


def _ports_txt(found) -> str:
    out = []
    for i, q, ports, speed in found:
        if ports:
            out.append(f"{_fmt(q * ports)} × {speed or 'cổng'} ({_fmt(q)} card {ports} cổng)")
        else:
            out.append(f"{_fmt(q)} × {_text(i.desc, 70)}")
    return _join(out[:4])


_PORT_GROUP = re.compile(r"\b(\d{1,3})\s*[x×]\s*(\d{1,3}(?:/\d{1,3})*\s*(?:gbe|ge|gb|g|m)\b(?:\s*(?:sfp28|sfp\+|sfp|qsfp28|qsfp\+|qsfp-dd|qsfp|rj-?45|base-t))?)", re.IGNORECASE)
_PORT_GROUP2 = re.compile(r"\b(\d{1,3})(?:[\s-]*port[s]?|p)\b(?:\s*(\d{1,3}(?:/\d{1,3})*\s*g\w*))?", re.IGNORECASE)


def _port_groups(text: str) -> str:
    seen = []
    for a, b in _PORT_GROUP.findall(text):
        g = f"{a} × {' '.join(b.split())}"
        if g not in seen:
            seen.append(g)
    if True:
        for a, b in _PORT_GROUP2.findall(text):
            g = f"{a} cổng" + (f" {b}" if b else "")
            if g not in seen:
                seen.append(g)
    return _join(seen[:5])


_TIERS = ["dna premier", "dna advantage", "dna essentials", "network advantage", "network essentials", "advanced", "advantage",
          "essentials", "enterprise", "premier", "premium", "foundation", "standard", "basic", "best", "better", "good", "ltm"]


def _license_tier(text: str) -> str:
    t = _norm(text)
    for tier in _TIERS:
        if re.search(r"\b" + re.escape(tier) + r"\b", t):
            return " ".join(w.upper() if w in ("dna", "ltm") else w.title() for w in tier.split())
    return ""


def config_out(c: Config, idx: int, category: Optional[str] = None, qty_mode: Optional[str] = None) -> Dict[str, Any]:
    n = config_qty(c)
    cat = category or classify(c.title, [i.desc for i in c.items])
    mode = qty_mode or c.qty_mode or guess_qty_mode(c, n)
    total = sum((i.total for i in c.items if i.total is not None), Decimal(0))
    if c.price_basis == "per_unit":
        total = total * n
    return {"id": idx, "title": c.title, "category": cat, "qty": n, "qtyMode": mode, "priceBasis": c.price_basis,
            "summary": summarise(cat, c, n, mode) + price_rows(c, n, cat),
            "items": [{"pn": i.pn, "desc": i.desc, "module": i.module, "unit": i.unit, "qty": _num(i.qty), "price": _num(i.price),
                       "total": _num(i.total), "isModel": k == c.model_line, "empty": is_empty(i)} for k, i in enumerate(c.items)],
            "total": _num(total)}


def money_text(d: Optional[Decimal]) -> str:
    if d is None:
        return ""
    s = f"{d:,.2f}" if d != int(d) else f"{int(d):,}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")   # 1.234.567,89 (Vietnamese)


def price_rows(c: Config, n: int, cat: str = "") -> List[List[str]]:
    """Price lines of the summary box: one unit and the whole configuration (licences / services:
    the total only — a "unit" means nothing for a bundle of subscriptions)."""
    lines = [i.total for i in c.items if i.total is not None]
    if not lines:
        return []
    total = sum(lines, Decimal(0))
    if cat in ("software", "service") and c.price_basis == "total":
        return [["Thành tiền", money_text(total.quantize(Decimal("0.01")))]]
    if c.price_basis == "per_unit":
        unit, whole = total, total * n
    else:
        unit, whole = (total / n if n > 1 else total), total
    rows = [["Giá 1 thiết bị", money_text(unit.quantize(Decimal("0.01")))]]
    if n > 1:
        rows.append([f"Thành tiền ({n} thiết bị)", money_text(whole.quantize(Decimal("0.01")))])
    if c.unit_price is not None:
        rows.append(["Giá niêm yết 1 thiết bị (file gốc)", money_text(c.unit_price)])
    return rows


def _num(d: Optional[Decimal]) -> Optional[float]:
    if d is None:
        return None
    return int(d) if d == int(d) else float(d)


def resummarise(cfg: Dict[str, Any]) -> List[List[str]]:
    """Recompute the summary of an edited configuration (category / qty mode changed in the UI)."""
    c = Config(title=str(cfg.get("title") or "")[:200])
    for k, it in enumerate(cfg.get("items") or []):
        c.items.append(Item(pn=str(it.get("pn") or "")[:80], desc=str(it.get("desc") or "")[:1000], qty=parse_number(it.get("qty")),
                            price=parse_number(it.get("price")), total=parse_number(it.get("total")), module=str(it.get("module") or "")[:120]))
        if it.get("isModel") and c.model_line is None:
            c.model_line = k
    c.price_basis = "per_unit" if cfg.get("priceBasis") == "per_unit" else "total"
    n = int(cfg.get("qty") or config_qty(c))
    return summarise(cfg.get("category") if cfg.get("category") in CATEGORY_LABELS else "other", c, max(1, n),
                     cfg.get("qtyMode") if cfg.get("qtyMode") in ("per_unit", "total") else "per_unit") + price_rows(c, max(1, n), cfg.get("category") or "")
