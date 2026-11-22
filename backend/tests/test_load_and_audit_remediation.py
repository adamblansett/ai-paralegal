import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.core.config import Settings, validate_production_security
from app.core.logging import redact_sensitive_log_fields
from app.core.rate_limit import SlidingWindowRateLimiter
from app.services.events import MatterEventBus


def test_production_fail_fast_security_validation() -> None:
    insecure = Settings(app_env="production", auth_auto_seed_demo_users=True)
    with pytest.raises(RuntimeError, match="Production security validation failed"):
        validate_production_security(insecure)

    hardened = Settings(
        app_env="production",
        auth_secret_key="x" * 40,
        auth_auto_seed_demo_users=False,
        cors_origins="https://paralegal.example.com",
    )
    validate_production_security(hardened)


def test_structlog_secret_redaction_processor() -> None:
    sample = {
        "event": "auth_attempt",
        "password": "SuperSecretPassword123",
        "authorization": "Bearer aaa.bbb.ccc",
        "nested": {
            "gemini_api_key": "AIzaSyExampleKey",
            "note": "Called with Bearer header.payload.sig and ?token=secret123",
        },
    }
    redacted = redact_sensitive_log_fields(None, "info", sample)
    assert redacted["password"] == "[REDACTED]"
    assert redacted["authorization"] == "[REDACTED]"
    assert redacted["nested"]["gemini_api_key"] == "[REDACTED]"
    assert "header.payload.sig" not in redacted["nested"]["note"]
    assert "secret123" not in redacted["nested"]["note"]


async def test_health_live_ready_and_concurrent_sse_fanout(async_client: AsyncClient) -> None:
    live = await async_client.get("/api/v1/health/live")
    assert live.status_code == 200
    assert live.json() == {"status": "alive"}

    ready = await async_client.get("/api/v1/health/ready")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["checks"]["database"] == "ok"

    # Concurrent SSE fanout load check (25 simultaneous subscribers, zero leaked handles)
    bus = MatterEventBus()
    matter_id = "load-test-matter"
    received_counts: list[int] = []

    async def subscriber_worker() -> None:
        async with bus.subscribe(matter_id) as queue:
            evt = await asyncio.wait_for(queue.get(), timeout=2.0)
            if evt["event"] == "task_progress":
                received_counts.append(1)

    tasks = [asyncio.create_task(subscriber_worker()) for _ in range(25)]
    await asyncio.sleep(0.05)
    assert bus.subscriber_count(matter_id) == 25

    bus.publish(matter_id, "task_progress", {"status": "running"})
    await asyncio.gather(*tasks)

    assert len(received_counts) == 25
    assert bus.subscriber_count(matter_id) == 0


async def test_redis_rate_limiter_with_mock_and_memory_fallback() -> None:
    limiter = SlidingWindowRateLimiter()

    # 1. Mocked Redis pipeline allowing then rejecting on limit
    mock_pipe = MagicMock()
    mock_pipe.execute = AsyncMock(side_effect=[[0, 0], [1, True]])
    mock_pipe.__aenter__ = AsyncMock(return_value=mock_pipe)
    mock_pipe.__aexit__ = AsyncMock(return_value=False)

    mock_client = MagicMock()
    mock_client.pipeline.return_value = mock_pipe
    mock_client.aclose = AsyncMock()

    with patch("app.core.rate_limit.aioredis.from_url", return_value=mock_client):
        allowed, retry_after = await limiter.check_async(
            "auth",
            "10.0.0.5",
            limit=5,
            backend="redis",
            redis_url="redis://localhost:6379/0",
        )
        assert allowed is True
        assert retry_after == 0

    # 2. Automatic fallback to in-memory limiter when Redis raises connection error
    with patch("app.core.rate_limit.aioredis.from_url", side_effect=RuntimeError("Redis down")):
        ok1, _ = await limiter.check_async(
            "auth",
            "10.0.0.6",
            limit=1,
            backend="redis",
            redis_url="redis://localhost:6379/0",
        )
        ok2, retry2 = await limiter.check_async(
            "auth",
            "10.0.0.6",
            limit=1,
            backend="redis",
            redis_url="redis://localhost:6379/0",
        )
        assert ok1 is True
        assert ok2 is False
        assert retry2 >= 1
