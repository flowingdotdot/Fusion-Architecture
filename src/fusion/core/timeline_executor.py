"""Timeline executor (doc section 11): fires each Cue at ``start + at_ms``, gated by
its dependencies' outcomes, against whichever Runtime client the Cue names. Runs all
Cues concurrently (dependencies impose ordering where declared) rather than one at a
time, since doc section 11 treats ``at_ms`` scheduling and dependency waits as
orthogonal, both feeding the same submit/conflict policy.

One event-listening task per distinct Runtime client multiplexes
``command.completed`` by command_id to whichever Cue is waiting on it, rather than
each Cue opening its own WebSocket connection (doc section 6: reuse connections).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from fusion.contracts.timeline import Cue, Timeline
from fusion.core.clock import Clock
from fusion.transport.client import ResyncRequired, RuntimeClient, RuntimeCommandError


@dataclass
class CueOutcome:
    cue_id: str
    outcome: str
    detail: str | None = None


class TimelineExecutor:
    def __init__(
        self,
        timeline: Timeline,
        clients: Mapping[str, RuntimeClient],
        clock: Clock,
        *,
        run_id: str,
    ) -> None:
        self._timeline = timeline
        self._clients = clients
        self._clock = clock
        self._run_id = run_id
        self.outcomes: dict[str, CueOutcome] = {}
        self._done_events: dict[str, asyncio.Event] = {
            cue.cue_id: asyncio.Event() for cue in timeline.cues
        }
        self._pending: dict[str, asyncio.Future[dict[str, object]]] = {}

    async def run(
        self, *, session_ids: Mapping[str, str], hold_check: Callable[[], bool]
    ) -> dict[str, CueOutcome]:
        start = self._clock.now()

        after_sequences: dict[str, int] = {}
        for name, client in self._clients.items():
            snapshot = await client.get_snapshot()
            after_sequences[name] = snapshot["sequence"]

        listeners = [
            asyncio.create_task(self._listen(client, after_sequences[name]))
            for name, client in self._clients.items()
        ]
        try:
            await asyncio.gather(
                *(self._run_cue(cue, start, session_ids, hold_check) for cue in self._timeline.cues)
            )
        finally:
            for task in listeners:
                task.cancel()
            await asyncio.gather(*listeners, return_exceptions=True)
        return self.outcomes

    async def _listen(self, client: RuntimeClient, after_sequence: int) -> None:
        events = client.events(after_sequence)
        try:
            async for event in events:
                if event.get("type") != "command.completed":
                    continue
                future = self._pending.get(event.get("command_id", ""))
                if future is not None and not future.done():
                    future.set_result(event["payload"])
        except ResyncRequired:
            # Can't confirm what happened to anything still in flight on this client.
            for future in self._pending.values():
                if not future.done():
                    future.set_result({"outcome": "UNKNOWN"})
        finally:
            await events.aclose()

    async def _run_cue(
        self, cue: Cue, start: float, session_ids: Mapping[str, str], hold_check: Callable[[], bool]
    ) -> None:
        for dep in cue.depends_on:
            await self._done_events[dep].wait()
            dep_outcome = self.outcomes.get(dep)
            if dep_outcome is None or dep_outcome.outcome != "SUCCEEDED":
                self._finish(cue, "SKIPPED", f"dependency '{dep}' did not succeed")
                return

        deadline = start + cue.at_ms / 1000
        while self._clock.now() < deadline:
            await self._clock.sleep(min(0.02, deadline - self._clock.now()))

        lateness_ms = (self._clock.now() - deadline) * 1000
        if lateness_ms > cue.late_policy.max_lateness_ms:
            if cue.late_policy.mode == "skip":
                self._finish(cue, "SKIPPED", f"late by {lateness_ms:.0f}ms")
                return
            self._finish(cue, "FAILED", f"late by {lateness_ms:.0f}ms (abort policy)")
            raise RuntimeError(f"cue '{cue.cue_id}' exceeded its late policy (abort)")

        if hold_check():
            self._finish(cue, "SKIPPED", "Show was held before this cue could fire")
            return

        client = self._clients[cue.runtime]
        try:
            submitted = await client.submit_command(
                request_id=f"{self._run_id}:{cue.cue_id}",
                control_session_id=session_ids[cue.runtime],
                run_id=self._run_id,
                cue_id=cue.cue_id,
                target_id=cue.target_id,
                action=cue.action,
                params=cue.params,
                completion_requirement=cue.completion_requirement.value,
                source="timeline",
            )
        except RuntimeCommandError as exc:
            self._finish(cue, "FAILED", str(exc))
            return

        if submitted["status"] == "TERMINAL":
            self._finish(cue, submitted["outcome"], None)
            return

        future: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
        self._pending[submitted["command_id"]] = future

        # A command that completes essentially instantly (e.g. a Fake move whose
        # target equals its current position) can have its completion Event
        # published -- and missed by _listen, since interest in this command_id
        # could only be registered after this HTTP round trip returned -- before
        # we ever get here. Re-check the command's own record and resolve
        # locally rather than hang forever waiting for an Event that already
        # came and went; re-check "not done" after the await in case _listen won
        # the race in the meantime.
        if not future.done():
            record = await client.get_command(submitted["command_id"])
            if not future.done() and record["status"] == "TERMINAL":
                future.set_result({"outcome": record["outcome"]})

        payload = await future
        self._finish(cue, str(payload.get("outcome", "UNKNOWN")), None)

    def _finish(self, cue: Cue, outcome: str, detail: str | None) -> None:
        self.outcomes[cue.cue_id] = CueOutcome(cue.cue_id, outcome, detail)
        self._done_events[cue.cue_id].set()
