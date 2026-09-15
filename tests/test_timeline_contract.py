"""validate_timeline (doc section 11): duplicate cue_id, missing dependency
references, and dependency cycles are structural validation errors.
"""

from __future__ import annotations

from fusion.contracts.timeline import Cue, Timeline, validate_timeline


def cue(cue_id: str, depends_on: list[str] | None = None) -> Cue:
    return Cue(
        cue_id=cue_id,
        at_ms=0,
        runtime="motor",
        target_id="t1",
        action="move",
        depends_on=depends_on or [],
    )


def test_valid_timeline_has_no_errors() -> None:
    timeline = Timeline(cues=[cue("a"), cue("b", depends_on=["a"])])
    assert validate_timeline(timeline) == []


def test_duplicate_cue_id_is_an_error() -> None:
    timeline = Timeline(cues=[cue("a"), cue("a")])
    errors = validate_timeline(timeline)
    assert any("duplicate cue_id" in e for e in errors)


def test_missing_dependency_reference_is_an_error() -> None:
    timeline = Timeline(cues=[cue("a", depends_on=["ghost"])])
    errors = validate_timeline(timeline)
    assert any("unknown cue" in e for e in errors)


def test_dependency_cycle_is_an_error() -> None:
    timeline = Timeline(cues=[cue("a", depends_on=["b"]), cue("b", depends_on=["a"])])
    errors = validate_timeline(timeline)
    assert any("cycle" in e for e in errors)


def test_self_dependency_is_a_cycle() -> None:
    timeline = Timeline(cues=[cue("a", depends_on=["a"])])
    errors = validate_timeline(timeline)
    assert any("cycle" in e for e in errors)
