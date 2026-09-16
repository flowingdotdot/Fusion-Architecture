"""Generic Runtime HTTP+WebSocket client (doc section 6). Reuses one HTTP connection
(httpx.AsyncClient keep-alive) rather than opening a new connection per request.
Scheduler uses this to talk to Motor now; Video/Setting are future consumers of the
same shape.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from pathlib import Path
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

    async def upload_file(
        self, file_path: Path, *, expected_sha256: str | None = None
    ) -> dict[str, Any]:
        """Streams a local file to the Runtime's blob store (doc section 14:
        "대용량 패키지·영상·대량 로그는 HTTPS로 전송한다") -- reads in chunks off a
        worker thread so a large upload doesn't block the event loop, and uses no
        request timeout since transfer time scales with file size, not with the
        5s default used for small JSON calls."""
        params = {"sha256": expected_sha256} if expected_sha256 else None

        async def _reader() -> AsyncGenerator[bytes]:
            with file_path.open("rb") as fh:
                while chunk := await asyncio.to_thread(fh.read, 1024 * 1024):
                    yield chunk

        r = await self._http.post("/api/v1/files", content=_reader(), params=params, timeout=None)
        r.raise_for_status()
        return r.json()  # type: ignore[no-any-return]

    async def download_file(self, sha256: str, dest_path: Path) -> None:
        async with self._http.stream(
            "GET", f"/api/v1/files/{sha256}", timeout=None
        ) as r:
            r.raise_for_status()
            with dest_path.open("wb") as fh:
                async for chunk in r.aiter_bytes():
                    await asyncio.to_thread(fh.write, chunk)

    async def events(self, after_sequence: int) -> AsyncGenerator[dict[str, Any]]:
        url = f"{self._ws_url}/api/v1/events"
        async with websockets.connect(url) as ws:
            await ws.send(json.dumps({"after_sequence": after_sequence}))
            async for raw in ws:
                msg: dict[str, Any] = json.loads(raw)
                if msg.get("type") == "resync_required":
                    raise ResyncRequired()
                yield msg
