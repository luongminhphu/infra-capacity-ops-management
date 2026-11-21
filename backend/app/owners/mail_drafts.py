"""Draft e-mails of the periodic owner resource review (ISO/IEC 27001:2022 — A.5.9 inventory, A.5.11 return of
assets, A.5.18 access rights, A.8.6 capacity management).

The infrastructure team edits ONE template (subject + body with {placeholders}) and downloads one draft per owner
as an .eml file (header X-Unsent: 1 — Outlook / Thunderbird open it as a new message to review and send), optionally
with the owner's Excel attached. Nothing is sent from here: drafts are files, so building them twice has no effect.

Safety: placeholders are a fixed list (no str.format — no attribute access); every value is HTML-escaped in the HTML
part; subject and addresses go through mailer.header (no CR/LF); addresses come from systems (validated on write)."""
import datetime as dt
import html
import io
import re
import zipfile
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import User
from ..shared import mailer
from . import inventory, review
from .models import OwnerMailTemplate

KIND = "review"
MAX_SUBJECT = 255
MAX_BODY = 20000
MAX_OWNERS = 500
MAX_VM_ROWS = 300
_PERIOD = re.compile(r"^[\w .\-/]{1,16}$")
_PH = re.compile(r"\{([a-z_]+)\}")

PLACEHOLDERS = {
    "owner_name": "Tên owner (email nếu chưa khai tên)",
    "owner_email": "Email owner",
    "period": "Kỳ rà soát, vd 2027-H1",
    "due_date": "Hạn phản hồi (dd/mm/yyyy)",
    "today": "Ngày lập bản nháp",
    "system_count": "Số hệ thống owner phụ trách",
    "system_list": "Mã — tên các hệ thống",
    "vm_count": "Số VM",
    "vm_on": "Số VM đang bật",
    "vm_off": "Số VM đang tắt",
    "vcpu": "Tổng vCPU",
    "ram_gb": "Tổng RAM (GB)",
    "disk_gb": "Tổng disk cấp phát (GB)",
    "unprotected": "Số VM đang bật chưa có backup",
    "backup_names": "Backup (người thay thế) của các hệ thống",
    "portal_link": "Liên kết Cổng Owner trên ICO",
    "vm_table": "Bảng VM (đặt riêng một dòng)",
    "sender": "Người lập (tài khoản đang đăng nhập)",
}

DEFAULT_SUBJECT = "[ICO] Rà soát tài nguyên định kỳ {period} — {system_count} hệ thống, {vm_count} VM — hạn {due_date}"
DEFAULT_BODY = """Kính gửi {owner_name},

Theo quy trình vận hành an toàn thông tin ISO/IEC 27001:2022, định kỳ đội Hạ tầng đề nghị owner rà soát tài nguyên đang sử dụng của các hệ thống mình phụ trách:
- A.5.9 Kiểm kê tài sản: danh sách VM, cấu hình và người chịu trách nhiệm đúng thực tế.
- A.5.11 Hoàn trả tài sản: VM không còn dùng được thu hồi.
- A.5.18 Quyền truy cập: owner, backup và support của hệ thống còn đúng người.
- A.8.6 Quản lý dung lượng: vCPU / RAM / disk phù hợp nhu cầu thực tế.

Kỳ rà soát: {period}
Hệ thống phụ trách ({system_count}): {system_list}
Tổng tài nguyên: {vm_count} VM ({vm_on} bật · {vm_off} tắt) · {vcpu} vCPU · {ram_gb} GB RAM · {disk_gb} GB disk

{vm_table}

Đề nghị anh/chị xác nhận từng VM trước ngày {due_date}:
1. Còn sử dụng — giữ nguyên.
2. Thu hồi — ghi ngày có thể tắt / xoá.
3. Giảm cấu hình — ghi cấu hình đề xuất.
4. Chuyển owner — ghi email owner mới.
Đồng thời xác nhận owner / backup / support của hệ thống còn đúng người.

Xác nhận trên ICO: {portal_link}
Hoặc trả lời email này, ghi quyết định cho từng VM (file Excel đính kèm có đủ danh sách).

Quá hạn chưa phản hồi, đội Hạ tầng leo thang tới quản lý trực tiếp theo quy trình.

Trân trọng,
{sender}"""


class Invalid(ValueError):
    pass


# ---------------------------------------------------------------- template
def get_template(db: Session) -> Dict[str, Any]:
    t = db.get(OwnerMailTemplate, KIND)
    return {"subject": t.subject if t else DEFAULT_SUBJECT, "body": t.body if t else DEFAULT_BODY, "custom": t is not None,
            "updatedAt": t.updated_at.isoformat() + "Z" if t else None, "updatedBy": t.updated_by if t else None,
            "placeholders": [{"key": k, "label": v} for k, v in PLACEHOLDERS.items()],
            "defaultSubject": DEFAULT_SUBJECT, "defaultBody": DEFAULT_BODY}


def _check(subject: Any, body: Any) -> Tuple[str, str]:
    if not isinstance(subject, str) or not isinstance(body, str):
        raise Invalid("Tiêu đề và nội dung phải là chuỗi.")
    subject, body = re.sub(r"[\r\n]+", " ", subject).strip(), body.replace("\r\n", "\n").strip()
    if not 1 <= len(subject) <= MAX_SUBJECT:
        raise Invalid(f"Tiêu đề 1–{MAX_SUBJECT} ký tự.")
    if not 1 <= len(body) <= MAX_BODY:
        raise Invalid(f"Nội dung 1–{MAX_BODY:,} ký tự.".replace(",", "."))
    unknown = sorted({m for m in _PH.findall(subject + "\n" + body) if m not in PLACEHOLDERS})
    if unknown:
        raise Invalid("Không có trường: " + ", ".join("{" + u + "}" for u in unknown[:10]) + " — xem danh sách trường bên dưới.")
    if "{vm_table}" in subject:
        raise Invalid("{vm_table} chỉ dùng trong nội dung.")
    return subject, body


def save_template(db: Session, user: User, subject: Any, body: Any) -> Dict[str, Any]:
    subject, body = _check(subject, body)
    t = db.get(OwnerMailTemplate, KIND) or OwnerMailTemplate(kind=KIND)
    t.subject, t.body, t.updated_at, t.updated_by = subject, body, dt.datetime.utcnow(), user.username
    db.add(t)
    db.flush()
    from .service import audit
    audit(db, user, "owners.mail_template.save", subject=subject[:120])
    return get_template(db)


def reset_template(db: Session, user: User) -> Dict[str, Any]:
    t = db.get(OwnerMailTemplate, KIND)
    if t is not None:
        db.delete(t)
        from .service import audit
        audit(db, user, "owners.mail_template.reset")
    db.flush()
    return get_template(db)


# ---------------------------------------------------------------- recipients
def portal_link() -> str:
    base = get_settings().public_base()
    return f"{base}/#/owner" if base else ""


def clean_period(period: Optional[str], due: Optional[str], today: Optional[dt.date] = None) -> Tuple[str, dt.date]:
    today = today or review.today_vn()
    p = (period or "").strip() or review.period_label(today)
    if not _PERIOD.match(p):
        raise Invalid("Kỳ rà soát tối đa 16 ký tự (chữ, số, - . /).")
    if due:
        try:
            d = dt.date.fromisoformat(due)
        except ValueError:
            raise Invalid("Hạn phản hồi phải có dạng yyyy-mm-dd.") from None
    else:
        d = today + dt.timedelta(days=14)
    return p, d


def owners(db: Session, osc=None, owner: Optional[str] = None) -> Dict[str, Any]:
    """Systems of the scope grouped by owner e-mail: {"owners": [{email, name, cc, systems, vms, totals}], "noOwner": [...]}.
    owner: only that owner's group (preview / one draft)."""
    inv = inventory.build(db, osc, include_unowned=False, owner=owner)
    groups: Dict[str, Dict[str, Any]] = {}
    no_owner = []
    for s in inv["systems"]:
        email = (s["ownerEmail"] or "").lower()
        if not email:
            no_owner.append({"code": s["code"], "name": s["name"], "vms": s["totals"]["vms"]})
            continue
        g = groups.setdefault(email, {"email": email, "name": None, "systems": [], "cc": []})
        g["name"] = g["name"] or s["ownerName"]
        g["systems"].append(s)
        b = (s["backupEmail"] or "").lower()
        if b and b != email and b not in g["cc"]:
            g["cc"].append(b)
    out = []
    for g in sorted(groups.values(), key=lambda x: x["email"]):
        vms = [v for s in g["systems"] for v in s["vms"]]
        out.append({**g, "vms": vms, "totals": inventory._totals(vms)})
    return {"owners": out[:MAX_OWNERS], "noOwner": no_owner, "truncated": len(out) > MAX_OWNERS,
            "backupMonitored": inv["backupMonitored"], "scope": inv["scope"]}


def summary(o: Dict[str, Any]) -> Dict[str, Any]:
    return {"email": o["email"], "name": o["name"], "cc": o["cc"], "vmCount": o["totals"]["vms"],
            "systems": [{"code": s["code"], "name": s["name"], "vms": s["totals"]["vms"]} for s in o["systems"]]}


# ---------------------------------------------------------------- render
def _vn(v: Any, d: Optional[int] = None) -> str:
    """vi-VN number: integers without decimals, else 1 decimal (4,5 GB)."""
    if d is None:
        d = 0 if v is None or float(v).is_integer() else 1
    return review._vn(v, d)


def _values(o: Dict[str, Any], period: str, due: dt.date, sender: str, today: dt.date) -> Dict[str, str]:
    t = o["totals"]
    link = portal_link()
    backups = sorted({(s["backupName"] or s["backupEmail"]) for s in o["systems"] if s["backupEmail"] or s["backupName"]})
    return {"owner_name": o["name"] or o["email"], "owner_email": o["email"], "period": period,
            "due_date": due.strftime("%d/%m/%Y"), "today": today.strftime("%d/%m/%Y"),
            "system_count": _vn(len(o["systems"])), "system_list": ", ".join(f"{s['code']} — {s['name']}" for s in o["systems"]),
            "vm_count": _vn(t["vms"]), "vm_on": _vn(t["on"]), "vm_off": _vn(t["off"]), "vcpu": _vn(t["vcpu"]),
            "ram_gb": _vn(t["ramGB"]), "disk_gb": _vn(t["diskGB"]), "unprotected": _vn(t["unprotected"]),
            "backup_names": ", ".join(backups) or "chưa có",
            "portal_link": link or "đăng nhập ICO bằng tài khoản công ty → Cổng Owner", "sender": sender}


def _fill(text: str, values: Dict[str, str], esc=lambda x: x) -> str:
    return _PH.sub(lambda m: esc(values[m.group(1)]) if m.group(1) in values else m.group(0), text)


def guest_name(v: Dict[str, Any]) -> Optional[str]:
    """Hostname inside the VM when it says more than the VMware name (differs beyond case / domain suffix)."""
    h = (v.get("hostname") or "").strip()
    if not h:
        return None
    short, name = h.split(".")[0].lower(), (v.get("vm") or "").strip().lower()
    return None if h.lower() == name or short == name else h


def _table_text(o: Dict[str, Any], backup: bool) -> str:
    lines = []
    for s in o["systems"]:
        lines.append(f"{s['code']} — {s['name']} ({len(s['vms'])} VM)")
        for v in s["vms"][:MAX_VM_ROWS]:
            gn = guest_name(v)
            lines.append(f"  - {v['vm']}{f' (hostname {gn})' if gn else ''} [{v['vcenterKey']} · cluster {v.get('cluster') or '—'} · "
                         f"IP {v.get('ip') or '—'}] {'bật' if v['powerOn'] else 'tắt'}, {_vn(v['vcpu'])} vCPU, "
                         f"{_vn(v['ramGB'])} GB RAM, {_vn(v['diskGB'])} GB disk" + (f", {v['backupLabel']}" if backup else ""))
        if len(s["vms"]) > MAX_VM_ROWS:
            lines.append(f"  … và {len(s['vms']) - MAX_VM_ROWS} VM khác (xem file Excel / ICO).")
    return "\n".join(lines)


def _table_html(o: Dict[str, Any], backup: bool) -> str:
    e = html.escape
    td = "padding:5px 8px;border-bottom:1px solid #CED5DE;vertical-align:top"
    head = ["Hệ thống", "VM", "Cluster", "IP", "Nguồn", "vCPU", "RAM (GB)", "Disk (GB)", "Hệ điều hành"] + (["Backup"] if backup else [])
    num = {5, 6, 7}
    th = "".join(f"<th style='{td};text-align:{'right' if i in num else 'left'};background:#EEF2F7'>{e(h)}</th>" for i, h in enumerate(head))
    rows = []
    for s in o["systems"]:
        for v in s["vms"][:MAX_VM_ROWS]:
            gn = guest_name(v)
            cells = [f"<b>{e(s['code'])}</b><br><span style='color:#465F83'>{e(s['name'])}</span>",
                     f"<b>{e(v['vm'])}</b>" + (f"<br><span style='color:#0B2D5C'>Hostname: {e(gn)}</span>" if gn else "")
                     + f"<br><span style='color:#465F83'>{e(v['vcenterKey'])}</span>",
                     e(v.get("cluster") or "—"), e(v.get("ip") or "—"),
                     "Bật" if v["powerOn"] else "Tắt", e(_vn(v["vcpu"])), e(_vn(v["ramGB"])), e(_vn(v["diskGB"])), e(v["os"] or "—")]
            if backup:
                cells.append(e(v["backupLabel"]))
            rows.append("<tr>" + "".join(f"<td style='{td};text-align:{'right' if i in num else 'left'}'>{c}</td>"
                                         for i, c in enumerate(cells)) + "</tr>")
        if len(s["vms"]) > MAX_VM_ROWS:
            rows.append(f"<tr><td style='{td}' colspan='{len(head)}'>… và {len(s['vms']) - MAX_VM_ROWS} VM khác của "
                        f"{e(s['code'])} — xem file Excel / ICO.</td></tr>")
    if not rows:
        rows.append(f"<tr><td style='{td}' colspan='{len(head)}'>Chưa có VM nào được gán vào các hệ thống này.</td></tr>")
    return f"<table style='border-collapse:collapse;font-size:13px' cellspacing='0'><tr>{th}</tr>{''.join(rows)}</table>"


def render(tpl: Dict[str, Any], o: Dict[str, Any], period: str, due: dt.date, sender: str, backup: bool,
           today: Optional[dt.date] = None) -> Tuple[str, str, str]:
    """(subject, text, html) of the draft of one owner."""
    today = today or review.today_vn()
    values = _values(o, period, due, sender, today)
    subject = mailer.header(_fill(tpl["subject"], values), MAX_SUBJECT)
    text = _fill(tpl["body"].replace("{vm_table}", _table_text(o, backup)), values)
    link = portal_link()
    paras = []
    src = re.sub(r"(?m)^[ \t]*\{vm_table\}[ \t]*$", "\n{vm_table}\n", tpl["body"])      # the table is its own block
    for block in re.split(r"\n\s*\n", src):
        if not block.strip():
            continue
        if block.strip() == "{vm_table}":
            paras.append(_table_html(o, backup))
            continue
        h = _fill(html.escape(block.strip("\n")), {**values, "vm_table": ""}, html.escape)
        if link:
            esc_link = html.escape(link)
            h = h.replace(esc_link, f"<a href=\"{esc_link}\">{esc_link}</a>")
        paras.append(f"<p style='margin:0 0 12px'>{h.replace(chr(10), '<br>')}</p>")
    body = f"<div style='font-family:Arial,sans-serif;font-size:14px;color:#0B2D5C;line-height:1.5'>{''.join(paras)}</div>"
    return subject, text, body


# ---------------------------------------------------------------- .eml
def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "owner"


def smtp_from(db: Session):
    """SMTP settings for the From header (read once per download), or None: the mail client fills From."""
    try:
        from ..spend.notify import smtp_settings
        return smtp_settings(db, from_name="ICO – Rà soát owner")
    except Exception:                       # SMTP not readable
        return None


def eml(o: Dict[str, Any], mail: Tuple[str, str, str], period: str, attach: bool, smtp=None) -> Tuple[str, bytes]:
    subject, text, body = mail
    msg = EmailMessage()
    if smtp and smtp.from_addr:
        msg["From"] = formataddr((mailer.header(smtp.from_name), mailer.header(smtp.from_addr)))
    msg["To"] = mailer.header(o["email"])
    if o["cc"]:
        msg["Cc"] = mailer.header(", ".join(o["cc"]), 1000)
    msg["Subject"] = subject
    msg["X-Unsent"] = "1"                   # open as a draft (Outlook)
    msg.set_content(text)
    msg.add_alternative(body, subtype="html")
    if attach:
        from ..excel_systems import build_xlsx
        d = {"systems": o["systems"], "unowned": None, "scope": f"Owner: {o['email']}",
             "totals": {**o["totals"], "systems": len(o["systems"])}}
        msg.add_attachment(build_xlsx(d, f"VM cần rà soát — kỳ {period}"), maintype="application",
                           subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           filename=f"ra-soat-{_slug(period)}-{_slug(o['email'].split('@')[0])}.xlsx")
    return f"ra-soat-{_slug(period)}-{_slug(o['email'].split('@')[0])}.eml", bytes(msg)


def zip_all(items: List[Tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    used = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in items:
            n, i = name, 2
            while n in used:
                n, i = name.replace(".eml", f"-{i}.eml"), i + 1
            used.add(n)
            z.writestr(n, data)
    return buf.getvalue()
