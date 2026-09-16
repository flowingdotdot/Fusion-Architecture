"""Setting's group deployment coordinator (doc section 15): pushes one config
payload to several Runtimes' own Stage/Apply endpoints -- the MQTT Adapter a real
Setting app would also expose calls into this exact same coordinator (doc: "MQTT
전용 우회 Apply 경로를 만들지 않는다"), it isn't a separate code path.

Each Runtime's Stage/Apply is attempted independently: one Runtime failing to
Stage or being REJECTED on Apply does not stop the others from being attempted,
matching doc's "일부 앱만 적용 성공" scenario -- the caller reads ``Deployment.overall``
to decide whether a Show may start.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fusion.contracts.deployment import Deployment, RuntimeDeploymentResult
from fusion.contracts.ids import new_id
from fusion.core.clock import Clock
from fusion.transport.client import RuntimeClient


class SettingCoordinator:
    def __init__(self, clients: Mapping[str, RuntimeClient], clock: Clock) -> None:
        self._clients = clients
        self._clock = clock

    async def deploy(
        self, kind: str, content: dict[str, Any], *, apply: bool = False
    ) -> Deployment:
        results: dict[str, RuntimeDeploymentResult] = {}
        for name, client in self._clients.items():
            results[name] = await self._deploy_one(name, client, kind, content, apply=apply)
        return Deployment(
            deployment_id=new_id("deploy"), kind=kind, created_at=self._clock.now(), results=results
        )

    async def _deploy_one(
        self, name: str, client: RuntimeClient, kind: str, content: dict[str, Any], *, apply: bool
    ) -> RuntimeDeploymentResult:
        try:
            staged = await client.stage_config(kind, content)
        except Exception as exc:  # noqa: BLE001 - an unreachable/erroring Runtime is a per-Runtime FAILED result, not a crash of the whole deployment
            return RuntimeDeploymentResult(runtime_name=name, staged=False, stage_error=str(exc))

        result = RuntimeDeploymentResult(
            runtime_name=name, staged=True, revision_id=staged["revision_id"]
        )
        if not apply:
            return result
        try:
            applied = await client.apply_config(staged["revision_id"])
            result.apply_outcome = applied["outcome"]
            result.apply_detail = applied.get("detail")
        except Exception as exc:  # noqa: BLE001 - same reasoning as the Stage step above
            result.apply_outcome = "FAILED"
            result.apply_detail = str(exc)
        return result
