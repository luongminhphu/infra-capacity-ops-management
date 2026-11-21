"""Read the resource table of a ServiceDesk Plus request body (ICO v2 G7b).

html.parser from the standard library only (nothing is fetched: no images, no CSS, no entities from outside). Tables become
grids (colspan / rowspan expanded), labels are matched with shared.text.norm_text against the built-in labels + the
itsm_label_map rows, then:
  * horizontal table — a header row with ≥ 2 known labels, one VM per following row;
  * vertical table   — rows "label | value", one VM for the whole table.
Sizes in GB ("8 GB", "1,5 TB" → 1536, "512 MB" → 0,5). confidence = how complete the lines are (vCPU and RAM weigh most);
below CONFIDENCE_OK the request goes to the AI fallback / the review queue."""
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

from ..shared.text import norm_text, parse_number

CONFIDENCE_OK = 0.7
FIELDS = ("vm_name", "env", "os", "vcpu", "ram_gb", "disk_gb", "qty", "project")
NUMBER_FIELDS = ("vcpu", "ram_gb", "disk_gb", "qty")
# Built-in labels (normalised). itsm_label_map rows add to / override them; field "ignore" drops a column on purpose.
DEFAULT_LABELS: Dict[str, str] = {
    **dict.fromkeys(("ten may", "ten may ao", "ten vm", "hostname", "host name", "vm name", "vm", "may ao", "server name"), "vm_name"),
    **dict.fromkeys(("vcpu", "cpu", "so core", "core", "so cpu", "so vcpu", "cores"), "vcpu"),
    **dict.fromkeys(("ram", "memory", "bo nho", "ram gb", "memory gb", "bo nho ram"), "ram_gb"),
    **dict.fromkeys(("o cung", "disk", "dung luong", "dung luong o cung", "disk gb", "o dia", "storage", "hdd"), "disk_gb"),
    **dict.fromkeys(("so luong", "sl", "qty", "quantity"), "qty"),
    **dict.fromkeys(("moi truong", "env", "environment"), "env"),
    **dict.fromkeys(("hdh", "os", "he dieu hanh", "operating system"), "os"),
    **dict.fromkeys(("du an", "project", "ten du an"), "project"),
}
_UNITS = ("gb", "tb", "mb", "gib", "tib")


class _Tables(HTMLParser):
    """Cells of every table (outermost), with colspan / rowspan; text outside tables kept for the AI fallback."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: List[List[List[Tuple[str, int, int]]]] = []
        self.depth = 0
        self.skip = 0
        self.cell: Optional[List[str]] = None
        self.span = (1, 1)
        self.text: List[str] = []
        self.heads: List[str] = []          # per table: the text right before it + its caption ("As-is", "To-be"…)
        self.pending: List[str] = []
        self.caption = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag == "caption" and self.depth == 1:
            self.caption = True
        elif tag == "table":
            self.depth += 1
            if self.depth == 1:
                self.tables.append([])
                self.heads.append(" ".join("".join(self.pending).split())[-160:])
                self.pending = []
        elif tag == "tr" and self.depth == 1:
            self.tables[-1].append([])
        elif tag in ("td", "th") and self.depth == 1 and self.tables and self.tables[-1]:
            a = dict(attrs)
            num = lambda k: max(1, min(int(a[k]), 50)) if str(a.get(k) or "").isdigit() else 1      # noqa: E731
            self.cell, self.span = [], (num("colspan"), num("rowspan"))
        elif tag in ("br", "p", "div", "li") and self.depth == 0:
            self.text.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag == "caption":
            self.caption = False
        elif tag == "table":
            self.depth = max(0, self.depth - 1)
        elif tag in ("td", "th") and self.depth == 1 and self.cell is not None:
            self.tables[-1][-1].append((" ".join("".join(self.cell).split()), *self.span))
            self.cell = None

    def handle_data(self, data):
        if self.skip:
            return
        if self.cell is not None:
            self.cell.append(data)
        elif self.caption and self.depth == 1:
            self.heads[-1] += " " + data
        elif self.depth == 0:
            self.text.append(data)
            self.pending.append(data)


def _grid(rows: List[List[Tuple[str, int, int]]]) -> List[List[str]]:
    """Expand colspan / rowspan: a merged cell's text is repeated in every slot it covers."""
    out: List[List[str]] = []
    carry: Dict[Tuple[int, int], str] = {}
    for r, cells in enumerate(rows):
        row: List[str] = []
        c = 0
        for text, cs, rs in cells:
            while (r, c) in carry:
                row.append(carry.pop((r, c)))
                c += 1
            for k in range(cs):
                row.append(text)
                for d in range(1, rs):
                    carry[(r + d, c + k)] = text
            c += cs
        while (r, c) in carry:
            row.append(carry.pop((r, c)))
            c += 1
        out.append(row)
    return out


def field_of(label: str, labels: Dict[str, str]) -> Optional[str]:
    n = norm_text(label)
    if not n:
        return None
    if n in labels:
        return labels[n]
    words = [w for w in n.split() if w not in _UNITS]                # "RAM (GB)", "Disk GB" → "ram", "disk"
    return labels.get(" ".join(words)) if words else None


def size_gb(text: str) -> Optional[float]:
    v = parse_number(text)
    if v is None:
        return None
    t = norm_text(text)
    factor = 1024 if " tb" in f" {t}" or t.endswith("tb") or "tib" in t else (1 / 1024 if " mb" in f" {t}" or t.endswith("mb") else 1)
    return round(float(v) * factor, 3)


def _value(field: str, text: str) -> Any:
    text = (text or "").strip()
    if not text or text in ("-", "—", "n/a", "N/A"):
        return None
    if field in ("ram_gb", "disk_gb"):
        return size_gb(text)
    if field in ("vcpu", "qty"):
        v = parse_number(text)
        return (int(v) if field == "qty" else float(v)) if v is not None and v > 0 else None
    return text[:255]


def _line(cells: Dict[str, str]) -> Dict[str, Any]:
    line: Dict[str, Any] = {f: _value(f, cells.get(f, "")) for f in FIELDS}
    line.update(vcpu_before=None, ram_gb_before=None, disk_gb_before=None)
    line["qty"] = line["qty"] or 1
    line["raw"] = {k: v for k, v in cells.items() if v}
    return line


def _score(line: Dict[str, Any], has_disk: bool) -> float:
    other = line["disk_gb"] is not None if has_disk else line["vm_name"] is not None or line["os"] is not None
    return 0.4 * (line["vcpu"] is not None) + 0.4 * (line["ram_gb"] is not None) + 0.2 * other


AS_IS = ("as is", "asis", "hien tai", "hien trang", "cau hinh cu", "truoc nang cap", "truoc khi")
TO_BE = ("to be", "tobe", "de xuat", "sau nang cap", "cau hinh moi", "mong muon", "sau khi")
SIZES = ("vcpu", "ram_gb", "disk_gb")


def split_role(label: str) -> Tuple[Optional[str], str]:
    """("as_is" | "to_be" | None, the label without the role words): "vCPU (As-is)" → ("as_is", "vcpu")."""
    n = f" {norm_text(label)} "
    r = role(label)
    if r:
        for w in AS_IS + TO_BE:
            n = n.replace(f" {w} ", " ")
    return r, " ".join(n.split())


def column(label: str, labels: Dict[str, str]) -> Optional[str]:
    """Field of a horizontal-table column; an As-is size column maps to its *_before field."""
    r, rest = split_role(label)
    f = field_of(rest if r else label, labels)
    if r == "as_is" and f in SIZES:
        return f + "_before"
    return f


def role(text: str) -> Optional[str]:
    """as_is / to_be from a heading or a column title ("Cấu hình hiện tại (As-is)", "To-be"); None when neither or both."""
    n = f" {norm_text(text)} "
    a, t = any(f" {w} " in n for w in AS_IS), any(f" {w} " in n for w in TO_BE)
    return "as_is" if a and not t else "to_be" if t and not a else None


def _table(g: List[List[str]], labels: Dict[str, str]) -> Tuple[List[Dict[str, Any]], List[str], bool]:
    """(lines, unknown labels, has a disk column) of one grid. A vertical table "Thông số | As-is | To-be" gives one line
    with the As-is values in *_before."""
    known = lambda r: sum(1 for c in r if column(c, labels) not in (None, "ignore"))      # noqa: E731
    unknown: List[str] = []
    if len(g[0]) == 3 and role(g[0][-2]) == "as_is" and role(g[0][-1]) == "to_be":        # combined As-is / To-be table
        before, after = {}, {}
        for r in g[1:]:
            f = field_of(r[0], labels)
            if f is None and r[0].strip():
                unknown.append(r[0])
            elif f not in (None, "ignore") and len(r) >= 3:
                before.setdefault(f, r[-2])
                after.setdefault(f, r[-1])
        if not after:
            return [], unknown, False
        line = _line({**before, **after})
        for f in SIZES:
            line[f + "_before"] = _value(f, before.get(f, ""))
        return [line], unknown, "disk_gb" in after
    head = next((i for i, r in enumerate(g) if known(r) >= 2), None)
    if head is None and len(g) > 1 and len(g[0]) > 2 and known(g[0]) >= 1:
        head = 0          # a header row with mostly unknown labels: read what is known, report the rest for review
    if head is not None and len(g) > head + 1 and len(g[head]) > 2:          # horizontal: one VM per row
        cols = {i: column(c, labels) for i, c in enumerate(g[head])}
        unknown += [c for i, c in enumerate(g[head]) if cols[i] is None and c.strip()]
        lines = []
        for r in g[head + 1:]:
            cells = {f: r[i] for i, f in cols.items() if f not in (None, "ignore") and i < len(r)}
            if any(v.strip() for v in cells.values()):
                line = _line({f: v for f, v in cells.items() if not f.endswith("_before")})
                for f in SIZES:                 # "vCPU (As-is) | vCPU (To-be)" columns in one table
                    if f + "_before" in cells:
                        line[f + "_before"] = _value(f, cells[f + "_before"])
                lines.append(line)
        return lines, unknown, "disk_gb" in cols.values()
    pairs = {}                                                               # vertical: "label | value" rows
    for r in g:
        if len(r) >= 2:
            f = field_of(r[0], labels)
            if f is None and r[0].strip():
                unknown.append(r[0])
            elif f != "ignore":
                pairs.setdefault(f, r[1])
    if len(pairs) >= 2:
        return [_line(pairs)], unknown, "disk_gb" in pairs
    return [], unknown, False


def _pair(as_is: List[Dict[str, Any]], to_be: List[Dict[str, Any]]) -> None:
    """Put the As-is sizes on the To-be lines: same VM name, else the same row."""
    by_name = {norm_text(x["vm_name"] or ""): x for x in as_is if x.get("vm_name")}
    for i, ln in enumerate(to_be):
        old = by_name.get(norm_text(ln["vm_name"] or "")) if ln.get("vm_name") else None
        old = old or (as_is[i] if i < len(as_is) else None)
        if old:
            for f in SIZES:
                ln[f + "_before"] = old.get(f)
            for f in ("vm_name", "env", "os", "project"):
                ln[f] = ln[f] or old.get(f)


def parse_html(html: Optional[str], labels: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """{"lines": [...], "confidence": 0..1, "unknownLabels": [...], "tables": n, "text": plain text for the AI fallback}."""
    labels = {**DEFAULT_LABELS, **(labels or {})}
    p = _Tables()
    try:
        p.feed(html or "")
        p.close()
    except Exception:  # noqa: BLE001 — html.parser is lenient; a broken body is a failed parse, not a crash
        return {"lines": [], "confidence": 0.0, "unknownLabels": [], "tables": 0, "text": ""}
    lines: List[Dict[str, Any]] = []
    unknown: List[str] = []
    has_disk = False
    as_is: List[Dict[str, Any]] = []
    to_be: List[Dict[str, Any]] = []
    for table, head in zip(p.tables, p.heads):
        g = [r for r in _grid(table) if any(c.strip() for c in r)]
        if not g:
            continue
        r = role(head)
        if r is None and len({c for c in g[0] if c.strip()}) == 1 and len(g) > 1:
            r = role(g[0][0] or next(c for c in g[0] if c.strip()))
            if r:
                g = g[1:]                       # the merged "As-is" / "To-be" title row is not data
        got, unk, disk = _table(g, labels)
        unknown += unk
        has_disk = has_disk or disk
        (as_is if r == "as_is" else to_be if r == "to_be" else lines).extend(got)
    if to_be:                                   # VM reconfiguration: the To-be lines carry the As-is sizes
        _pair(as_is, to_be)
        lines += to_be
    elif as_is:                                 # only the current configuration was given: nothing to change → review
        lines += as_is
    conf = round(sum(_score(x, has_disk) for x in lines) / len(lines), 2) if lines else 0.0
    text = " ".join("".join(p.text).split()) + "\n" + "\n".join(" | ".join(r) for t in p.tables for r in _grid(t))
    return {"lines": lines, "confidence": conf, "unknownLabels": list(dict.fromkeys(unknown))[:20], "tables": len(p.tables),
            "text": text.strip()[:8000]}
