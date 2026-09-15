"""Headless Scheduler (doc section 11, stage 3): owns the Show state machine, a
single Timeline, and a TriggerEngine reacting to (Fake, for now) input Events -- the
smallest real version of "Timeline은 Cue를 제출하고 State Machine은 Event·조건으로
전이 및 Action을 요청한다" from doc section 11, still with only one Runtime
(Motor) to talk to and no Studio Timeline-editing UI.

No PySide6 import here: a Scheduler UI is allowed to embed this class directly in
its own process (doc section 4 explicitly permits one launcher starting UI+Runtime
together), but this module itself stays UI-framework-free either way.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from fusion.contracts.ids import new_id
from fusion.contracts.timeline import Timeline, validate_timeline
from fusion.contracts.trigger import InputEvent, Trigger
from fusion.core.clock import Clock
from fusion.core.show_controller import ShowController, ShowState
from fusion.core.timeline_executor import CueOutcome, TimelineExecutor
from fusion.core.trigger_engine import ShowActions, TriggerEngine
from fusion.transport.client import RuntimeClient

logger = logging.getLogger("fusion.scheduler")


class SchedulerRuntimeApp:
    def __init__(
        self, clients: Mapping[str, RuntimeClient], clock: Clock, actor: str = "scheduler"
    ) -> None:
        self._clients = clients
        self._clock = clock
        self._actor = actor
        self._timeline: Timeline | None = None
        self._session_ids: dict[str, str] = {}
        self.last_outcomes: dict[str, CueOutcome] = {}

        self.controller = ShowController(on_state_change=self._on_state_change)
        self.triggers = TriggerEngine(
            self.controller,
            ShowActions(
                start=self._trigger_start, hold=self._trigger_hold, abort=self._trigger_abort
            ),
            clock,
        )

    def register_trigger(self, trigger: Trigger) -> None:
        self.triggers.register(trigger)

    async def fire_input(self, event: InputEvent) -> None:
        await self.triggers.handle(event)

    def load_timeline(self, timeline: Timeline) -> None:
        errors = validate_timeline(timeline)
        if errors:
            raise ValueError("invalid timeline: " + "; ".join(errors))
        self._timeline = timeline

    def status(self) -> dict[str, object]:
        return {
            **self.controller.status(),
            "cue_outcomes": {cid: outcome.outcome for cid, outcome in self.last_outcomes.items()},
        }

    # ---- Show lifecycle ----

    async def arm(self) -> None:
        await self.controller.arm(self._prepare)

    async def _prepare(self) -> None:
        if self._timeline is None:
            raise RuntimeError("no timeline loaded")
        for name, client in self._clients.items():
            await client.get_info()  # confirms the Runtime is actually reachable
            session = await client.acquire_session(self._actor, mode="SHOW")
            self._session_ids[name] = session["session_id"]

    def start(self) -> None:
        run_id = new_id("run")
        self.controller.start(self._run, run_id=run_id)

    def hold(self) -> None:
        self.controller.hold()

    async def abort(self) -> None:
        await self.controller.abort(self._stop_all)

    async def _run(self) -> None:
        assert self._timeline is not None
        assert self.controller.run_id is not None
        executor = TimelineExecutor(
            self._timeline, self._clients, self._clock, run_id=self.controller.run_id
        )
        self.last_outcomes = await executor.run(
            session_ids=self._session_ids, hold_check=lambda: self.controller.hold_requested
        )
        failed = {
            cid: o
            for cid, o in self.last_outcomes.items()
            if o.outcome not in ("SUCCEEDED", "SKIPPED")
        }
        if failed:
            raise RuntimeError(f"{len(failed)} cue(s) did not succeed: {failed}")

    async def _stop_all(self) -> None:
        for name, client in self._clients.items():
            session_id = self._session_ids.get(name)
            if session_id is None:
                continue
            try:
                targets = await client.get_targets()
            except Exception:  # noqa: BLE001 - best-effort stop; unreachable Runtime can't be stopped remotely anyway
                continue
            for target in targets:
                stop_action = next(
                    (a for a in target["actions"] if a["priority"] == "control"), None
                )
                if stop_action is None:
                    continue
                try:
                    await client.submit_command(
                        request_id=new_id("abort"),
                        control_session_id=session_id,
                        target_id=target["target_id"],
                        action=stop_action["name"],
                        completion_requirement="acknowledged",
                        source="show-abort",
                    )
                except Exception:  # noqa: BLE001 - best-effort: doc section 10, a software stop isn't the only safety net
                    logger.warning("abort: stop request to %s/%s failed", name, target["target_id"])

        for name in list(self._session_ids):
            client = self._clients[name]
            session_id = self._session_ids.pop(name)
            try:
                await client.release_session(session_id)
            except Exception:  # noqa: BLE001 - releasing a session on an already-unreachable Runtime is not fatal
                pass

    # ---- Trigger action bindings ----

    def _trigger_start(self) -> None:
        self.start()

    def _trigger_hold(self) -> None:
        self.hold()

    async def _trigger_abort(self) -> None:
        await self.abort()

    def _on_state_change(self, state: ShowState) -> None:
        logger.info("show state -> %s run_id=%s", state.value, self.controller.run_id)
