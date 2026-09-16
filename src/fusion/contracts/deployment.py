"""Group deployment contract (doc section 15): "여러 앱 배포는 deployment_id 아래
Runtime별 staged/active revision과 결과를 기록한다... 일부 앱만 적용 성공하면
PARTIAL/FAILED로 표시하고 Show 시작을 차단한다."
"""

from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, Field

DeploymentOverall = Literal["STAGED", "SUCCEEDED", "PARTIAL", "FAILED"]


class RuntimeDeploymentResult(BaseModel):
    runtime_name: str
    staged: bool
    revision_id: str | None = None
    stage_error: str | None = None
    apply_outcome: str | None = None
    """None means Apply was never attempted for this Runtime (a Stage-only
    deployment, or Stage itself already failed)."""
    apply_detail: str | None = None


class Deployment(BaseModel):
    deployment_id: str
    kind: str
    created_at: float = Field(default_factory=time.time)
    results: dict[str, RuntimeDeploymentResult]

    @property
    def overall(self) -> DeploymentOverall:
        if any(not r.staged for r in self.results.values()):
            return "FAILED"
        attempted = [r.apply_outcome for r in self.results.values() if r.apply_outcome is not None]
        if not attempted:
            return "STAGED"
        if all(outcome == "SUCCEEDED" for outcome in attempted):
            return "SUCCEEDED"
        if any(outcome == "SUCCEEDED" for outcome in attempted):
            return "PARTIAL"
        return "FAILED"
