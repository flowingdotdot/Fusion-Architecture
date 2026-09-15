"""Base class every Target adapter (real plugin or Fake) implements (doc section 9).

Action strings are never used for ``getattr``/``eval``/shell dispatch -- ``execute``
is the single explicit entry point, and every action it accepts must be declared in
``manifest()`` first. ``validate_manifest`` is the "manifest matches registered
actions" check the doc requires at startup.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from fusion.contracts.plugin import TargetManifest
from fusion.contracts.state import TargetState


class TargetAdapter(ABC):
    @abstractmethod
    def manifest(self) -> TargetManifest: ...

    @abstractmethod
    async def execute(self, action: str, params: dict[str, Any]) -> None:
        """Validate and start one accepted action. Raise ValueError for anything the
        plugin itself rejects (bad params, disallowed state, alarm, etc)."""

    @abstractmethod
    def is_action_complete(self, action: str, params: dict[str, Any]) -> bool: ...

    @abstractmethod
    def snapshot(self) -> TargetState: ...


def validate_manifest(adapter: TargetAdapter) -> None:
    manifest = adapter.manifest()
    seen: set[str] = set()
    for action in manifest.actions:
        if action.name in seen:
            raise ValueError(
                f"plugin '{manifest.plugin_id}' declares duplicate action '{action.name}'"
            )
        seen.add(action.name)
