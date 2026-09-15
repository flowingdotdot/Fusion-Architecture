"""Event bus (doc section 8): sequence-ordered publish, bounded replay buffer, bounded
per-subscriber queues. A subscriber that falls behind is force-closed (sentinel
``None``) rather than silently dropping events -- the reader must resync from a fresh
snapshot instead of missing important Events quietly.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque

from fusion.contracts.event import Event
from fusion.contracts.ids import new_event_id

DEFAULT_REPLAY_BUFFER_SIZE = 500
DEFAULT_SUBSCRIBER_QUEUE_SIZE = 200


class EventBus:
    def __init__(
        self,
        runtime_id: str,
        runtime_boot_id: str,
        *,
        replay_buffer_size: int = DEFAULT_REPLAY_BUFFER_SIZE,
        subscriber_queue_size: int = DEFAULT_SUBSCRIBER_QUEUE_SIZE,
    ) -> None:
        self.runtime_id = runtime_id
        self.runtime_boot_id = runtime_boot_id
        self._sequence = 0
        self._buffer: deque[Event] = deque(maxlen=replay_buffer_size)
        self._subscribers: dict[int, asyncio.Queue[Event | None]] = {}
        self._next_sub_id = 0
        self._subscriber_queue_size = subscriber_queue_size

    @property
    def sequence(self) -> int:
        return self._sequence

    def publish(
        self,
        event_type: str,
        *,
        target_id: str | None = None,
        command_id: str | None = None,
        run_id: str | None = None,
        cue_id: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> Event:
        self._sequence += 1
        event = Event(
            event_id=new_event_id(),
            type=event_type,
            runtime_id=self.runtime_id,
            runtime_boot_id=self.runtime_boot_id,
            sequence=self._sequence,
            target_id=target_id,
            command_id=command_id,
            run_id=run_id,
            cue_id=cue_id,
            occurred_at=time.time(),
            payload=payload or {},
        )
        self._buffer.append(event)

        overflowed: list[int] = []
        for sub_id, queue in self._subscribers.items():
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                overflowed.append(sub_id)
        for sub_id in overflowed:
            queue = self._subscribers.pop(sub_id)
            try:
                queue.get_nowait()  # make room: the sentinel matters more than one more event
            except asyncio.QueueEmpty:
                pass
            queue.put_nowait(None)  # force the reader to stop and resync
        return event

    def replay_since(self, after_sequence: int) -> list[Event] | None:
        """Events with sequence > after_sequence, or None if the buffer can't cover
        the gap (caller must resync from a fresh snapshot)."""
        if after_sequence == self._sequence:
            return []
        if not self._buffer:
            return [] if after_sequence == 0 else None
        oldest = self._buffer[0].sequence
        if after_sequence < oldest - 1:
            return None
        return [e for e in self._buffer if e.sequence > after_sequence]

    def subscribe(self) -> tuple[int, asyncio.Queue[Event | None]]:
        sub_id = self._next_sub_id
        self._next_sub_id += 1
        queue: asyncio.Queue[Event | None] = asyncio.Queue(maxsize=self._subscriber_queue_size)
        self._subscribers[sub_id] = queue
        return sub_id, queue

    def unsubscribe(self, sub_id: int) -> None:
        self._subscribers.pop(sub_id, None)
