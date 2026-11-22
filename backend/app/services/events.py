import asyncio
import json
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
import structlog

from app.core.config import Settings, get_settings

logger = structlog.get_logger(__name__)


class MatterEventBus:
    """Async pub/sub event bus with optional Redis Pub/Sub fanout across API & Celery replicas."""

    def __init__(self, max_queue_size: int = 128) -> None:
        self._max_queue_size = max_queue_size
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._origin_id = str(uuid.uuid4())

    @staticmethod
    def _should_use_redis(settings: Settings | None = None) -> bool:
        cfg = settings or get_settings()
        if not cfg.redis_url:
            return False
        if cfg.event_bus_backend == "redis":
            return True
        return cfg.event_bus_backend == "auto" and cfg.task_execution_mode == "celery"

    @staticmethod
    def _channel_name(matter_id: str | uuid.UUID) -> str:
        return f"ai_paralegal:matter_events:{matter_id}"

    async def _publish_redis(self, redis_url: str, channel: str, message: str) -> None:
        client = aioredis.from_url(redis_url, socket_connect_timeout=1.0, socket_timeout=1.0)
        try:
            await client.publish(channel, message)
        except Exception as exc:
            logger.debug("redis_event_publish_skipped", channel=channel, error=str(exc))
        finally:
            await client.aclose()

    def _enqueue_local(self, key: str, event: dict[str, Any]) -> None:
        subscribers = self._subscribers.get(key)
        if not subscribers:
            return
        for queue in list(subscribers):
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    def publish(
        self,
        matter_id: str | uuid.UUID,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        key = str(matter_id)
        event = {
            "event": event_type,
            "data": {
                **payload,
                "matter_id": key,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        }
        self._enqueue_local(key, event)

        cfg = get_settings()
        if self._should_use_redis(cfg):
            envelope = json.dumps(
                {"origin": self._origin_id, **event},
                default=str,
            )
            with suppress(RuntimeError):
                loop = asyncio.get_running_loop()
                loop.create_task(
                    self._publish_redis(cfg.redis_url, self._channel_name(key), envelope)
                )

    @asynccontextmanager
    async def subscribe(
        self, matter_id: str | uuid.UUID
    ) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        key = str(matter_id)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._max_queue_size)
        self._subscribers[key].add(queue)

        redis_task: asyncio.Task[None] | None = None
        cfg = get_settings()
        if self._should_use_redis(cfg):
            redis_task = asyncio.create_task(
                self._listen_redis_channel(cfg.redis_url, self._channel_name(key), queue)
            )

        try:
            yield queue
        finally:
            if redis_task is not None:
                redis_task.cancel()
                with suppress(asyncio.CancelledError):
                    await redis_task
            subs = self._subscribers.get(key)
            if subs is not None:
                subs.discard(queue)
                if not subs:
                    self._subscribers.pop(key, None)

    async def _listen_redis_channel(
        self,
        redis_url: str,
        channel: str,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        client = aioredis.from_url(redis_url, socket_connect_timeout=1.0, socket_timeout=5.0)
        pubsub = client.pubsub()
        try:
            await pubsub.subscribe(channel)
            async for raw in pubsub.listen():
                if raw.get("type") != "message":
                    continue
                payload_raw = raw.get("data")
                if not payload_raw:
                    continue
                try:
                    decoded = json.loads(
                        payload_raw.decode("utf-8")
                        if isinstance(payload_raw, bytes)
                        else str(payload_raw)
                    )
                except Exception:
                    continue
                if decoded.get("origin") == self._origin_id:
                    continue
                evt = {"event": decoded.get("event"), "data": decoded.get("data", {})}
                if queue.full():
                    with suppress(asyncio.QueueEmpty):
                        queue.get_nowait()
                with suppress(asyncio.QueueFull):
                    queue.put_nowait(evt)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("redis_pubsub_listener_closed", channel=channel, error=str(exc))
        finally:
            with suppress(Exception):
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
            with suppress(Exception):
                await client.aclose()

    def subscriber_count(self, matter_id: str | uuid.UUID) -> int:
        return len(self._subscribers.get(str(matter_id), ()))


event_bus = MatterEventBus()
