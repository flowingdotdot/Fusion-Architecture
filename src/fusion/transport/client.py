"""Generic Runtime HTTP+WebSocket client (doc section 6). Reuses one HTTP connection
(httpx.AsyncClient keep-alive) rather than opening a new connection per request.
Scheduler uses this to talk to Motor now; Video/Setting are future consumers of the
same shape.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx
import websockets


class RuntimeCommandError(Exception):
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        super().__init__(body.get("message", "command error"))
        self.status_code = status_code
        self.code = body.get("code")
        self.body = body


class ResyncRequired(Exception):
    """Raised out of ``events()`` when the server can't cover the requested replay
    range (or a subscriber fell behind) -- caller must re-fetch a snapshot."""


class RuntimeClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._ws_url = self._base_url.replace("http://", "ws://").replace("https://", "wss://")
        self._http = httpx.AsyncClient(base_url=self._base_url, timeout=5.0)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get_info(self) -> dict[str, Any]:
        r = await self._http.get("/api/v1/info")
        r.raise_for_status()
        return r.json()  # type: ignore[no-any-return]

    async def get_targets(self) -> list[dict[str, Any]]:
        r = await self._http.get("/api/v1/targets")
        r.raise_for_status()
        return r.json()  # type: ignore[no-any-return]

    async def get_snapshot(self) -> dict[str, Any]:
        r = await self._http.get("/api/v1/snapshot")
        r.raise_for_status()
        return r.json()  # type: ignore[no-any-return]

    async def acquire_session(
        self, actor: str, mode: str = "SHOW", ttl_s: float | None = None
    ) -> dict[str, Any]:
        r = await self._http.post(
            "/api/v1/control-sessions", json={"actor": actor, "mode": mode, "ttl_s": ttl_s}
        )
        r.raise_for_status()
        return r.json()  # type: ignore[no-any-return]

    async def renew_session(self, session_id: str) -> dict[str, Any]:
        r = await self._http.post(f"/api/v1/control-sessions/{session_id}/renew")
        r.raise_for_status()
        return r.json()  # type: ignore[no-any-return]

    async def release_session(self, session_id: str) -> None:
        await self._http.delete(f"/api/v1/control-sessions/{session_id}")

    async def submit_command(self, **kwargs: Any) -> dict[str, Any]:
        r = await self._http.post("/api/v1/commands", json=kwargs)
        if r.status_code >= 400:
            raise RuntimeCommandError(r.status_code, r.json())
        return r.json()  # type: ignore[no-any-return]

    async def get_command(self, command_id: str) -> dict[str, Any]:
        r = await self._http.get(f"/api/v1/commands/{command_id}")
        r.raise_for_status()
        return r.json()  # type: ignore[no-any-return]

    async def stage_config(self, kind: str, content: dict[str, Any]) -> dict[str, Any]:
        r = await self._http.post("/api/v1/config/stage", json={"kind": kind, "content": content})
        r.raise_for_status()
        return r.json()  # type: ignore[no-any-return]

    async def apply_config(
        self, revision_id: str, expected_active_revision: str | None = None
    ) -> dict[str, Any]:
        r = await self._http.post(
            "/api/v1/config/apply",
            json={"revision_id": revision_id, "expected_active_revision": expected_active_revision},
        )
        r.raise_for_status()
        return r.json()  # type: ignore[no-any-return]

    async def get_config_status(self) -> dict[str, Any]:
        r = await self._http.get("/api/v1/config/status")
        r.raise_for_status()
        return r.json()  # type: ignore[no-any-return]

    async def get_bytes(self, path: str) -> bytes:
        """For a Runtime-specific extra route that isn't part of the generic
        RuntimeApi contract (e.g. Video's ``/preview`` JPEG snapshot)."""
        r = await self._http.get(path)
        r.raise_for_status()
        return r.content

    async def events(self, after_sequence: int) -> AsyncGenerator[dict[str, Any]]:
        url = f"{self._ws_url}/api/v1/events"
        async with websockets.connect(url) as ws:
            await ws.send(json.dumps({"after_sequence": after_sequence}))
            async for raw in ws:
                msg: dict[str, Any] = json.loads(raw)
                if msg.get("type") == "resync_required":
                    raise ResyncRequired()
                yield msg
