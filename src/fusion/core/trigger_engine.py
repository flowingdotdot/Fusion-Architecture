"""Trigger engine (doc section 11): registered source/type/comparison/show-state
conditions map to one ShowController call. Debounce/cooldown are the engine's own
defense against repeated physical input (doc: "버튼 debounce", "시작 연타 재진입
방지", "전이 반복 제한"); ShowController's own state guard (raising on start() outside
ARMED, etc) is the second, independent line of defense against re-entry.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fusion.contracts.trigger import InputEvent, Trigger, condition_matches
from fusion.core.clock import Clock
from fusion.core.show_controller import ShowController


@dataclass
class ShowActions:
    """The actual start/hold/abort behavior, owned by whoever assembled the Show
    (Cue submission, RuntimeClient sessions, etc) -- the engine only decides *when*
    to call these, never *how*."""

    start: Callable[[], None]
    hold: Callable[[], None]
    abort: Callable[[], Awaitable[None]]


class TriggerEngine:
    def __init__(self, controller: ShowController, actions: ShowActions, clock: Clock) -> None:
        self._controller = controller
        self._actions = actions
        self._clock = clock
        self._triggers: list[Trigger] = []
        self._last_matched_at: dict[str, float] = {}
        self._last_fired_at: dict[str, float] = {}
        self.on_action_error: Callable[[Trigger, Exception], None] | None = None

    def register(self, trigger: Trigger) -> None:
        self._triggers.append(trigger)

    async def handle(self, event: InputEvent) -> None:
        now = self._clock.now()
        for trigger in self._triggers:
            if not condition_matches(trigger.condition, event):
                continue

            last_matched = self._last_matched_at.get(trigger.trigger_id)
            self._last_matched_at[trigger.trigger_id] = now
            if last_matched is not None and (now - last_matched) * 1000 < trigger.debounce_ms:
                continue

            if (
                trigger.show_states is not None
                and self._controller.state.value not in trigger.show_states
            ):
                continue

            last_fired = self._last_fired_at.get(trigger.trigger_id)
            if last_fired is not None and (now - last_fired) * 1000 < trigger.cooldown_ms:
                continue

            try:
                if trigger.action == "start":
                    self._actions.start()
                elif trigger.action == "hold":
                    self._actions.hold()
                elif trigger.action == "abort":
                    await self._actions.abort()
            except Exception as exc:  # noqa: BLE001 - a rejected transition is not a crash
                if self.on_action_error:
                    self.on_action_error(trigger, exc)
                continue

            self._last_fired_at[trigger.trigger_id] = now
