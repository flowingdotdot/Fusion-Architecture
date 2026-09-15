"""TriggerEngine (doc section 11): registered source/type/comparator/show-state
conditions only, plus debounce and cooldown as the engine's own defense against
repeated physical input.
"""

from __future__ import annotations

from fusion.contracts.trigger import InputEvent, Trigger, TriggerCondition
from fusion.core.show_controller import ShowController, ShowState
from fusion.core.trigger_engine import ShowActions, TriggerEngine
from fusion.simulation.fake_clock import FakeClock


def make_engine() -> tuple[TriggerEngine, ShowController, FakeClock, list[str]]:
    clock = FakeClock()
    controller = ShowController()
    calls: list[str] = []
    actions = ShowActions(
        start=lambda: calls.append("start"),
        hold=lambda: calls.append("hold"),
        abort=lambda: calls.append("abort") or _async_none(),  # type: ignore[func-returns-value]
    )
    engine = TriggerEngine(controller, actions, clock)
    return engine, controller, clock, calls


async def _async_none() -> None:
    return None


async def test_matching_event_fires_the_bound_action() -> None:
    engine, _controller, clock, calls = make_engine()
    engine.register(
        Trigger(
            trigger_id="t1",
            condition=TriggerCondition(source="panel", type="start_button"),
            action="start",
        )
    )

    await engine.handle(
        InputEvent(source="panel", type="start_button", value=True, occurred_at=clock.now())
    )
    assert calls == ["start"]


async def test_non_matching_source_or_type_does_not_fire() -> None:
    engine, _controller, clock, calls = make_engine()
    engine.register(
        Trigger(
            trigger_id="t1",
            condition=TriggerCondition(source="panel", type="start_button"),
            action="start",
        )
    )

    await engine.handle(
        InputEvent(source="other", type="start_button", value=True, occurred_at=clock.now())
    )
    await engine.handle(
        InputEvent(source="panel", type="other_button", value=True, occurred_at=clock.now())
    )
    assert calls == []


async def test_debounce_ignores_rapid_repeats_from_same_source() -> None:
    engine, _controller, clock, calls = make_engine()
    engine.register(
        Trigger(
            trigger_id="t1",
            condition=TriggerCondition(source="panel", type="start_button"),
            action="start",
            debounce_ms=100,
        )
    )

    event = InputEvent(source="panel", type="start_button", value=True, occurred_at=clock.now())
    await engine.handle(event)
    await engine.handle(event)  # same instant -- within debounce window
    assert calls == ["start"]

    await clock.advance(0.2)
    await engine.handle(
        InputEvent(source="panel", type="start_button", value=True, occurred_at=clock.now())
    )
    assert calls == ["start", "start"]


async def test_cooldown_blocks_refiring_even_without_debounce() -> None:
    engine, _controller, clock, calls = make_engine()
    engine.register(
        Trigger(
            trigger_id="t1",
            condition=TriggerCondition(source="panel", type="start_button"),
            action="start",
            cooldown_ms=1000,
        )
    )

    await engine.handle(
        InputEvent(source="panel", type="start_button", value=True, occurred_at=clock.now())
    )
    await clock.advance(0.05)
    await engine.handle(
        InputEvent(source="panel", type="start_button", value=True, occurred_at=clock.now())
    )
    assert calls == ["start"]  # second press within cooldown is ignored

    await clock.advance(2.0)
    await engine.handle(
        InputEvent(source="panel", type="start_button", value=True, occurred_at=clock.now())
    )
    assert calls == ["start", "start"]


async def test_show_state_gate_blocks_action_outside_allowed_states() -> None:
    engine, controller, clock, calls = make_engine()
    engine.register(
        Trigger(
            trigger_id="t1",
            condition=TriggerCondition(source="panel", type="start_button"),
            action="start",
            show_states=[ShowState.ARMED.value],
        )
    )

    assert controller.state == ShowState.IDLE
    await engine.handle(
        InputEvent(source="panel", type="start_button", value=True, occurred_at=clock.now())
    )
    assert calls == []  # IDLE is not in show_states, so the trigger never even calls start()


async def test_action_error_is_reported_not_raised() -> None:
    engine, controller, clock, _calls = make_engine()
    engine.register(
        Trigger(
            trigger_id="t1",
            condition=TriggerCondition(source="panel", type="start_button"),
            action="start",
        )
    )

    # Force the underlying action to fail (e.g. ShowController's own guard rejecting
    # it) -- the action here is a plain lambda from make_engine(), not a real
    # ShowController call, so simulate the failure directly via a raising action.
    controller.state = ShowState.RUNNING
    errors: list[Exception] = []
    engine.on_action_error = lambda _trigger, exc: errors.append(exc)

    def failing_start() -> None:
        raise RuntimeError("cannot start from RUNNING")

    engine._actions.start = failing_start  # noqa: SLF001 - directly exercising the error path
    await engine.handle(
        InputEvent(source="panel", type="start_button", value=True, occurred_at=clock.now())
    )
    assert len(errors) == 1
    assert "cannot start from RUNNING" in str(errors[0])
