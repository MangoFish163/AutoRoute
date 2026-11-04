"""Bounded in-process invalidation; database remains the source of truth."""

import asyncio
import threading
from contextlib import asynccontextmanager


class EventBus:
    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers = set()

    @asynccontextmanager
    async def subscribe(self):
        queue = asyncio.Queue(maxsize=1)
        item = (asyncio.get_running_loop(), queue)
        with self._lock:
            self._subscribers.add(item)
        try:
            yield queue
        finally:
            with self._lock:
                self._subscribers.discard(item)

    def publish(self, created_at):
        with self._lock:
            subscribers = tuple(self._subscribers)
        for loop, queue in subscribers:

            def notify(q=queue):
                low = high = created_at
                if q.full():
                    previous_low, previous_high = q.get_nowait()
                    low, high = min(low, previous_low), max(high, previous_high)
                q.put_nowait((low, high))

            try:
                loop.call_soon_threadsafe(notify)
            except RuntimeError:
                pass


bus = EventBus()
