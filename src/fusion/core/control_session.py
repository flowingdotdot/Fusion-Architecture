"""Control session manager (doc section 5): one active session per Runtime.

Expiry/renewal use the Runtime's own local monotonic Clock -- never a cross-PC
timestamp. Issuing a new session replaces (invalidates) whatever was active.
"""

from __future__ import annotations

from fusion.contracts.control import ControlSession, RuntimeMode
from fusion.contracts.errors import ErrorCode, FusionError
from fusion.contracts.ids import new_control_session_id
from fusion.core.clock import Clock

DEFAULT_TTL_S = 30.0


class ControlSessionManager:
    def __init__(
        self, clock: Clock, runtime_boot_id: str, *, default_ttl_s: float = DEFAULT_TTL_S
    ) -> None:
        self._clock = clock
        self._runtime_boot_id = runtime_boot_id
        self._default_ttl_s = default_ttl_s
        self._active: ControlSession | None = None

    @property
    def active(self) -> ControlSession | None:
        return self._active

    def acquire(
        self, actor: str, mode: RuntimeMode, *, ttl_s: float | None = None
    ) -> ControlSession:
        now = self._clock.now()
        session = ControlSession(
            session_id=new_control_session_id(),
            mode=mode,
            actor=actor,
            issued_at=now,
            expires_at=now + (ttl_s or self._default_ttl_s),
            runtime_boot_id=self._runtime_boot_id,
        )
        self._active = session
        return session

    def renew(self, session_id: str, *, ttl_s: float | None = None) -> ControlSession:
        session = self._require_active(session_id)
        session.expires_at = self._clock.now() + (ttl_s or self._default_ttl_s)
        return session

    def release(self, session_id: str) -> None:
        if self._active is not None and self._active.session_id == session_id:
            self._active = None

    def validate(self, session_id: str | None) -> ControlSession:
        if session_id is None:
            raise FusionError(ErrorCode.CONTROL_SESSION_REQUIRED, "control_session_id is required")
        return self._require_active(session_id)

    def _require_active(self, session_id: str) -> ControlSession:
        if self._active is None or self._active.session_id != session_id:
            raise FusionError(ErrorCode.CONTROL_SESSION_INVALID, "control session is not active")
        if self._clock.now() >= self._active.expires_at:
            self._active = None
            raise FusionError(ErrorCode.CONTROL_SESSION_EXPIRED, "control session has expired")
        return self._active
