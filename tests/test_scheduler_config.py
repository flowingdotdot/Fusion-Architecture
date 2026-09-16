"""SchedulerRuntimeApp's config Stage/Apply (doc section 15): Scheduler has no
Targets of its own, so DeviceRuntimeApp's default "any Target busy" apply_guard
doesn't fit it -- its guard checks Show state instead, and only allows Apply
while IDLE (doc: "Apply는 Show 정지... 등 적용 조건을 검사한다. 실행 중이면 staged
상태로 두고 적용 불가 이유를 반환한다").
"""

from __future__ import annotations

from typing import Any

from fusion.apps.scheduler.runtime import SchedulerRuntimeApp
from fusion.contracts.config import ApplyOutcome
from fusion.contracts.timeline import Cue, Timeline
from fusion.core.show_controller import ShowState
from fusion.simulation.fake_clock import FakeClock


class FakeRuntimeClient:
    def __init__(self) -> None:
        self._session_counter = 0

    async def get_info(self) -> dict[str, Any]:
        return {"runtime_id": "fake-motor"}

    async def acquire_session(self, actor: str, mode: str = "SHOW") -> dict[str, Any]:
        self._session_counter += 1
        return {"session_id": f"sess-{self._session_counter}"}

    async def get_targets(self) -> list[dict[str, Any]]:
        return []

    async def release_session(self, session_id: str) -> None:
        return None


def make_scheduler() -> SchedulerRuntimeApp:
    clock = FakeClock()
    scheduler = SchedulerRuntimeApp({"motor": FakeRuntimeClient()}, clock)
    scheduler.load_timeline(
        Timeline(
            cues=[
                Cue(
                    cue_id="cue-1",
                    at_ms=0,
                    runtime="motor",
                    target_id="t1",
                    action="move",
                    params={"position": 1},
                )
            ]
        )
    )
    return scheduler


async def test_apply_succeeds_while_show_is_idle() -> None:
    scheduler = make_scheduler()
    assert scheduler.controller.state == ShowState.IDLE

    revision = scheduler.stage_config("show", {"timeline": "v1"})
    record = await scheduler.apply_config(revision.revision_id)

    assert record.outcome == ApplyOutcome.SUCCEEDED
    assert scheduler.get_config_status()["active_revision_id"] == revision.revision_id


async def test_apply_is_rejected_while_show_is_armed() -> None:
    scheduler = make_scheduler()
    await scheduler.arm()
    assert scheduler.controller.state == ShowState.ARMED

    revision = scheduler.stage_config("show", {"timeline": "v2"})
    record = await scheduler.apply_config(revision.revision_id)

    assert record.outcome == ApplyOutcome.REJECTED
    assert "not idle" in (record.detail or "")
    assert scheduler.get_config_status()["active_revision_id"] is None


async def test_stage_is_allowed_even_while_armed() -> None:
    scheduler = make_scheduler()
    await scheduler.arm()

    revision = scheduler.stage_config("show", {"timeline": "v3"})
    assert revision.revision_id in scheduler.get_config_status()["staged_revision_ids"]
