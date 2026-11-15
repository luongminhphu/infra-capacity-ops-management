"""Outbound reminder channels: SMTP e-mail and Discord webhooks.

* Discord URL must be an https Discord webhook (SSRF guard); redirects are not followed;
  user text is markdown-escaped and mentions are disabled (allowed_mentions.parse = []).
* E-mail headers drop CR/LF; the HTML part escapes every value.
* Error messages returned to the UI / log never contain the webhook URL or the password.
"""
import html
import re
import smtplib
import ssl
import urllib.error
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from typing import Iterable, List, Optional, Sequence

from .. import secrets_box
from .models import SpendNotifyChannel, SpendSmtpConfig
from .service import EMAIL_RE

from ..shared.webhook import DISCORD_HOSTS, WebhookError as NotifyError, post_json, validate_discord_url  # noqa: F401,E402
DISCORD_LIMIT = 1900          # Discord rejects content over 2000 characters
TIMEOUT = 15
USER_AGENT = "ICO-Spend-Reminder/1.0"


@dataclass
class ReminderLine:
    code: str
    title: str
    vendor: str
    end_date: str          # dd/mm/yyyy
    days_left: int
    amount: str            # formatted VND
    owner: str
    owner_email: Optional[str]
    link: Optional[str]


# ---------------------------------------------------------------- validation
def parse_recipients(value: Optional[str]) -> List[str]:
    out = []
    for part in re.split(r"[,;\s]+", value or ""):
        if not part:
            continue
        if not EMAIL_RE.match(part) or len(part) > 254:
            raise NotifyError(f"Địa chỉ email không hợp lệ: {part[:60]}")
        out.append(part)
    if len(out) > 50:
        raise NotifyError("Tối đa 50 người nhận cho một kênh.")
    return out


# ---------------------------------------------------------------- formatting
_MD = re.compile(r"([\\*_~`|>#\[\]()<:-])")


def discord_escape(text: str) -> str:
    """Neutralise markdown, masked links and mentions in user-entered text."""
    t = _MD.sub(r"\\\1", str(text or ""))
    return t.replace("@", "@​")


def _days(d: int) -> str:
    return "hết hạn hôm nay" if d == 0 else f"còn {d} ngày"


def discord_messages(lines: Sequence[ReminderLine], test: bool = False) -> List[str]:
    head = ("**[ICO] Thử kênh nhắc hạn** — tin thử, không cần xử lý." if test
            else f"**[ICO] Nhắc hạn chi phí IT** — {len(lines)} khoản sắp hết hạn, cần quyết định gia hạn:")
    chunks, cur = [], head
    for ln in lines:
        row = (f"\n• **{discord_escape(ln.code)}** — {discord_escape(ln.title)} ({discord_escape(ln.vendor)})"
               f"\n   Hết hạn {ln.end_date} ({_days(ln.days_left)}) · {discord_escape(ln.amount)} · Phụ trách: {discord_escape(ln.owner)}")
        if ln.link:
            row += f"\n   <{ln.link}>"
        if len(cur) + len(row) > DISCORD_LIMIT:
            chunks.append(cur)
            cur = "(tiếp)"
        cur += row
    chunks.append(cur)
    return chunks


def _header(text: str) -> str:
    return re.sub(r"[\r\n]+", " ", text)[:200]


def email_content(lines: Sequence[ReminderLine], test: bool = False):
    if test:
        subject = "[ICO] Thử kênh nhắc hạn chi phí IT"
        intro = "Đây là email thử từ module Chi phí & Hợp đồng IT. Kênh hoạt động bình thường."
    else:
        subject = f"[ICO] Nhắc hạn chi phí IT: {len(lines)} khoản sắp hết hạn"
        intro = "Các khoản dưới đây sắp hết hạn. Đề nghị người phụ trách quyết định gia hạn hoặc ngừng."
    text = [intro, ""]
    for ln in lines:
        text.append(f"- {ln.code} — {ln.title} ({ln.vendor}): hết hạn {ln.end_date} ({_days(ln.days_left)}), "
                    f"{ln.amount}, phụ trách {ln.owner}" + (f"\n  {ln.link}" if ln.link else ""))
    e = html.escape
    rows = "".join(
        f"<tr><td style='padding:4px 8px'><b>{e(ln.code)}</b></td><td style='padding:4px 8px'>{e(ln.title)}</td>"
        f"<td style='padding:4px 8px'>{e(ln.vendor)}</td><td style='padding:4px 8px'>{e(ln.end_date)}<br><small>{e(_days(ln.days_left))}</small></td>"
        f"<td style='padding:4px 8px;text-align:right'>{e(ln.amount)}</td><td style='padding:4px 8px'>{e(ln.owner)}</td>"
        f"<td style='padding:4px 8px'>{('<a href=' + chr(34) + e(ln.link) + chr(34) + '>Mở</a>') if ln.link else ''}</td></tr>"
        for ln in lines)
    table = ("<table style='border-collapse:collapse;font-size:13px' border='1' cellspacing='0'><tr>"
             "<th>Mã</th><th>Dịch vụ</th><th>Nhà cung cấp</th><th>Hết hạn</th><th>Số tiền</th><th>Phụ trách</th><th></th></tr>"
             + rows + "</table>") if lines else ""
    body_html = f"<div style='font-family:Arial,sans-serif'><p>{e(intro)}</p>{table}</div>"
    return subject, "\n".join(text), body_html


# ---------------------------------------------------------------- transport
def _post_json(url: str, payload: dict) -> None:
    post_json(url, payload, USER_AGENT)


def send_discord(url: str, lines: Sequence[ReminderLine], test: bool = False) -> None:
    url = validate_discord_url(url)
    for content in discord_messages(lines, test):
        _post_json(url, {"content": content, "allowed_mentions": {"parse": []}, "username": "ICO nhắc hạn"})


def smtp_send(cfg: SpendSmtpConfig, to: Iterable[str], subject: str, text: str, body_html: str) -> None:
    to = list(to)
    if not to:
        return
    msg = EmailMessage()
    msg["Subject"] = _header(subject)
    msg["From"] = formataddr(("ICO – Chi phí IT", cfg.from_addr))
    msg["To"] = ", ".join(to)
    msg.set_content(text)
    msg.add_alternative(body_html, subtype="html")
    try:
        password = secrets_box.decrypt(cfg.password_enc) if cfg.password_enc else ""
    except secrets_box.SecretUnreadable:
        raise NotifyError("Mật khẩu SMTP đã lưu không giải mã được (SECRETS_KEY đổi) — nhập lại.") from None
    ctx = ssl.create_default_context()
    try:
        if cfg.security == "ssl":
            server = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=TIMEOUT, context=ctx)
        else:
            server = smtplib.SMTP(cfg.host, cfg.port, timeout=TIMEOUT)
        with server:
            if cfg.security == "starttls":
                server.starttls(context=ctx)
            if cfg.username:
                server.login(cfg.username, password)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise NotifyError("Máy chủ SMTP từ chối đăng nhập (sai tài khoản / mật khẩu).") from None
    except smtplib.SMTPRecipientsRefused:
        raise NotifyError("Máy chủ SMTP từ chối địa chỉ người nhận.") from None
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        raise NotifyError(f"Gửi email thất bại: {type(exc).__name__}.") from None


def send_email(cfg: Optional[SpendSmtpConfig], channel: SpendNotifyChannel, lines: Sequence[ReminderLine],
               test: bool = False) -> None:
    """Fixed recipients get every line; with include_owner, each owner also gets their own lines."""
    if cfg is None:
        raise NotifyError("Chưa cấu hình máy chủ SMTP (Cài đặt → Chi phí & nhắc hạn).")
    recipients = parse_recipients(channel.recipients)
    if recipients:
        smtp_send(cfg, recipients, *email_content(lines, test))
    if channel.include_owner and not test:
        by_owner = {}
        for ln in lines:
            if ln.owner_email and ln.owner_email not in recipients:
                by_owner.setdefault(ln.owner_email, []).append(ln)
        for addr, own in by_owner.items():
            smtp_send(cfg, [addr], *email_content(own))


def send(channel: SpendNotifyChannel, smtp_cfg: Optional[SpendSmtpConfig], lines: Sequence[ReminderLine],
         test: bool = False) -> None:
    if channel.kind == "discord":
        try:
            url = secrets_box.decrypt(channel.webhook_enc)
        except secrets_box.SecretUnreadable:
            raise NotifyError("Webhook đã lưu không giải mã được (SECRETS_KEY đổi) — nhập lại.") from None
        SENDERS["discord"](url, lines, test)
    else:
        SENDERS["email"](smtp_cfg, channel, lines, test)


# Indirection so tests can capture deliveries without network access.
SENDERS = {"discord": send_discord, "email": send_email}
