"""Contracts: text of a .docx version, comparison between versions, purge after Final.

Comparison (docs/design/spend-module.md §1.7, GĐ4):
* each version keeps its paragraphs (text only) while its file exists;
* `compare(from, to)` diffs the paragraphs of the two versions and attributes every inserted /
  changed paragraph to the version that introduced it, and every removed paragraph to the
  version that removed it, by walking the intermediate versions one step at a time ("blame").
  The UI colours each change with the party of that version (Legal / Finance / Đối tác).

Purge: once a contract is Final, the files (and stored text) of every version other than the
final one are deleted after `purge_days`, unless an admin cancelled it. Metadata, hash and an
audit row stay. Run by the daily job and idempotent (purged files are skipped).
"""
import datetime as dt
import difflib
import io
import json
import logging
import re
import zipfile
from typing import Any, Dict, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET

from sqlalchemy.orm import Session

from ..models import AuditLog
from . import files
from .models import Contract, ContractVersion, SpendFile

logger = logging.getLogger("vco.spend.purge")

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_PARAGRAPHS = 20000
MAX_DOC_XML = 50 * 1024 * 1024
PARTY_LABELS = {"legal": "Legal (nội bộ)", "finance": "Finance (nội bộ)", "partner": "Đối tác"}
STATUS_LABELS = {"draft": "Nháp", "negotiating": "Đang đàm phán", "final": "Đã chốt (Final)", "signed": "Đã ký"}


class DocxTextError(ValueError):
    pass


# ---------------------------------------------------------------- text extraction
def docx_paragraphs(data: bytes) -> List[str]:
    """Paragraph texts of word/document.xml in reading order (tables included).
    A DOCTYPE is refused: Word never writes one and it is the vehicle of entity attacks."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            info = zf.getinfo("word/document.xml")
            if info.file_size > MAX_DOC_XML:
                raise DocxTextError("Nội dung văn bản quá lớn.")
            xml = zf.read(info)
    except (KeyError, zipfile.BadZipFile) as exc:
        raise DocxTextError("Không đọc được nội dung .docx.") from exc
    if b"<!DOCTYPE" in xml[:4096].upper() or b"<!ENTITY" in xml.upper():
        raise DocxTextError("File .docx chứa khai báo DOCTYPE / ENTITY không hợp lệ.")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise DocxTextError("Nội dung .docx bị hỏng.") from exc
    out: List[str] = []
    for p in root.iter(W + "p"):
        parts = []
        for node in p.iter():
            if node.tag == W + "t" and node.text:
                parts.append(node.text)
            elif node.tag == W + "tab":
                parts.append("\t")
            elif node.tag in (W + "br", W + "cr"):
                parts.append("\n")
        text = "".join(parts).strip()
        if text:
            out.append(text)
        if len(out) > MAX_PARAGRAPHS:
            raise DocxTextError(f"Văn bản có quá {MAX_PARAGRAPHS} đoạn.")
    return out


def paragraphs_of(v: ContractVersion) -> Optional[List[str]]:
    if v.text_content is None:
        return None
    try:
        return json.loads(v.text_content)
    except ValueError:
        return None


# ---------------------------------------------------------------- comparison
_WORD = re.compile(r"\s+|[^\s]+")


def _word_diff(a: str, b: str) -> List[Dict[str, str]]:
    aw, bw = _WORD.findall(a), _WORD.findall(b)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, aw, bw, autojunk=False).get_opcodes():
        if op == "equal":
            out.append({"op": "equal", "text": "".join(aw[i1:i2])})
        else:
            if i2 > i1:
                out.append({"op": "delete", "text": "".join(aw[i1:i2])})
            if j2 > j1:
                out.append({"op": "insert", "text": "".join(bw[j1:j2])})
    return out


def compare(versions: Sequence[Tuple[int, str, List[str]]]) -> Dict[str, Any]:
    """versions: consecutive [(version_no, party, paragraphs), …] from the base ("from") to the
    target ("to"). Returns blocks of the from→to diff with attribution to the version that made
    each change."""
    if len(versions) < 2:
        raise ValueError("need two versions")
    base_no = versions[0][0]
    # Track every paragraph of the current step: (text, origin index in base or None, version that wrote it).
    cur: List[Tuple[str, Optional[int], int]] = [(t, i, base_no) for i, t in enumerate(versions[0][2])]
    removed_by: Dict[int, int] = {}
    for no, _party, paras in versions[1:]:
        nxt: List[Tuple[str, Optional[int], int]] = []
        sm = difflib.SequenceMatcher(None, [c[0] for c in cur], paras, autojunk=False)
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal":
                nxt.extend(cur[i1:i2])
                continue
            for c in cur[i1:i2]:
                if c[1] is not None and op in ("delete", "replace"):
                    removed_by.setdefault(c[1], no)
            nxt.extend((t, None, no) for t in paras[j1:j2])
        cur = nxt
    party_of = {no: party for no, party, _ in versions}
    base, target = versions[0][2], versions[-1][2]
    blame = [c[2] for c in cur]          # version that wrote each paragraph of the target
    blocks: List[Dict[str, Any]] = []
    stats = {p: 0 for p in PARTY_LABELS}

    def mark(no: int) -> Dict[str, Any]:
        party = party_of.get(no, "legal")
        stats[party] += 1
        return {"version": no, "party": party}

    sm = difflib.SequenceMatcher(None, base, target, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            blocks.append({"op": "equal", "paragraphs": target[j1:j2]})
        elif op == "delete":
            for i in range(i1, i2):
                blocks.append({"op": "delete", "text": base[i], **mark(removed_by.get(i, versions[-1][0]))})
        elif op == "insert":
            for j in range(j1, j2):
                blocks.append({"op": "insert", "text": target[j], **mark(blame[j])})
        else:   # replace: pair paragraphs one to one, word-level diff when they are similar
            olds, news = base[i1:i2], target[j1:j2]
            for k in range(max(len(olds), len(news))):
                old = olds[k] if k < len(olds) else None
                new = news[k] if k < len(news) else None
                if old is not None and new is not None and difflib.SequenceMatcher(None, old, new).ratio() >= 0.5:
                    blocks.append({"op": "change", "words": _word_diff(old, new), **mark(blame[j1 + k])})
                else:
                    if old is not None:
                        blocks.append({"op": "delete", "text": old, **mark(removed_by.get(i1 + k, versions[-1][0]))})
                    if new is not None:
                        blocks.append({"op": "insert", "text": new, **mark(blame[j1 + k])})
    return {"blocks": blocks, "changesByParty": stats}


# ---------------------------------------------------------------- purge
def purge_due(db: Session, now: Optional[dt.datetime] = None, username: str = "system") -> List[str]:
    """Delete the files of non-final versions of contracts whose waiting time is over.
    Returns the codes of the contracts processed."""
    now = now or dt.datetime.utcnow()
    done = []
    due = (db.query(Contract).filter(Contract.status.in_(("final", "signed")), Contract.purge_due_at.isnot(None),
                                     Contract.purge_due_at <= now, Contract.purge_cancelled_at.is_(None),
                                     Contract.purge_done_at.is_(None)).all())
    for c in due:
        # Re-read inside the loop: an admin may have cancelled after the query above.
        db.refresh(c)
        if c.purge_cancelled_at is not None:
            continue
        versions = db.query(ContractVersion).filter(ContractVersion.contract_id == c.id,
                                                    ContractVersion.version_no != c.final_version_no).all()
        removed = []
        for v in versions:
            f = db.get(SpendFile, v.file_id)
            if f is not None and f.purged_at is None:
                files.remove(f)
                f.purged_at, f.purged_by, f.purge_reason = now, username, "Hợp đồng đã chốt Final — xoá version cũ theo chính sách"
                removed.append({"version": v.version_no, "file": f.original_name, "sha256": f.sha256})
            v.text_content = None
        c.purge_done_at = now
        db.add(AuditLog(username=username, action="spend.contract.purge",
                        detail=json.dumps({"code": c.code, "finalVersion": c.final_version_no, "removed": removed}, ensure_ascii=False)))
        db.commit()
        logger.info("Purged %d old version file(s) of contract %s", len(removed), c.code)
        done.append(c.code)
    return done
