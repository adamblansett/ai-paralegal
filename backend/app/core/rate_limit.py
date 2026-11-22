import math
import threading
import time
import uuid
from collections import defaultdict, deque

import redis.asyncio as aioredis
import structlog
from fastapi import Depends, HTTPException, Request, status

from app.core.config import Settings, get_settings

logger = structlog.get_logger(__name__)


class SlidingWindowRateLimiter:
    """Sliding-window rate limiter supporting in-memory and distributed Redis ZSET backends."""

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
        """Record request if under in-memory limit; return `(allowed, retry_after_seconds)`."""
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

    async def check_async(
        self,
        bucket: str,
        key: str,
        limit: int,
        window_seconds: int = 60,
        *,
        backend: str = "memory",
        redis_url: str = "",
    ) -> tuple[bool, int]:
        """Check sliding window using Redis ZSET when configured, else in-memory."""
        use_redis = bool(redis_url) and backend in {"redis", "auto"}
        if use_redis:
            try:
                return await self._check_redis(
                    bucket=bucket,
                    key=key,
                    limit=limit,
                    window_seconds=window_seconds,
                    redis_url=redis_url,
                )
            except Exception as exc:
                if backend == "redis":
                    logger.warning(
                        "redis_rate_limit_fallback_to_memory",
                        bucket=bucket,
                        error=str(exc),
                    )
        return self.check(bucket, key, limit, window_seconds)

    async def _check_redis(
        self,
        *,
        bucket: str,
        key: str,
        limit: int,
        window_seconds: int,
        redis_url: str,
    ) -> tuple[bool, int]:
        now_ms = int(time.time() * 1000)
        window_ms = window_seconds * 1000
        cutoff_ms = now_ms - window_ms
        redis_key = f"ai_paralegal:ratelimit:{bucket}:{key}"
        member = f"{now_ms}:{uuid.uuid4().hex}"

        client = aioredis.from_url(redis_url, socket_connect_timeout=1.0, socket_timeout=1.0)
        try:
            async with client.pipeline(transaction=True) as pipe:
                pipe.zremrangebyscore(redis_key, 0, cutoff_ms)
                pipe.zcard(redis_key)
                results = await pipe.execute()
                current_count = int(results[1] or 0)
                if current_count >= limit:
                    oldest = await client.zrange(redis_key, 0, 0, withscores=True)
                    retry_after = window_seconds
                    if oldest:
                        oldest_ms = int(oldest[0][1])
                        retry_after = max(1, math.ceil((oldest_ms + window_ms - now_ms) / 1000))
                    return False, retry_after

                pipe2 = client.pipeline(transaction=True)
                pipe2.zadd(redis_key, {member: now_ms})
                pipe2.pexpire(redis_key, window_ms)
                await pipe2.execute()
                return True, 0
        finally:
            await client.aclose()

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
    allowed, retry_after = await limiter.check_async(
        "auth",
        _client_key(request),
        limit=settings.rate_limit_auth_per_minute,
        backend=settings.rate_limit_backend,
        redis_url=settings.redis_url,
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
    allowed, retry_after = await limiter.check_async(
        "llm",
        _client_key(request),
        limit=settings.rate_limit_llm_per_minute,
        backend=settings.rate_limit_backend,
        redis_url=settings.redis_url,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="LLM request rate limit exceeded. Please retry later.",
            headers={"Retry-After": str(retry_after)},
        )
