"""
Lightweight in-process event bus for SSE streaming.

When a verify_step or log_frame happens via the HTTP API, we publish an
event to any subscribers that have an open /sessions/{id}/events stream.
Subscribers are per-session asyncio.Queue instances; publishers iterate
the subscriber list and put_nowait.

Bounded queues drop oldest events if a slow subscriber falls behind —
better than blocking the publisher.
"""

import asyncio
import json
import logging
import time
from collections import defaultdict
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Max events queued per subscriber before we start dropping
QUEUE_MAXSIZE = 100


class EventBus:
    """Per-session subscription. Thread-safe (uses asyncio.Queue)."""

    def __init__(self) -> None:
        # session_id → list of subscriber queues
        self._subscribers: Dict[str, Set[asyncio.Queue]] = defaultdict(set)
        # Wildcard "*" subscribers receive ALL events regardless of session
        self._wildcard_subs: Set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self, session_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        async with self._lock:
            if session_id == "*":
                self._wildcard_subs.add(q)
            else:
                self._subscribers[session_id].add(q)
        return q

    async def unsubscribe(self, session_id: str, q: asyncio.Queue) -> None:
        async with self._lock:
            if session_id == "*":
                self._wildcard_subs.discard(q)
            else:
                self._subscribers[session_id].discard(q)
                if not self._subscribers[session_id]:
                    del self._subscribers[session_id]

    def publish(self, session_id: Optional[str], event_type: str,
                payload: dict) -> int:
        """Publish to all subscribers of this session + wildcard subscribers.
        Returns count of subscribers reached. Non-blocking — drops events
        for slow consumers rather than waiting."""
        event = {
            "type": event_type,
            "session_id": session_id,
            "ts": time.time(),
            "data": payload,
        }
        reached = 0
        targets: List[asyncio.Queue] = []
        if session_id:
            targets.extend(self._subscribers.get(session_id, ()))
        targets.extend(self._wildcard_subs)
        for q in targets:
            try:
                q.put_nowait(event)
                reached += 1
            except asyncio.QueueFull:
                # Slow consumer — drop. Better than blocking the writer.
                logger.debug("Event queue full for session=%s type=%s; dropping",
                             session_id, event_type)
        return reached

    def subscriber_count(self, session_id: Optional[str] = None) -> int:
        if session_id is None:
            return sum(len(s) for s in self._subscribers.values()) + len(self._wildcard_subs)
        return len(self._subscribers.get(session_id, ())) + len(self._wildcard_subs)


_bus = EventBus()


def get_bus() -> EventBus:
    return _bus


def format_sse(event: dict) -> str:
    """Format a dict as a single SSE message frame."""
    return f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
