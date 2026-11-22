import math
import threading
import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request, status

from app.core.config import Settings, get_settings


class SlidingWindowRateLimiter:
    """In-memory sliding-window rate limiter keyed by endpoint bucket and client key."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def check(
        self,
        bucket: str,
        key: str,
        limit: int,
        window_seconds: int = 60,
    ) -> tuple[bool, int]:
        """Record request if under limit; return `(allowed, retry_after_seconds)`."""
        now = time.monotonic()
        cutoff = now - window_seconds
        composite = (bucket, key)
        with self._lock:
            q = self._events[composite]
            while q and q[0] <= cutoff:
                q.popleft()
            if len(q) >= limit:
                retry_after = max(1, math.ceil(window_seconds - (now - q[0])))
                return False, retry_after
            q.append(now)
            return True, 0

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _get_limiter(request: Request) -> SlidingWindowRateLimiter:
    limiter = getattr(request.app.state, "rate_limiter", None)
    if isinstance(limiter, SlidingWindowRateLimiter):
        return limiter
    created = SlidingWindowRateLimiter()
    request.app.state.rate_limiter = created
    return created


async def rate_limit_auth(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> None:
    """Enforce per-IP sliding-window limit on authentication endpoints."""
    if not settings.rate_limit_enabled:
        return
    limiter = _get_limiter(request)
    allowed, retry_after = limiter.check(
        "auth",
        _client_key(request),
        limit=settings.rate_limit_auth_per_minute,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Authentication rate limit exceeded. Please retry later.",
            headers={"Retry-After": str(retry_after)},
        )


async def rate_limit_llm(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> None:
    """Enforce per-IP sliding-window limit on expensive LLM analysis/drafting endpoints."""
    if not settings.rate_limit_enabled:
        return
    limiter = _get_limiter(request)
    allowed, retry_after = limiter.check(
        "llm",
        _client_key(request),
        limit=settings.rate_limit_llm_per_minute,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="LLM request rate limit exceeded. Please retry later.",
            headers={"Retry-After": str(retry_after)},
        )
