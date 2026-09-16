"""Generic Runtime HTTP+WebSocket server (doc section 6): builds the FastAPI app for
any object satisfying ``RuntimeApi``. Motor uses this now; Video/Setting reuse the
same shape in later stages -- this is not built ahead of a real consumer, Motor is
the first one.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from fusion.contracts.command import CommandRecord, CommandSubmitRequest
from fusion.contracts.config import ApplyRecord, ConfigRevision
from fusion.contracts.control import ControlSession, RuntimeMode
from fusion.contracts.errors import ErrorCode, FusionError
from fusion.contracts.event import Event
from fusion.contracts.plugin import TargetManifest


class RuntimeApi(Protocol):
    runtime_id: str
    runtime_boot_id: str

    @property
    def sequence(self) -> int: ...

    async def get_info(self) -> dict[str, Any]: ...
    async def get_targets(self) -> list[TargetManifest]: ...
    async def get_snapshot(self) -> dict[str, Any]: ...
    async def submit_command(self, req: CommandSubmitRequest) -> CommandRecord: ...
    async def get_command(self, command_id: str) -> CommandRecord | None: ...
    def replay_since(self, after_sequence: int) -> list[Event] | None: ...
    def subscribe_events(self) -> tuple[int, asyncio.Queue[Event | None]]: ...
    def unsubscribe_events(self, sub_id: int) -> None: ...
    async def acquire_control_session(
        self, actor: str, mode: RuntimeMode, ttl_s: float | None = None
    ) -> ControlSession: ...
    async def renew_control_session(self, session_id: str) -> ControlSession: ...
    async def release_control_session(self, session_id: str) -> None: ...
    def stage_config(self, kind: str, content: dict[str, Any]) -> ConfigRevision: ...
    async def apply_config(
        self, revision_id: str, expected_active_revision: str | None = None
    ) -> ApplyRecord: ...
    def get_config_status(self) -> dict[str, Any]: ...


_STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.VALIDATION_ERROR: 400,
    ErrorCode.UNKNOWN_TARGET: 404,
    ErrorCode.UNKNOWN_ACTION: 404,
    ErrorCode.COMMAND_NOT_FOUND: 404,
    ErrorCode.BUSY: 409,
    ErrorCode.REQUEST_ID_CONFLICT: 409,
    ErrorCode.CONTROL_SESSION_REQUIRED: 401,
    ErrorCode.CONTROL_SESSION_INVALID: 401,
    ErrorCode.CONTROL_SESSION_EXPIRED: 401,
    ErrorCode.RUNTIME_BOOT_MISMATCH: 409,
    ErrorCode.STALE_GENERATION: 409,
    ErrorCode.RESYNC_REQUIRED: 409,
    ErrorCode.INTERNAL_ERROR: 500,
}


class ControlSessionRequest(BaseModel):
    actor: str
    mode: RuntimeMode = RuntimeMode.MANUAL
    ttl_s: float | None = None


class ConfigStageRequest(BaseModel):
    kind: str
    content: dict[str, Any]


class ConfigApplyRequest(BaseModel):
    revision_id: str
    expected_active_revision: str | None = None


def build_app(api: RuntimeApi) -> FastAPI:
    app = FastAPI(title=f"fusion-runtime[{api.runtime_id}]")

    @app.exception_handler(FusionError)
    async def _fusion_error_handler(_request: Request, exc: FusionError) -> JSONResponse:
        status = _STATUS_BY_CODE.get(exc.code, 400)
        return JSONResponse(status_code=status, content=exc.to_dict())

    @app.get("/api/v1/info")
    async def info() -> dict[str, Any]:
        return await api.get_info()

    @app.get("/api/v1/targets")
    async def targets() -> list[dict[str, Any]]:
        return [m.model_dump() for m in await api.get_targets()]

    @app.get("/api/v1/snapshot")
    async def snapshot() -> dict[str, Any]:
        return await api.get_snapshot()

    @app.post("/api/v1/commands", status_code=202)
    async def submit(req: CommandSubmitRequest) -> dict[str, Any]:
        record = await api.submit_command(req)
        return record.model_dump()

    @app.get("/api/v1/commands/{command_id}")
    async def get_command(command_id: str) -> dict[str, Any]:
        record = await api.get_command(command_id)
        if record is None:
            raise FusionError(ErrorCode.COMMAND_NOT_FOUND, f"unknown command_id '{command_id}'")
        return record.model_dump()

    @app.post("/api/v1/control-sessions", status_code=201)
    async def acquire_session(body: ControlSessionRequest) -> dict[str, Any]:
        session = await api.acquire_control_session(body.actor, body.mode, body.ttl_s)
        return session.model_dump()

    @app.post("/api/v1/control-sessions/{session_id}/renew")
    async def renew_session(session_id: str) -> dict[str, Any]:
        session = await api.renew_control_session(session_id)
        return session.model_dump()

    @app.delete("/api/v1/control-sessions/{session_id}", status_code=204)
    async def release_session(session_id: str) -> None:
        await api.release_control_session(session_id)

    @app.post("/api/v1/config/stage", status_code=201)
    async def stage_config(body: ConfigStageRequest) -> dict[str, Any]:
        revision = api.stage_config(body.kind, body.content)
        return revision.model_dump()

    @app.post("/api/v1/config/apply")
    async def apply_config(body: ConfigApplyRequest) -> dict[str, Any]:
        record = await api.apply_config(body.revision_id, body.expected_active_revision)
        return record.model_dump()

    @app.get("/api/v1/config/status")
    async def config_status() -> dict[str, Any]:
        return api.get_config_status()

    @app.websocket("/api/v1/events")
    async def events_ws(ws: WebSocket) -> None:
        await ws.accept()
        try:
            init = await ws.receive_json()
        except Exception:  # noqa: BLE001 - malformed/empty init -> just stream from now
            init = {}
        after_sequence = int(init.get("after_sequence", api.sequence))

        replay = api.replay_since(after_sequence)
        if replay is None:
            await ws.send_json({"type": "resync_required"})
            await ws.close()
            return
        for replayed_event in replay:
            await ws.send_json(replayed_event.model_dump())

        sub_id, queue = api.subscribe_events()
        try:
            while True:
                live_event = await queue.get()
                if live_event is None:
                    await ws.send_json({"type": "resync_required"})
                    break
                await ws.send_json(live_event.model_dump())
        except WebSocketDisconnect:
            pass
        finally:
            api.unsubscribe_events(sub_id)

    return app
