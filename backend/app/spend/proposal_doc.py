"""Proposal (tờ trình) templates: read the structure of an uploaded .docx and fill a COPY of it.

Structure ("outline"), read from the template itself — no placeholder convention required:
* preamble: everything before the first section heading — the "Về việc / Đề xuất bởi /
  Trình đến / Thời gian" table, the "Kính gửi" line and the opening paragraph(s);
* section heading: a numbered paragraph whose numbering format is not a bullet
  (e.g. "1. Mục đích triển khai"), or a paragraph starting with "Phụ lục …" (appendix);
* section body: what follows a heading up to the next one (paragraphs, bullets, tables).

Section kinds:
* approval — contains the signature table (rows "Đề xuất / Kiểm soát / Tham vấn / Phê duyệt"):
  kept as is, the people are filled from Settings → recipients;
* locked   — conflict-of-interest declaration (policy text): kept verbatim;
* opinion  — "Ý kiến …" (written by another department): replaced by blank dotted lines;
* editable — everything else: written from the data (AI) or left as "[Cần soạn …]".

Filling keeps the template's styles, numbering, header / footer and logo: new paragraphs,
bullets and tables are deep copies of an example element found in the template.
"""
import copy
import datetime as dt
import io
import re
from typing import Any, Dict, List, Optional, Sequence

import docx
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

ROLE_LABELS = {"de_xuat": "Đề xuất", "kiem_soat": "Kiểm soát", "tham_van": "Tham vấn", "phe_duyet": "Phê duyệt"}
_ROLE_OF_LABEL = {v.lower(): k for k, v in ROLE_LABELS.items()}
_APPENDIX = re.compile(r"^\s*ph[ụu]\s*l[ụu]c\b", re.IGNORECASE)
_LOCKED = re.compile(r"xung đột lợi ích|xđli", re.IGNORECASE)
_OPINION = re.compile(r"^\s*ý kiến\b", re.IGNORECASE)
_COST = re.compile(r"chi phí|dự toán|tổng hợp", re.IGNORECASE)
HEADER_LABELS = {"về việc": "subject", "đề xuất bởi": "department", "trình đến": "to", "thời gian": "date"}
MAX_SAMPLE = 400


class TemplateError(ValueError):
    pass


# ---------------------------------------------------------------- numbering helpers
class _Numbering:
    """numId -> numbering format of level n, reading the numbering part once."""

    def __init__(self, document):
        self.fmt: Dict[tuple, str] = {}
        try:
            el = document.part.numbering_part.element
        except (KeyError, NotImplementedError, AttributeError):
            return
        abstract = {}
        for a in el.findall(qn("w:abstractNum")):
            for lvl in a.findall(qn("w:lvl")):
                f = lvl.find(qn("w:numFmt"))
                abstract[(a.get(qn("w:abstractNumId")), lvl.get(qn("w:ilvl")))] = f.get(qn("w:val")) if f is not None else None
        for n in el.findall(qn("w:num")):
            aid = n.find(qn("w:abstractNumId"))
            if aid is None:
                continue
            for (a, il), f in abstract.items():
                if a == aid.get(qn("w:val")):
                    self.fmt[(n.get(qn("w:numId")), il)] = f


def _num_pr(p: Paragraph):
    """numPr of the paragraph, or of its style chain (numbering defined on the style)."""
    pPr = p._p.pPr
    if pPr is not None and pPr.numPr is not None and pPr.numPr.numId is not None:
        return pPr.numPr
    style = p.style
    while style is not None:
        spPr = style.element.pPr
        if spPr is not None and spPr.numPr is not None and spPr.numPr.numId is not None:
            return spPr.numPr
        style = style.base_style
    return None


def _num_format(p: Paragraph, nb: _Numbering) -> Optional[str]:
    numPr = _num_pr(p)
    if numPr is None:
        return None
    num_id = str(numPr.numId.val)
    if num_id == "0":
        return None
    ilvl = str(numPr.ilvl.val) if numPr.ilvl is not None else "0"
    return nb.fmt.get((num_id, ilvl), "decimal")


def _is_heading(p: Paragraph, nb: _Numbering) -> Optional[Dict[str, Any]]:
    text = p.text.strip()
    if not text or len(text) > 160:
        return None
    if _APPENDIX.match(text):
        return {"appendixStart": True}
    fmt = _num_format(p, nb)
    if fmt and fmt not in ("bullet", "none"):
        return {"appendixStart": False}
    return None


def _is_bullet(p: Paragraph, nb: _Numbering) -> bool:
    return _num_format(p, nb) == "bullet"


def _approval_table(t: Table) -> bool:
    roles = {r.cells[0].text.strip().lower() for r in t.rows if r.cells}
    return len(roles & set(_ROLE_OF_LABEL)) >= 2


def _header_table(t: Table) -> bool:
    labels = {r.cells[0].text.strip().rstrip(":").lower() for r in t.rows if r.cells}
    return len(labels & set(HEADER_LABELS)) >= 2


# ---------------------------------------------------------------- outline
def _blocks(document):
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield "p", Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield "tbl", Table(child, document)


def _sections(document) -> Dict[str, Any]:
    """Split the body into preamble + sections (element references, used by both outline and fill)."""
    nb = _Numbering(document)
    preamble: List[Any] = []
    sections: List[Dict[str, Any]] = []
    in_appendix = False
    for kind, obj in _blocks(document):
        h = _is_heading(obj, nb) if kind == "p" else None
        if h is not None:
            if h["appendixStart"]:
                in_appendix = True
            sections.append({"heading": obj, "body": [], "appendix": in_appendix, "appendixTitle": h["appendixStart"]})
        elif sections:
            sections[-1]["body"].append((kind, obj))
        else:
            preamble.append((kind, obj))
    for s in sections:
        tables = [o for k, o in s["body"] if k == "tbl"]
        text = s["heading"].text.strip()
        if any(_approval_table(t) for t in tables):
            s["kind"] = "approval"
        elif _LOCKED.search(text):
            s["kind"] = "locked"
        elif _OPINION.match(text):
            s["kind"] = "opinion"
        elif s["appendixTitle"] and not any(k == "p" and o.text.strip() for k, o in s["body"]) and not tables:
            s["kind"] = "title"          # "Phụ lục A – …" line directly followed by its numbered subsections
        else:
            s["kind"] = "editable"
    return {"preamble": preamble, "sections": sections, "numbering": nb}


def _sample(body) -> str:
    parts = []
    for k, o in body:
        if k == "p" and o.text.strip():
            parts.append(o.text.strip())
        elif k == "tbl":
            parts.append("[Bảng " + " | ".join(c.text.strip()[:30] for c in o.rows[0].cells) + "]")
        if sum(len(x) for x in parts) > MAX_SAMPLE:
            break
    return " ".join(parts)[:MAX_SAMPLE]


def outline(data: bytes) -> Dict[str, Any]:
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 — python-docx raises several types on a broken package
        raise TemplateError("Không đọc được file mẫu .docx.") from exc
    parts = _sections(document)
    if not parts["sections"]:
        raise TemplateError("Không nhận ra mục nào: mẫu cần các tiêu đề mục đánh số (1., 2., …) như tờ trình hiện có.")
    header = next((o for k, o in parts["preamble"] if k == "tbl" and _header_table(o)), None)
    roles = []
    for s in parts["sections"]:
        if s["kind"] == "approval":
            t = next(o for k, o in s["body"] if k == "tbl" and _approval_table(o))
            roles = [_ROLE_OF_LABEL.get(r.cells[0].text.strip().lower()) for r in t.rows if r.cells]
            roles = [r for r in roles if r]
    return {
        "hasHeaderTable": header is not None,
        "hasGreeting": any(k == "p" and o.text.strip().lower().startswith("kính gửi") for k, o in parts["preamble"]),
        "approvalRoles": roles,
        "sections": [{
            "index": i, "heading": s["heading"].text.strip(), "kind": s["kind"], "appendix": s["appendix"],
            "tables": sum(1 for k, _ in s["body"] if k == "tbl"), "sample": _sample(s["body"]),
        } for i, s in enumerate(parts["sections"])],
    }


# ---------------------------------------------------------------- writing helpers
def set_text(p: Paragraph, text: str) -> None:
    """Replace the text of a paragraph, keeping the formatting of its first run."""
    runs = p.runs
    if not runs:
        p.add_run(text)
        return
    runs[0].text = text
    for r in runs[1:]:
        r._r.getparent().remove(r._r)


def _new_paragraph_after(anchor_el, example: Paragraph, text: str):
    el = copy.deepcopy(example._p)
    anchor_el.addnext(el)
    p = Paragraph(el, example._parent)
    for r in p.runs[1:]:
        r._r.getparent().remove(r._r)
    # Drop hyperlinks / fields copied from the example: only plain runs carry the new text.
    for extra in list(el):
        if extra.tag not in (qn("w:pPr"), qn("w:r")):
            el.remove(extra)
    set_text(p, text)
    return el


def _set_cell(cell, text: str) -> None:
    paras = cell.paragraphs
    set_text(paras[0], text)
    for p in paras[1:]:
        p._p.getparent().remove(p._p)


def _strip_merge(tc) -> None:
    tcPr = tc.tcPr
    if tcPr is not None:
        for tag in ("w:gridSpan", "w:vMerge", "w:hMerge"):
            for e in tcPr.findall(qn(tag)):
                tcPr.remove(e)


def _fit_row(tr, n: int) -> None:
    tcs = tr.findall(qn("w:tc"))
    for tc in tcs:
        _strip_merge(tc)
    while len(tcs) > n:
        tr.remove(tcs[-1])
        tcs = tcs[:-1]
    while len(tcs) < n:
        new = copy.deepcopy(tcs[-1])
        tcs[-1].addnext(new)
        tcs.append(new)


def _new_table_after(anchor_el, example: Table, rows: Sequence[Sequence[str]]):
    """Copy the example table's look: its first row styles the header, its second row the body."""
    el = copy.deepcopy(example._tbl)
    anchor_el.addnext(el)
    trs = el.findall(qn("w:tr"))
    head_tpl = copy.deepcopy(trs[0])
    body_tpl = copy.deepcopy(trs[1] if len(trs) > 1 else trs[0])
    for tr in trs:
        el.remove(tr)
    ncols = max(1, max(len(r) for r in rows))
    grid = el.find(qn("w:tblGrid"))
    if grid is not None:
        cols = grid.findall(qn("w:gridCol"))
        total = sum(int(c.get(qn("w:w")) or 0) for c in cols) or 9000
        for c in cols:
            grid.remove(c)
        for _ in range(ncols):
            gc = copy.deepcopy(cols[0]) if cols else grid.makeelement(qn("w:gridCol"), {})
            gc.set(qn("w:w"), str(total // ncols))
            grid.append(gc)
    else:
        total = 9000
    table = Table(el, example._parent)
    for i, row in enumerate(rows):
        tr = copy.deepcopy(head_tpl if i == 0 else body_tpl)
        _fit_row(tr, ncols)
        for tc in tr.findall(qn("w:tc")):
            tcW = tc.tcPr.find(qn("w:tcW")) if tc.tcPr is not None else None
            if tcW is not None:
                tcW.set(qn("w:w"), str(total // ncols))
                tcW.set(qn("w:type"), "dxa")
        el.append(tr)
    for r, row in zip(table.rows, rows):
        for c, value in zip(r.cells, list(row) + [""] * (ncols - len(row))):
            _set_cell(c, str(value))
    return el


def _blank(p: Paragraph) -> bool:
    if p.text.strip():
        return False
    return not any(el.tag in _OBJECT_TAGS for el in p._p.iter())


_OBJECT_TAGS = {qn("w:drawing"), qn("w:object"), qn("w:pict"), qn("w:fldSimple"),
                "{urn:schemas-microsoft-com:vml}shape", "{http://schemas.openxmlformats.org/markup-compatibility/2006}AlternateContent"}


def _remove(obj) -> None:
    el = obj._p if isinstance(obj, Paragraph) else obj._tbl
    el.getparent().remove(el)


# ---------------------------------------------------------------- fill
def vn_date(d: dt.date) -> str:
    return f"Ngày {d.day:02d} tháng {d.month:02d} năm {d.year}"


def fill(data: bytes, header: Dict[str, Optional[str]], recipients: Sequence[Dict[str, str]],
         content: Optional[Dict[str, Any]], data_table: Sequence[Sequence[str]]) -> bytes:
    """Fill a copy of the template.

    header:      subject, department, date (dd/mm…), to, greeting — None keeps the template text;
    recipients:  [{role, salutation, name, title}] in order — empty keeps the template's people;
    content:     {"intro": str, "sections": {index: [blocks]}} from the AI, or None (no AI):
                 editable sections then get "[Cần soạn …]" and the data table goes into the
                 first cost section;
    data_table:  rows (first = header) of the Opex / Capex lines, used when content is None.
    """
    document = docx.Document(io.BytesIO(data))
    parts = _sections(document)
    nb = parts["numbering"]

    # Preamble: header table, greeting, opening paragraph.
    for k, o in parts["preamble"]:
        if k == "tbl" and _header_table(o):
            for r in o.rows:
                key = HEADER_LABELS.get(r.cells[0].text.strip().rstrip(":").lower())
                if key and header.get(key) and len(r.cells) > 1:
                    _set_cell(r.cells[-1], header[key])
    paras = [o for k, o in parts["preamble"] if k == "p"]
    greet_i = next((i for i, p in enumerate(paras) if p.text.strip().lower().startswith("kính gửi")), None)
    if greet_i is not None and header.get("greeting"):
        set_text(paras[greet_i], header["greeting"])
    intro = [p for p in paras[(greet_i + 1 if greet_i is not None else 0):] if p.text.strip()]
    if greet_i is None:
        intro = [p for p in intro if p.text.strip().upper() != "TỜ TRÌNH"]
    if intro:
        set_text(intro[0], (content or {}).get("intro") or "[Cần soạn: mở đầu — lý do trình, nội dung đề nghị phê duyệt]")
        for p in intro[1:]:
            _remove(p)

    # Example elements for new content (section's own first, else any in the document).
    def examples(body):
        par = next((o for k, o in body if k == "p" and o.text.strip() and not _is_bullet(o, nb)), None)
        bul = next((o for k, o in body if k == "p" and o.text.strip() and _is_bullet(o, nb)), None)
        tbl = next((o for k, o in body if k == "tbl"), None)
        return par, bul, tbl

    doc_par = doc_bul = doc_tbl = None
    for s in parts["sections"]:
        if s["kind"] == "editable":
            par, bul, tbl = examples(s["body"])
            doc_par, doc_bul, doc_tbl = doc_par or par, doc_bul or bul, doc_tbl or tbl
    fallback_par = doc_par or doc_bul or next((p for p in paras if p.text.strip()), None)
    if fallback_par is None:
        raise TemplateError("Mẫu không có đoạn văn nào để làm khuôn định dạng.")

    sections_content = {int(k): v for k, v in ((content or {}).get("sections") or {}).items()}
    cost_target = None
    if content is None and data_table:
        cost_target = next((i for i, s in enumerate(parts["sections"]) if s["kind"] == "editable" and not s["appendix"]
                            and _COST.search(s["heading"].text)), None)
        if cost_target is None:
            cost_target = next((i for i, s in enumerate(parts["sections"]) if s["kind"] == "editable"), None)

    for i, s in enumerate(parts["sections"]):
        if s["kind"] in ("locked", "title"):
            continue
        if s["kind"] == "approval":
            _fill_approval(s, recipients)
            continue
        par, bul, tbl = examples(s["body"])
        par, bul, tbl = par or doc_par or fallback_par, bul or doc_bul or par or fallback_par, tbl or doc_tbl
        # Keep one truly empty paragraph as spacing before the next heading; an empty-looking paragraph
        # that holds a drawing / embedded object (e.g. an Excel table) is old content and goes too.
        keep_tail = [o for k, o in s["body"] if k == "p" and _blank(o)][-1:]
        for k, o in s["body"]:
            if not (keep_tail and o is keep_tail[0]):
                _remove(o)
        anchor = s["heading"]._p
        if s["kind"] == "opinion":
            blocks = [{"type": "paragraph", "text": "…" * 42} for _ in range(4)]
        elif content is None:
            blocks = [{"type": "paragraph", "text": f"[Cần soạn: {s['heading'].text.strip()}]"}]
            if i == cost_target:
                blocks.append({"type": "table", "rows": [list(r) for r in data_table]})
        else:
            blocks = sections_content.get(i) or [{"type": "paragraph", "text": f"[Cần soạn: {s['heading'].text.strip()}]"}]
        for b in blocks:
            if b.get("type") == "table" and b.get("rows"):
                if tbl is None:
                    for row in b["rows"]:
                        anchor = _new_paragraph_after(anchor, par, " | ".join(str(c) for c in row))
                else:
                    anchor = _new_table_after(anchor, tbl, b["rows"])
            elif b.get("type") == "bullet":
                anchor = _new_paragraph_after(anchor, bul, str(b.get("text") or ""))
            elif b.get("text"):
                anchor = _new_paragraph_after(anchor, par, str(b.get("text")))

    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def _fill_approval(section: Dict[str, Any], recipients: Sequence[Dict[str, str]]) -> None:
    if not recipients:
        return
    t = next(o for k, o in section["body"] if k == "tbl" and _approval_table(o))
    queues: Dict[str, List[Dict[str, str]]] = {}
    for r in recipients:
        queues.setdefault(r["role"], []).append(r)
    for row in t.rows:
        role = _ROLE_OF_LABEL.get(row.cells[0].text.strip().lower())
        if not role or not queues.get(role) or len(row.cells) < 2:
            continue
        person = queues[role].pop(0)
        cell = row.cells[1]
        filled = [p for p in cell.paragraphs if p.text.strip()]
        if "họ và tên" in cell.text.lower():                      # "Họ và tên: … / Chức vụ: …"
            _set_cell(cell, f"Họ và tên: {person['name']}")
            p = cell.paragraphs[0]
            _new_paragraph_after(p._p, p, f"Chức vụ: {person['title']}")
        elif len(filled) >= 2:                                     # name and title on two lines
            set_text(filled[0], person["name"])
            set_text(filled[1], person["title"])
            for extra in filled[2:]:
                extra._p.getparent().remove(extra._p)
        else:
            _set_cell(cell, f"{person['name']} / {person['title']}")


def text_of(data: bytes) -> List[str]:
    """Paragraph and table text in reading order (for tests and checks)."""
    document = docx.Document(io.BytesIO(data))
    out = []
    for k, o in _blocks(document):
        if k == "p":
            out.append(o.text)
        else:
            out.extend(" | ".join(c.text for c in r.cells) for r in o.rows)
    return out
