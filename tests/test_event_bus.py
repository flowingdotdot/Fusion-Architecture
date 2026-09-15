"""Event bus replay/resync and bounded-subscriber-queue behavior (doc section 8/10)."""

from __future__ import annotations

from fusion.core.event_bus import EventBus


def test_replay_since_within_buffer_returns_only_newer_events() -> None:
    bus = EventBus("r1", "boot1", replay_buffer_size=5)
    for i in range(3):
        bus.publish("tick", payload={"i": i})

    replayed = bus.replay_since(1)
    assert replayed is not None
    assert [e.sequence for e in replayed] == [2, 3]


def test_replay_since_already_current_returns_empty() -> None:
    bus = EventBus("r1", "boot1")
    bus.publish("tick")
    bus.publish("tick")
    assert bus.replay_since(bus.sequence) == []


def test_replay_since_out_of_range_requires_resync() -> None:
    bus = EventBus("r1", "boot1", replay_buffer_size=3)
    for i in range(10):
        bus.publish("tick", payload={"i": i})
    assert bus.replay_since(0) is None


async def test_subscriber_overflow_forces_resync_instead_of_silent_drop() -> None:
    bus = EventBus("r1", "boot1", subscriber_queue_size=2)
    _sub_id, queue = bus.subscribe()
    for i in range(5):
        bus.publish("tick", payload={"i": i})

    received = []
    while True:
        item = await queue.get()
        if item is None:
            break
        received.append(item)

    assert len(received) <= 2  # some events were not silently absorbed
    assert item is None  # the reader is explicitly told to resync, not left hanging
