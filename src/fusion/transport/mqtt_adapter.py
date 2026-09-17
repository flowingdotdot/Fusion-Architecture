"""MQTT remote management Adapter (doc section 14): calls the exact same internal
Stage/Apply/status service HTTP already calls (``DeviceRuntimeApp.stage_config`` /
``apply_config`` / ``get_config_status``) -- no separate bypass path (doc: "MQTT
전용 우회 Apply 경로를 만들지 않는다"). Scope is deliberately narrow per doc: config
push/pull, staged/active revision query, apply request/result, status/diagnostics --
NOT motor moves or Show execution ("초기 MQTT에서 직접 모터 이동·Show 실행은 구현하지
않는다").

Topic layout (this module's own choice -- the doc doesn't fix one):
  {prefix}/{runtime_id}/info                  retained, published once at connect
  {prefix}/{runtime_id}/config/status         retained, refreshed periodically
  {prefix}/{runtime_id}/config/stage/request  subscribed;
      {"kind":..., "content":..., "request_id"?:...}
  {prefix}/{runtime_id}/config/stage/response   published, never retained
  {prefix}/{runtime_id}/config/apply/request  subscribed;
      {"revision_id":..., "expected_active_revision"?:..., "request_id"?:...}
  {prefix}/{runtime_id}/config/apply/response   published, never retained

Doc: "실행·적용 요청은 retain=false이며 retained 실행 요청도 수신 시 거절한다" -- a
stage/apply *request* that arrives with the MQTT retain flag set (a stale message
the broker replayed to a freshly (re)connecting subscriber, not a live request) is
rejected outright and never executed. Status IS allowed to be retained -- doc
explicitly separates the two.

Using a genuinely public broker (e.g. HiveMQ's free public test broker,
broker.hivemq.com:1883) means every topic under whatever prefix is chosen is
visible to and writable by anyone else also using that broker -- there is no
authentication or per-Runtime topic ACL on it at all (doc section 14 explicitly
wants TLS + Runtime-scoped ACLs for a real deployment; a public test broker cannot
provide either). Pick a unique ``topic_prefix`` to avoid collisions, and never rely
on this for anything sensitive.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
from collections.abc import Callable
from typing import Any, Protocol

import aiomqtt

from fusion.contracts.config import ApplyRecord, ConfigRevision
from fusion.core.clock import Clock

logger = logging.getLogger("fusion.mqtt")

DEFAULT_STATUS_INTERVAL_S = 10.0
DEFAULT_RECONNECT_INITIAL_DELAY_S = 1.0
DEFAULT_RECONNECT_MAX_DELAY_S = 30.0


class ConfigApi(Protocol):
    def stage_config(self, kind: str, content: dict[str, Any]) -> ConfigRevision: ...
    async def apply_config(
        self, revision_id: str, expected_active_revision: str | None = None
    ) -> ApplyRecord: ...
    def get_config_status(self) -> dict[str, Any]: ...


class MqttPublisher(Protocol):
    """The minimal slice of ``aiomqtt.Client`` this module actually uses -- lets
    tests inject a fake without a real broker connection."""

    async def publish(
        self, topic: str, payload: str, *, qos: int = 0, retain: bool = False
    ) -> None: ...


class MqttSettingAdapter:
    def __init__(
        self,
        host: str,
        port: int,
        runtime_id: str,
        api: ConfigApi,
        clock: Clock,
        *,
        topic_prefix: str = "fusion",
        username: str | None = None,
        password: str | None = None,
        client_id: str | None = None,
        use_tls: bool = False,
        status_interval_s: float = DEFAULT_STATUS_INTERVAL_S,
        reconnect_initial_delay_s: float = DEFAULT_RECONNECT_INITIAL_DELAY_S,
        reconnect_max_delay_s: float = DEFAULT_RECONNECT_MAX_DELAY_S,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._runtime_id = runtime_id
        self._api = api
        self._clock = clock
        self._prefix = topic_prefix
        self._username = username
        self._password = password
        self._client_id = client_id
        self._use_tls = use_tls
        self._status_interval_s = status_interval_s
        self._reconnect_initial_delay_s = reconnect_initial_delay_s
        self._reconnect_max_delay_s = reconnect_max_delay_s
        self._client_factory = client_factory or self._make_real_client
        self._task: asyncio.Task[None] | None = None

    def topic(self, suffix: str) -> str:
        return f"{self._prefix}/{self._runtime_id}/{suffix}"

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def _make_real_client(self) -> aiomqtt.Client:
        tls_context = ssl.create_default_context() if self._use_tls else None
        return aiomqtt.Client(
            hostname=self._host,
            port=self._port,
            username=self._username,
            password=self._password,
            identifier=self._client_id,
            tls_context=tls_context,
        )

    async def _run_forever(self) -> None:
        """A dropped broker connection (network blip, broker restart, long-running
        deployment outliving a single TCP session) must not permanently kill this
        background task -- doc's "장시간 운영" requirement implies the MQTT channel
        recovers on its own, the same way WebSocket resync/control-session renewal
        already do for the HTTP side. Backoff is exponential, capped, and resets
        once a connection is actually established."""
        delay = self._reconnect_initial_delay_s
        while True:
            try:
                await self._connect_and_serve()
                delay = self._reconnect_initial_delay_s
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any connect/protocol failure retries, it never leaves the adapter permanently dead
                logger.warning("mqtt connection lost, retrying in %.1fs: %s", delay, exc)
                await self._clock.sleep(delay)
                delay = min(delay * 2, self._reconnect_max_delay_s)

    async def _connect_and_serve(self) -> None:
        async with self._client_factory() as client:
            await client.subscribe(self.topic("config/stage/request"), qos=1)
            await client.subscribe(self.topic("config/apply/request"), qos=1)
            await self.publish_info(client)
            await self.publish_status(client)

            status_task = asyncio.create_task(self._status_loop(client))
            try:
                async for message in client.messages:
                    if message.retain:
                        # doc: a retained execution/apply request is rejected, never executed
                        continue
                    await self._handle_message(client, str(message.topic), message.payload)
            finally:
                status_task.cancel()

    async def _status_loop(self, client: MqttPublisher) -> None:
        while True:
            await self._clock.sleep(self._status_interval_s)
            await self.publish_status(client)

    async def publish_info(self, client: MqttPublisher) -> None:
        payload = json.dumps({"runtime_id": self._runtime_id})
        await client.publish(self.topic("info"), payload, qos=1, retain=True)

    async def publish_status(self, client: MqttPublisher) -> None:
        payload = json.dumps(self._api.get_config_status())
        await client.publish(self.topic("config/status"), payload, qos=1, retain=True)

    async def _handle_message(self, client: MqttPublisher, topic: str, raw_payload: bytes) -> None:
        try:
            payload = json.loads(raw_payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        if topic == self.topic("config/stage/request"):
            await self.handle_stage_request(client, payload)
        elif topic == self.topic("config/apply/request"):
            await self.handle_apply_request(client, payload)

    async def handle_stage_request(self, client: MqttPublisher, payload: dict[str, Any]) -> None:
        response: dict[str, Any] = {"request_id": payload.get("request_id")}
        try:
            revision = self._api.stage_config(payload["kind"], payload["content"])
            response.update(revision.model_dump())
        except Exception as exc:  # noqa: BLE001 - reported back over MQTT, not raised into the message loop
            response["error"] = str(exc)
        await client.publish(
            self.topic("config/stage/response"), json.dumps(response), qos=1, retain=False
        )

    async def handle_apply_request(self, client: MqttPublisher, payload: dict[str, Any]) -> None:
        response: dict[str, Any] = {"request_id": payload.get("request_id")}
        try:
            record = await self._api.apply_config(
                payload["revision_id"], payload.get("expected_active_revision")
            )
            response.update(record.model_dump())
        except Exception as exc:  # noqa: BLE001 - reported back over MQTT, not raised into the message loop
            response["error"] = str(exc)
        await client.publish(
            self.topic("config/apply/response"), json.dumps(response), qos=1, retain=False
        )
        await self.publish_status(client)
