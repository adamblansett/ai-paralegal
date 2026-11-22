import asyncio
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Any


class MatterEventBus:
    """In-process async pub/sub event bus powering live Server-Sent Events (SSE) per matter."""

    def __init__(self, max_queue_size: int = 128) -> None:
        self._max_queue_size = max_queue_size
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)

    def publish(
        self,
        matter_id: str | uuid.UUID,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        key = str(matter_id)
        subscribers = self._subscribers.get(key)
        if not subscribers:
            return

        event = {
            "event": event_type,
            "data": {
                **payload,
                "matter_id": key,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        }
        for queue in list(subscribers):
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    @asynccontextmanager
    async def subscribe(
        self, matter_id: str | uuid.UUID
    ) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        key = str(matter_id)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._max_queue_size)
        self._subscribers[key].add(queue)
        try:
            yield queue
        finally:
            subs = self._subscribers.get(key)
            if subs is not None:
                subs.discard(queue)
                if not subs:
                    self._subscribers.pop(key, None)

    def subscriber_count(self, matter_id: str | uuid.UUID) -> int:
        return len(self._subscribers.get(str(matter_id), ()))


event_bus = MatterEventBus()
