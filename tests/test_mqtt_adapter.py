"""MqttSettingAdapter's message-handling logic (doc section 14): calls the exact
same ConfigService the HTTP routes use. Driven directly against a fake publisher
double (no real broker) -- the retained-message-rejection wiring and the live
HiveMQ round trip are verified separately (that part genuinely needs a real broker).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fusion.apps.motor.runtime import MotorRuntimeApp
from fusion.simulation.fake_clock import FakeClock
from fusion.simulation.fake_motor import FakeMotorAdapter
from fusion.transport.mqtt_adapter import MqttSettingAdapter


class FakePublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, int, bool]] = []

    async def publish(
        self, topic: str, payload: str, *, qos: int = 0, retain: bool = False
    ) -> None:
        self.published.append((topic, payload, qos, retain))


def make_adapter() -> tuple[MqttSettingAdapter, MotorRuntimeApp, FakeClock]:
    clock = FakeClock()
    motor = FakeMotorAdapter("motor01", clock)
    app = MotorRuntimeApp("motor-test", {"motor01": motor}, clock)
    adapter = MqttSettingAdapter(
        "broker.example", 1883, "motor-test", app, clock, topic_prefix="fusion-test"
    )
    return adapter, app, clock


class FailingConnection:
    """Stands in for what a real ``aiomqtt.Client`` does when the broker is
    unreachable: construction succeeds, the actual network attempt (and failure)
    happens on ``__aenter__``."""

    async def __aenter__(self) -> FailingConnection:
        raise ConnectionError("simulated broker unreachable")

    async def __aexit__(self, *exc: object) -> None:
        return None


class FakeMqttConnection:
    """Minimal async-context-manager stand-in for a connected ``aiomqtt.Client`` --
    enough surface (``subscribe``/``publish``/``messages``) for
    ``_connect_and_serve`` to run its real connect sequence against, without a
    real broker. ``messages`` idles forever (a real connection sits open until
    something disconnects it), which is fine here since the test only needs to
    observe that the connect sequence completed."""

    def __init__(self, publisher: FakePublisher) -> None:
        self._publisher = publisher
        self.subscribed: list[str] = []

    async def __aenter__(self) -> FakeMqttConnection:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def subscribe(self, topic: str, qos: int = 0) -> None:
        self.subscribed.append(topic)

    async def publish(
        self, topic: str, payload: str, *, qos: int = 0, retain: bool = False
    ) -> None:
        await self._publisher.publish(topic, payload, qos=qos, retain=retain)

    @property
    def messages(self) -> Any:
        return self._idle_forever()

    async def _idle_forever(self) -> Any:
        await asyncio.Event().wait()
        yield  # pragma: no cover - unreachable, just makes this an async generator


class FailThenSucceedFactory:
    """A ``client_factory`` that fails ``fail_times`` connection attempts, then
    hands back a working ``FakeMqttConnection`` -- drives
    ``MqttSettingAdapter._run_forever``'s reconnect/backoff loop deterministically."""

    def __init__(self, fail_times: int, publisher: FakePublisher) -> None:
        self.fail_times = fail_times
        self.attempts = 0
        self.publisher = publisher
        self.connections: list[FakeMqttConnection] = []

    def __call__(self) -> Any:
        self.attempts += 1
        if self.attempts <= self.fail_times:
            return FailingConnection()
        conn = FakeMqttConnection(self.publisher)
        self.connections.append(conn)
        return conn


def test_topic_uses_configured_prefix_and_runtime_id() -> None:
    adapter, _app, _clock = make_adapter()
    assert adapter.topic("info") == "fusion-test/motor-test/info"
    assert adapter.topic("config/stage/request") == "fusion-test/motor-test/config/stage/request"


async def test_stage_request_stages_and_publishes_a_non_retained_response() -> None:
    adapter, app, _clock = make_adapter()
    publisher = FakePublisher()

    await adapter.handle_stage_request(
        publisher, {"kind": "install", "content": {"a": 1}, "request_id": "r1"}
    )

    assert len(publisher.published) == 1
    topic, payload, qos, retain = publisher.published[0]
    assert topic == adapter.topic("config/stage/response")
    assert retain is False
    body = json.loads(payload)
    assert body["request_id"] == "r1"
    assert body["kind"] == "install"
    assert body["revision_id"] in app.get_config_status()["staged_revision_ids"]


async def test_stage_request_with_bad_payload_reports_error_not_raise() -> None:
    adapter, _app, _clock = make_adapter()
    publisher = FakePublisher()

    await adapter.handle_stage_request(publisher, {"request_id": "r1"})  # missing "kind"/"content"

    topic, payload, _qos, retain = publisher.published[0]
    assert retain is False
    body = json.loads(payload)
    assert "error" in body
    assert body["request_id"] == "r1"


async def test_apply_request_applies_and_also_refreshes_retained_status() -> None:
    adapter, app, _clock = make_adapter()
    publisher = FakePublisher()

    revision = app.stage_config("install", {"a": 1})
    await adapter.handle_apply_request(
        publisher, {"revision_id": revision.revision_id, "request_id": "r2"}
    )

    assert len(publisher.published) == 2  # apply response, then a status refresh
    apply_topic, apply_payload, _qos1, apply_retain = publisher.published[0]
    status_topic, status_payload, _qos2, status_retain = publisher.published[1]

    assert apply_topic == adapter.topic("config/apply/response")
    assert apply_retain is False
    apply_body = json.loads(apply_payload)
    assert apply_body["outcome"] == "SUCCEEDED"
    assert apply_body["request_id"] == "r2"

    assert status_topic == adapter.topic("config/status")
    assert status_retain is True
    assert json.loads(status_payload)["active_revision_id"] == revision.revision_id


async def test_publish_status_is_retained() -> None:
    adapter, _app, _clock = make_adapter()
    publisher = FakePublisher()

    await adapter.publish_status(publisher)

    topic, _payload, _qos, retain = publisher.published[0]
    assert topic == adapter.topic("config/status")
    assert retain is True


async def test_publish_info_is_retained() -> None:
    adapter, _app, _clock = make_adapter()
    publisher = FakePublisher()

    await adapter.publish_info(publisher)

    topic, payload, _qos, retain = publisher.published[0]
    assert topic == adapter.topic("info")
    assert retain is True
    assert json.loads(payload)["runtime_id"] == "motor-test"


async def test_run_forever_retries_a_dropped_connection_and_recovers() -> None:
    """Doc's 'long-running operation' requirement (stage 8): a broker connection
    that fails must not permanently kill the background task -- it retries with
    backoff and, once the broker becomes reachable again, resumes normal
    operation (subscribe + publish info/status) exactly as a first-try connect
    would have."""
    clock = FakeClock()
    motor = FakeMotorAdapter("motor01", clock)
    app = MotorRuntimeApp("motor-test", {"motor01": motor}, clock)
    publisher = FakePublisher()
    factory = FailThenSucceedFactory(fail_times=2, publisher=publisher)
    adapter = MqttSettingAdapter(
        "broker.example",
        1883,
        "motor-test",
        app,
        clock,
        topic_prefix="fusion-test",
        reconnect_initial_delay_s=1.0,
        reconnect_max_delay_s=30.0,
        client_factory=factory,
    )

    await adapter.start()
    await clock.advance(1.0)  # 1st attempt fails immediately, backoff = 1s
    await clock.advance(2.0)  # 2nd attempt fails, backoff doubles to 2s; 3rd attempt succeeds

    assert factory.attempts == 3
    assert len(factory.connections) == 1
    conn = factory.connections[0]
    assert adapter.topic("config/stage/request") in conn.subscribed
    assert adapter.topic("config/apply/request") in conn.subscribed
    published_topics = [topic for topic, *_ in publisher.published]
    assert adapter.topic("info") in published_topics
    assert adapter.topic("config/status") in published_topics

    await adapter.stop()
