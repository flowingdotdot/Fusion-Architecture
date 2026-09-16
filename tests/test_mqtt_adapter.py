"""MqttSettingAdapter's message-handling logic (doc section 14): calls the exact
same ConfigService the HTTP routes use. Driven directly against a fake publisher
double (no real broker) -- the retained-message-rejection wiring and the live
HiveMQ round trip are verified separately (that part genuinely needs a real broker).
"""

from __future__ import annotations

import json

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
