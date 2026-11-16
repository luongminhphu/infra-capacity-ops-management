"""HTTP surface hardening shared by every response (audit SEC-12 / SEC2-03, SEC2-08).

SecurityHeadersMiddleware
    CSP step 1 (decision Q4, 16/11/2026): scripts only from this origin (the app has no inline
    script or on*= handler), no plugins, no framing, forms / base only to self. `style-src` still
    allows 'unsafe-inline' because templates use style="" attributes; it is tightened once those
    are gone. Plus nosniff, X-Frame-Options for old browsers, Referrer-Policy, Permissions-Policy
    and HSTS when the app is served over HTTPS (same condition as secure cookies).

BodyLimitMiddleware
    Rejects a request body larger than MAX_REQUEST_MB with 413, from Content-Length when present
    and by counting streamed chunks otherwise (chunked uploads used to bypass the per-route checks).
"""
from typing import Callable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

CSP_STEP1 = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
             "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
             "frame-ancestors 'none'")


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, https: Callable[[], bool]):
        self.app, self.https = app, https

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", [])]
                have = {k.lower() for k, _ in headers}

                def add(name: str, value: str) -> None:
                    if name.encode() not in have:                # a route may set its own (downloads, PDFs)
                        headers.append((name.encode(), value.encode()))
                add("content-security-policy", CSP_STEP1)
                add("x-content-type-options", "nosniff")
                add("x-frame-options", "DENY")
                add("referrer-policy", "same-origin")
                add("permissions-policy", "camera=(), microphone=(), geolocation=(), payment=()")
                if self.https():
                    add("strict-transport-security", "max-age=31536000")
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: Callable[[], int]):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.max_bytes()
        for k, v in scope.get("headers", []):
            if k == b"content-length" and v.isdigit() and int(v) > limit:
                await _too_large(send, limit)
                return
        seen, exceeded, started = 0, False, False

        async def receive_wrapper() -> Message:
            nonlocal seen, exceeded
            if exceeded:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:                         # the route sees a disconnect and stops reading
                    exceeded = True
                    return {"type": "http.disconnect"}
            return message

        async def send_wrapper(message: Message) -> None:
            nonlocal started
            if exceeded:                                 # whatever the route answers (422, 400…), the client gets 413
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except Exception:                                # noqa: BLE001 — a route failing on the cut body
            if not exceeded:
                raise
        if exceeded and not started:
            await _too_large(send, limit)


async def _too_large(send: Send, limit: int) -> None:
    body = ('{"detail":"Dữ liệu gửi lên quá lớn (tối đa %d MB)."}' % (limit // (1024 * 1024))).encode()
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json; charset=utf-8"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})
