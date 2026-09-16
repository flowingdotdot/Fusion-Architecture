"""SettingCoordinator (doc section 15): each Runtime's Stage/Apply is attempted
independently; ``Deployment.overall`` summarizes STAGED/SUCCEEDED/PARTIAL/FAILED.
"""

from __future__ import annotations

from typing import Any

from fusion.apps.setting.coordinator import SettingCoordinator
from fusion.simulation.fake_clock import FakeClock


class FakeDeployClient:
    def __init__(self, *, stage_error: str | None = None, apply_outcome: str = "SUCCEEDED") -> None:
        self._stage_error = stage_error
        self._apply_outcome = apply_outcome
        self.staged_content: dict[str, Any] | None = None
        self.applied_revision_id: str | None = None

    async def stage_config(self, kind: str, content: dict[str, Any]) -> dict[str, Any]:
        if self._stage_error:
            raise RuntimeError(self._stage_error)
        self.staged_content = content
        return {"revision_id": "rev-1", "kind": kind, "content": content}

    async def apply_config(
        self, revision_id: str, expected_active_revision: str | None = None
    ) -> dict[str, Any]:
        self.applied_revision_id = revision_id
        return {"revision_id": revision_id, "outcome": self._apply_outcome, "detail": None}


async def test_stage_only_deployment_is_staged_overall() -> None:
    motor = FakeDeployClient()
    coordinator = SettingCoordinator({"motor": motor}, FakeClock())  # type: ignore[arg-type]

    deployment = await coordinator.deploy("install", {"a": 1}, apply=False)
    assert deployment.overall == "STAGED"
    assert motor.staged_content == {"a": 1}
    assert motor.applied_revision_id is None


async def test_all_succeed_is_succeeded_overall() -> None:
    motor = FakeDeployClient(apply_outcome="SUCCEEDED")
    video = FakeDeployClient(apply_outcome="SUCCEEDED")
    coordinator = SettingCoordinator({"motor": motor, "video": video}, FakeClock())  # type: ignore[arg-type]

    deployment = await coordinator.deploy("install", {"a": 1}, apply=True)
    assert deployment.overall == "SUCCEEDED"


async def test_one_rejected_one_succeeded_is_partial() -> None:
    motor = FakeDeployClient(apply_outcome="SUCCEEDED")
    video = FakeDeployClient(apply_outcome="REJECTED")
    coordinator = SettingCoordinator({"motor": motor, "video": video}, FakeClock())  # type: ignore[arg-type]

    deployment = await coordinator.deploy("install", {"a": 1}, apply=True)
    assert deployment.overall == "PARTIAL"


async def test_a_runtime_that_cannot_even_stage_makes_the_whole_deployment_failed() -> None:
    motor = FakeDeployClient(stage_error="connection refused")
    video = FakeDeployClient(apply_outcome="SUCCEEDED")
    coordinator = SettingCoordinator({"motor": motor, "video": video}, FakeClock())  # type: ignore[arg-type]

    deployment = await coordinator.deploy("install", {"a": 1}, apply=True)
    assert deployment.overall == "FAILED"
    assert deployment.results["motor"].staged is False
    assert deployment.results["motor"].stage_error == "connection refused"
    # video was still attempted independently, not skipped just because motor failed
    assert deployment.results["video"].staged is True
    assert deployment.results["video"].apply_outcome == "SUCCEEDED"


async def test_all_rejected_is_failed_overall() -> None:
    motor = FakeDeployClient(apply_outcome="REJECTED")
    coordinator = SettingCoordinator({"motor": motor}, FakeClock())  # type: ignore[arg-type]

    deployment = await coordinator.deploy("install", {"a": 1}, apply=True)
    assert deployment.overall == "FAILED"
