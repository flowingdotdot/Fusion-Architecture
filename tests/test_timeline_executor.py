"""TimelineExecutor (doc section 11): dependencies are a hard wait regardless of
schedule, a failed dependency skips its dependent, and a Cue that becomes late while
waiting on a dependency is skipped/aborted per its own late_policy. Uses a small
in-process fake RuntimeClient double (no real HTTP) so timing is entirely FakeClock-
driven and deterministic.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from fusion.contracts.timeline import Cue, LatePolicy, Timeline
from fusion.core.timeline_executor import TimelineExecutor
from fusion.simulation.fake_clock import FakeClock


class FakeRuntimeClient:
    def __init__(self) -> None:
        self.sequence = 0
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.submitted: list[dict[str, Any]] = []

    async def get_snapshot(self) -> dict[str, Any]:
        return {"sequence": self.sequence}

    async def get_targets(self) -> list[dict[str, Any]]:
        return []

    async def submit_command(self, **kwargs: Any) -> dict[str, Any]:
        self.submitted.append(kwargs)
        return {
            "command_id": f"cmd-for-{kwargs['request_id']}",
            "status": "OBSERVING",
            "outcome": None,
        }

    async def complete(self, request_id: str, outcome: str = "SUCCEEDED") -> None:
        self.sequence += 1
        await self._queue.put(
            {
                "type": "command.completed",
                "command_id": f"cmd-for-{request_id}",
                "payload": {"outcome": outcome},
            }
        )

    async def events(self, after_sequence: int) -> AsyncGenerator[dict[str, Any]]:
        while True:
            yield await self._queue.get()


def cue(cue_id: str, **kwargs: Any) -> Cue:
    return Cue(cue_id=cue_id, at_ms=0, runtime="motor", target_id="t1", action="move", **kwargs)


async def pump_until(condition: Any, tries: int = 50) -> None:
    """Runs the event loop forward one tick at a time until ``condition()`` is true.
    A single ``await asyncio.sleep(0)`` only unwinds one level of nested
    ``create_task`` (e.g. the run_task's own first step); getting all the way down to
    a Cue sub-task's first real await (its ``submit_command`` call) takes another
    hop, so a bounded poll -- not a fixed sleep(0) count -- is the robust way to wait
    for it here."""
    for _ in range(tries):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition was never met")


async def test_dependent_cue_waits_for_its_dependency() -> None:
    clock = FakeClock()
    client = FakeRuntimeClient()
    timeline = Timeline(cues=[cue("a"), cue("b", depends_on=["a"])])
    executor = TimelineExecutor(timeline, {"motor": client}, clock, run_id="run-1")

    run_task = asyncio.create_task(
        executor.run(session_ids={"motor": "sess"}, hold_check=lambda: False)
    )
    await pump_until(lambda: len(client.submitted) == 1)
    assert [s["cue_id"] for s in client.submitted] == ["a"]

    await client.complete("run-1:a", "SUCCEEDED")
    await pump_until(lambda: len(client.submitted) == 2)
    assert [s["cue_id"] for s in client.submitted] == ["a", "b"]

    await client.complete("run-1:b", "SUCCEEDED")
    outcomes = await run_task
    assert outcomes["a"].outcome == "SUCCEEDED"
    assert outcomes["b"].outcome == "SUCCEEDED"


async def test_failed_dependency_skips_the_dependent_without_submitting_it() -> None:
    clock = FakeClock()
    client = FakeRuntimeClient()
    timeline = Timeline(cues=[cue("a"), cue("b", depends_on=["a"])])
    executor = TimelineExecutor(timeline, {"motor": client}, clock, run_id="run-1")

    run_task = asyncio.create_task(
        executor.run(session_ids={"motor": "sess"}, hold_check=lambda: False)
    )
    await pump_until(lambda: len(client.submitted) == 1)
    await client.complete("run-1:a", "FAILED")

    outcomes = await run_task
    assert outcomes["a"].outcome == "FAILED"
    assert outcomes["b"].outcome == "SKIPPED"
    assert [s["cue_id"] for s in client.submitted] == ["a"]


async def test_held_show_skips_cues_not_yet_fired() -> None:
    clock = FakeClock()
    client = FakeRuntimeClient()
    timeline = Timeline(cues=[cue("a")])
    executor = TimelineExecutor(timeline, {"motor": client}, clock, run_id="run-1")

    outcomes = await executor.run(session_ids={"motor": "sess"}, hold_check=lambda: True)
    assert outcomes["a"].outcome == "SKIPPED"
    assert client.submitted == []


async def test_cue_late_past_a_slow_dependency_is_skipped_by_default() -> None:
    clock = FakeClock()
    client = FakeRuntimeClient()
    timeline = Timeline(
        cues=[
            cue("a"),
            cue("b", depends_on=["a"], late_policy=LatePolicy(max_lateness_ms=100, mode="skip")),
        ]
    )
    executor = TimelineExecutor(timeline, {"motor": client}, clock, run_id="run-1")

    run_task = asyncio.create_task(
        executor.run(session_ids={"motor": "sess"}, hold_check=lambda: False)
    )
    await pump_until(lambda: len(client.submitted) == 1)
    assert [s["cue_id"] for s in client.submitted] == ["a"]  # "b" is waiting, not skipped-yet

    await clock.advance(5.0)  # "a" takes a very long time in this simulated world
    await client.complete("run-1:a", "SUCCEEDED")

    outcomes = await run_task
    assert outcomes["a"].outcome == "SUCCEEDED"
    assert outcomes["b"].outcome == "SKIPPED"
    assert [s["cue_id"] for s in client.submitted] == ["a"]  # "b" never actually submitted


async def test_cue_late_past_a_slow_dependency_can_abort_the_show() -> None:
    clock = FakeClock()
    client = FakeRuntimeClient()
    timeline = Timeline(
        cues=[
            cue("a"),
            cue("b", depends_on=["a"], late_policy=LatePolicy(max_lateness_ms=100, mode="abort")),
        ]
    )
    executor = TimelineExecutor(timeline, {"motor": client}, clock, run_id="run-1")

    run_task = asyncio.create_task(
        executor.run(session_ids={"motor": "sess"}, hold_check=lambda: False)
    )
    await pump_until(lambda: len(client.submitted) == 1)
    await clock.advance(5.0)
    await client.complete("run-1:a", "SUCCEEDED")

    with pytest.raises(RuntimeError, match="late policy"):
        await run_task
