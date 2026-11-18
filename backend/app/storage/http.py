"""Small JSON client for the read-only REST APIs (storage arrays, Proxmox VE, Nutanix Prism): TLS (verify off /
system store / pasted CA), no redirects
(a redirect would carry the token / Basic credentials to another host), bounded response size, Vietnamese
errors that never contain the secret."""
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

from . import StorageConfig, StorageError

MAX_BYTES = 32 * 1024 * 1024
ALLOWED_POSTS = ("/login", "/logout")          # Pure session endpoints only (read-only rule)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise StorageError(f"Máy chủ chuyển hướng ({code}) sang {urllib.parse.urlsplit(newurl).netloc or newurl} — "
                           "nhập đúng địa chỉ và cổng quản trị.")


def ssl_context(cfg: StorageConfig) -> ssl.SSLContext:
    if not cfg.verify_ssl:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    pem = (cfg.ca_pem or "").strip()
    try:
        ctx = ssl.create_default_context(cadata=pem) if pem else ssl.create_default_context()
    except ssl.SSLError as exc:
        raise StorageError(f"Chứng chỉ CA không hợp lệ: {exc}") from exc
    ctx.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)
    return ctx


class JsonClient:
    """`product` / `api_hint` only word the error messages ("mảng lưu trữ" / "Purity / ONTAP" by default)."""

    def __init__(self, cfg: StorageConfig, base: str, product: str = "mảng lưu trữ", api_hint: str = "Purity / ONTAP"):
        self.cfg = cfg
        self.base = base.rstrip("/")
        self.product, self.api_hint = product, api_hint
        self.headers: Dict[str, str] = {"Accept": "application/json"}
        self.opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_context(cfg)), _NoRedirect())

    def request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None,
                headers: Optional[Dict[str, str]] = None) -> Tuple[Any, Dict[str, str]]:
        if method != "GET" and not any(path.endswith(p) for p in ALLOWED_POSTS):
            raise StorageError(f"Chặn lời gọi {method} {path}: ICO chỉ đọc dữ liệu {self.product}.")
        url = path if path.startswith("https://") else self.base + path
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, safe="=',|:<>*", quote_via=urllib.parse.quote)
        req = urllib.request.Request(url, method=method, headers={**self.headers, **(headers or {})},
                                     data=b"" if method == "POST" else None)
        host = f"{self.cfg.host}:{self.cfg.port}"
        try:
            with self.opener.open(req, timeout=self.cfg.timeout) as resp:
                raw = resp.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise StorageError(f"Phản hồi của {path} quá lớn (> 32 MB).")
                hdrs = {k.lower(): v for k, v in resp.headers.items()}
                return (json.loads(raw) if raw.strip() else {}), hdrs
        except StorageError:
            raise
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise StorageError(f"{host} từ chối xác thực (401) — kiểm tra API token / user + mật khẩu chỉ đọc.") from exc
            if exc.code == 403:
                raise StorageError(f"Tài khoản không có quyền đọc {path.split('?')[0]} (403) — gán role chỉ đọc đủ quyền xem.") from exc
            if exc.code == 404:
                raise StorageError(f"Không có API {path.split('?')[0]} (404) — kiểm tra phiên bản {self.api_hint} hỗ trợ REST.") from exc
            raise StorageError(f"Lỗi API {exc.code} tại {path.split('?')[0]}.") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, ssl.SSLCertVerificationError):
                raise StorageError(f"Chứng chỉ của {self.cfg.host} không được tin cậy ({exc.reason.verify_message}) — dán CA, "
                                   "kiểm tra tên máy trong chứng chỉ, hoặc tắt kiểm tra chứng chỉ.") from exc
            raise StorageError(f"Không kết nối được {host} — {exc.reason}") from exc
        except (TimeoutError, OSError) as exc:
            raise StorageError(f"Hết thời gian chờ / lỗi mạng tới {host} — {exc}") from exc
        except ValueError as exc:
            raise StorageError(f"{path.split('?')[0]} trả về dữ liệu không phải JSON.") from exc

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        return self.request("GET", path, params)[0]
