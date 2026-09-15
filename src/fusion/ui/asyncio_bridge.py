"""Runs an asyncio event loop on a dedicated background thread so Qt widget code can
call async Runtime-client methods without blocking the Qt event loop.

Doc section 4: "네트워크·MQTT·장비 콜백은 검증된 입력을 Runtime loop로 전달한다.
콜백 스레드가 상태를 직접 변경하지 않는다" and "Qt Signal/Slot은 UI 내부와 UI 측
통신 결과 반영에 사용한다" -- the network code here never touches a QWidget
directly; it only ever emits a Qt signal, whose queued delivery Qt itself marshals
onto the Qt (GUI) thread, and only *that* slot touches widgets.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from typing import Any

from PySide6.QtCore import QObject, Signal


class _ResultRelay(QObject):
    finished = Signal(object, object)  # (result, exception) -- delivered on the Qt thread


class AsyncioBridge:
    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._relays: list[_ResultRelay] = []  # kept alive until their signal fires

    def run(
        self,
        make_coro: Callable[[], Coroutine[Any, Any, Any]],
        on_done: Callable[[Any, BaseException | None], None],
    ) -> None:
        """Schedules ``make_coro()`` on the background loop; ``on_done(result,
        exception)`` runs on the Qt thread once it finishes (exactly one of the two
        is ``None``)."""
        relay = _ResultRelay()
        self._relays.append(relay)

        def _deliver(result: Any, exc: BaseException | None) -> None:
            self._relays.remove(relay)
            on_done(result, exc)

        relay.finished.connect(_deliver)

        future = asyncio.run_coroutine_threadsafe(make_coro(), self._loop)

        def _on_future_done(fut: asyncio.Future[Any]) -> None:
            try:
                result = fut.result()
            except BaseException as exc:  # noqa: BLE001 - forwarded to the Qt-side callback, not swallowed
                relay.finished.emit(None, exc)
            else:
                relay.finished.emit(result, None)

        future.add_done_callback(_on_future_done)

    def stop(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2.0)
