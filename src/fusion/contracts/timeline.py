"""Timeline/Cue contract (doc section 11): a Cue is one scheduled command request.

Kept intentionally simple for stage 3 -- no scrub/seek, no per-Cue retry, no nested
Timelines. ``validate_timeline`` only checks structure (doc section 11: "dependency
cycle-누락-미지원 완료 수준은 검증 오류다"); it does not know whether a target/action
actually exists, since that depends on Runtime info fetched at Show-arm time.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from fusion.contracts.command import CompletionRequirement

LatePolicyMode = Literal["skip", "abort"]


DEFAULT_MAX_LATENESS_MS = 500
"""A default of 0 would make every Cue spuriously late: normal asyncio scheduling,
one dependency check, and the HTTP round trip to submit a command all take a few
milliseconds even under FakeClock-free real execution. 500ms is slack for that
overhead on a single local-network Show, not a validated field tolerance -- doc
section 20 item 3 calls out timing tolerances as something to collect from real
requirements, not invent."""


class LatePolicy(BaseModel):
    max_lateness_ms: int = DEFAULT_MAX_LATENESS_MS
    """How late (past ``at_ms``, after dependencies are satisfied) a Cue may still
    fire before ``mode`` applies."""
    mode: LatePolicyMode = "skip"
    """What happens once a Cue is later than max_lateness_ms allows: "skip" leaves
    it un-fired (dependents waiting on it never resolve and will themselves be
    skipped/aborted by their own late policy or a dependency-failure); "abort" faults
    the whole Show."""


class Cue(BaseModel):
    cue_id: str
    at_ms: int
    """Offset from Timeline start. Not a wall-clock deadline -- computed from the
    Timeline's own local monotonic start each time, so repeated polling never
    accumulates drift (doc section 11)."""
    runtime: str
    """Which Runtime client (by the Scheduler's own local name, e.g. "motor") this
    Cue targets -- not the remote runtime_id, which the Scheduler learns from that
    Runtime's own /api/v1/info."""
    target_id: str
    action: str
    params: dict[str, object] = Field(default_factory=dict)
    completion_requirement: CompletionRequirement = CompletionRequirement.OBSERVED
    depends_on: list[str] = Field(default_factory=list)
    """cue_id values that must reach SUCCEEDED (at this Cue's completion_requirement
    or better) before this Cue is eligible to fire, regardless of late_policy.mode --
    a dependency is a hard wait, not a scheduling preference (doc section 11)."""
    late_policy: LatePolicy = Field(default_factory=LatePolicy)


class Timeline(BaseModel):
    cues: list[Cue]

    def get_cue(self, cue_id: str) -> Cue | None:
        return next((c for c in self.cues if c.cue_id == cue_id), None)


def validate_timeline(timeline: Timeline) -> list[str]:
    """Returns a list of human-readable structural errors (empty if valid): duplicate
    cue_id, a depends_on referencing a cue_id that doesn't exist, or a dependency
    cycle."""
    errors: list[str] = []

    seen: set[str] = set()
    for cue in timeline.cues:
        if cue.cue_id in seen:
            errors.append(f"duplicate cue_id '{cue.cue_id}'")
        seen.add(cue.cue_id)

    known_ids = {cue.cue_id for cue in timeline.cues}
    for cue in timeline.cues:
        for dep in cue.depends_on:
            if dep not in known_ids:
                errors.append(f"cue '{cue.cue_id}' depends_on unknown cue '{dep}'")

    errors.extend(_find_cycles(timeline))
    return errors


def _find_cycles(timeline: Timeline) -> list[str]:
    graph = {cue.cue_id: cue.depends_on for cue in timeline.cues}
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = dict.fromkeys(graph, WHITE)
    errors: list[str] = []

    def visit(node: str, path: list[str]) -> None:
        if node not in graph:
            return  # unknown-dependency error is already reported separately
        color[node] = GRAY
        for dep in graph[node]:
            if color.get(dep) == GRAY:
                cycle = " -> ".join([*path, node, dep])
                errors.append(f"dependency cycle: {cycle}")
            elif color.get(dep) == WHITE:
                visit(dep, [*path, node])
        color[node] = BLACK

    for cue_id in graph:
        if color[cue_id] == WHITE:
            visit(cue_id, [])
    return errors
