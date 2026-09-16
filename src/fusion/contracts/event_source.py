"""External input Endpoint contract (doc section 13): an EventSource only ever
produces Events (InputEvents, fed to a TriggerEngine) -- it never receives Commands
and never calls a device function directly, deliberately not shaped like
``plugin_sdk.TargetAdapter``.
"""

from __future__ import annotations

from typing import Protocol


class EventSource(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
