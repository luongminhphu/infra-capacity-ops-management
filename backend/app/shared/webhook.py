"""Outgoing webhooks (Discord): URL validation and a JSON POST that never follows redirects.
Shared by reminders (Chi phí), operations alerts (90 %) and the AI cost alert."""
import json
import re
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlparse

DISCORD_HOSTS = {"discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com"}
TIMEOUT = 15
USER_AGENT = "ICO/1.0"


class WebhookError(Exception):
    """Delivery failed; the message is safe to show and to store."""


NotifyError = WebhookError      # name used by the callers' messages


def validate_discord_url(url: str) -> str:
    url = (url or "").strip()
    u = urlparse(url)
    if (u.scheme != "https" or (u.hostname or "").lower() not in DISCORD_HOSTS or u.port not in (None, 443)
            or u.username or u.password or not re.match(r"^/api/webhooks/\d+/[A-Za-z0-9_\-]+/?$", u.path or "")
            or u.query or u.fragment):
        raise WebhookError("Webhook Discord phải có dạng https://discord.com/api/webhooks/<id>/<token>.")
    return url



class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401
        return None


def post_json(url: str, payload: dict, user_agent: str = USER_AGENT) -> None:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": user_agent})
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(req, timeout=TIMEOUT) as resp:
            if resp.status >= 300:
                raise WebhookError(f"Discord trả mã HTTP {resp.status}.")
    except urllib.error.HTTPError as exc:
        hint = {401: "webhook không hợp lệ", 404: "webhook đã bị xoá", 429: "bị giới hạn tốc độ, sẽ thử lại"}.get(exc.code, "")
        raise WebhookError(f"Discord trả mã HTTP {exc.code}{' — ' + hint if hint else ''}.") from None
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise WebhookError(f"Không kết nối được Discord: {type(reason).__name__}.") from None


