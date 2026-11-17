"""Throttle password guessing (audit SEC-04 / SEC2-01).

In-process counters: each pod limits on its own (k8s/04-app.yaml runs 2 replicas), which still bounds
guessing to N x the rate.

- per (client IP, username): after FREE_FAILURES wrong passwords within WINDOW, each new attempt
  waits an exponentially growing lockout (60 s, 120 s, ... up to MAX_LOCK);
- per client IP: at most IP_FAILURES wrong attempts per WINDOW, whatever the names tried.

A success clears the (IP, username) counter. Only failures are counted, so a user who types the
right password is never slowed down by the throttle itself.
"""
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Tuple

from fastapi import HTTPException, Request

WINDOW = 15 * 60
FREE_FAILURES = 5
BASE_LOCK = 60
MAX_LOCK = 15 * 60
IP_FAILURES = 30

_lock = threading.Lock()
_pair: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)
_ip: Dict[str, Deque[float]] = defaultdict(deque)


def client_ip(request: Optional[Request]) -> str:
    """The TCP peer. X-Forwarded-For is not trusted here: anyone can send it (SEC2-09)."""
    return (request.client.host if request is not None and request.client else "")[:64]


def _trim(q: Deque[float], now: float) -> None:
    while q and now - q[0] > WINDOW:
        q.popleft()


def retry_after(ip: str, name: str, now: Optional[float] = None) -> int:
    """Seconds the caller must wait before the next attempt; 0 = allowed."""
    now = time.time() if now is None else now
    key = (ip, (name or "").strip().lower())
    with _lock:
        pq, iq = _pair[key], _ip[ip]
        _trim(pq, now)
        _trim(iq, now)
        wait = 0
        if len(pq) >= FREE_FAILURES:
            lock = min(MAX_LOCK, BASE_LOCK * 2 ** (len(pq) - FREE_FAILURES))
            wait = max(wait, int(pq[-1] + lock - now) + 1)
        if len(iq) >= IP_FAILURES:
            wait = max(wait, int(iq[0] + WINDOW - now) + 1)
        return max(0, wait)


def check(ip: str, name: str) -> None:
    wait = retry_after(ip, name)
    if wait:
        raise HTTPException(429, f"Đăng nhập sai quá nhiều lần. Thử lại sau {wait} giây.",
                            headers={"Retry-After": str(wait)})


def failed(ip: str, name: str, now: Optional[float] = None) -> None:
    now = time.time() if now is None else now
    with _lock:
        _pair[(ip, (name or "").strip().lower())].append(now)
        _ip[ip].append(now)


def succeeded(ip: str, name: str) -> None:
    with _lock:
        _pair.pop((ip, (name or "").strip().lower()), None)


def reset() -> None:
    """Tests only."""
    with _lock:
        _pair.clear()
        _ip.clear()
