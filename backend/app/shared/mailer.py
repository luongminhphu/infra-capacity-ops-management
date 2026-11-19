"""SMTP transport shared by every module (spend reminders, owner reviews…). Pure transport: the caller loads the
SMTP settings (today: spend.notify.smtp_settings — the one SMTP configuration of the app) and builds the content.

* Headers drop CR/LF (no header injection); callers escape every value they put in the HTML part.
* Error messages never contain the password; they are Vietnamese and safe to show / store.
"""
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from typing import Iterable

TIMEOUT = 15


class MailError(Exception):
    """Sending failed; the message is safe to show."""


@dataclass
class SmtpSettings:
    host: str
    port: int
    security: str            # starttls | ssl | none
    username: str
    password: str
    from_addr: str
    from_name: str = "ICO"


def header(text: str, limit: int = 200) -> str:
    return re.sub(r"[\r\n]+", " ", str(text or ""))[:limit]


def build_message(cfg: SmtpSettings, to: Iterable[str], subject: str, text: str, body_html: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = header(subject)
    msg["From"] = formataddr((header(cfg.from_name, 80), cfg.from_addr))
    msg["To"] = ", ".join(header(a, 255) for a in to)
    msg.set_content(text)
    msg.add_alternative(body_html, subtype="html")
    return msg


def send(cfg: SmtpSettings, to: Iterable[str], subject: str, text: str, body_html: str) -> None:
    to = [a for a in to if a]
    if not to:
        return
    msg = build_message(cfg, to, subject, text, body_html)
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
                server.login(cfg.username, cfg.password)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise MailError("Máy chủ SMTP từ chối đăng nhập (sai tài khoản / mật khẩu).") from None
    except smtplib.SMTPRecipientsRefused:
        raise MailError("Máy chủ SMTP từ chối địa chỉ người nhận.") from None
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        raise MailError(f"Gửi email thất bại: {type(exc).__name__}.") from None
