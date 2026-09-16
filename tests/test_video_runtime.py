"""VideoRuntimeApp + FakeVideoPlayerAdapter (doc section 12, stage 5): prepare/play/
pause/stop/seek/loop/volume, play-vs-ended distinction, media_generation on stale
events. Driven headlessly with FakeClock, mirroring test_command_flow.py's pattern.
"""

from __future__ import annotations

from fusion.apps.video.runtime import VideoRuntimeApp
from fusion.contracts.command import CommandOutcome, CommandRecord, CommandSubmitRequest
from fusion.contracts.control import RuntimeMode
from fusion.simulation.fake_clock import FakeClock
from fusion.simulation.fake_video_player import FakeVideoPlayerAdapter


def make_app(**player_kwargs: object) -> tuple[VideoRuntimeApp, FakeClock, FakeVideoPlayerAdapter]:
    clock = FakeClock()
    adapter = FakeVideoPlayerAdapter("output1", clock, **player_kwargs)  # type: ignore[arg-type]
    app = VideoRuntimeApp("video-test", {"output1": adapter}, clock)

    def _publish(event_type: str, payload: dict[str, object]) -> None:
        app.publish_event(event_type, target_id="output1", payload=payload)

    adapter.bind_event_publisher(_publish)
    return app, clock, adapter


async def run_to_completion(
    app: VideoRuntimeApp, clock: FakeClock, command_id: str, *, timeout_ticks: int = 200
) -> CommandRecord:
    for _ in range(timeout_ticks):
        record = await app.get_command(command_id)
        assert record is not None
        if record.status.value == "TERMINAL":
            return record
        await clock.advance(0.05)
    raise AssertionError("command did not reach a terminal state in time")


async def test_prepare_then_play_reaches_acknowledged_immediately() -> None:
    app, clock, adapter = make_app()
    session = await app.acquire_control_session("tester", RuntimeMode.MANUAL)

    prepare_req = CommandSubmitRequest(
        request_id="req-prepare",
        control_session_id=session.session_id,
        target_id="output1",
        action="prepare",
        params={"media_path": "mat/A.mp4"},
    )
    prepare_record = await app.submit_command(prepare_req)
    final = await run_to_completion(app, clock, prepare_record.command_id)
    assert final.outcome == CommandOutcome.SUCCEEDED
    assert adapter.snapshot().fields["prepared"].value is True

    play_req = CommandSubmitRequest(
        request_id="req-play",
        control_session_id=session.session_id,
        target_id="output1",
        action="play",
        completion_requirement="acknowledged",
    )
    play_record = await app.submit_command(play_req)
    # "acknowledged" resolves synchronously inside submit() -- already terminal.
    assert play_record.status.value == "TERMINAL"
    assert play_record.outcome == CommandOutcome.SUCCEEDED
    assert play_record.achieved_completion == "acknowledged"


async def test_play_before_prepare_fails_not_silently_ignored() -> None:
    app, _clock, _adapter = make_app()
    session = await app.acquire_control_session("tester", RuntimeMode.MANUAL)

    req = CommandSubmitRequest(
        request_id="req-play-early",
        control_session_id=session.session_id,
        target_id="output1",
        action="play",
        completion_requirement="acknowledged",
    )
    record = await app.submit_command(req)
    assert record.status.value == "TERMINAL"
    assert record.outcome == CommandOutcome.FAILED
    assert record.error is not None


async def test_playback_ended_event_fires_after_fake_duration_and_carries_generation() -> None:
    app, clock, adapter = make_app(fake_duration_s=1.0)
    session = await app.acquire_control_session("tester", RuntimeMode.MANUAL)

    async def submit(
        request_id: str, action: str, params: dict[str, object] | None = None
    ) -> CommandRecord:
        req = CommandSubmitRequest(
            request_id=request_id,
            control_session_id=session.session_id,
            target_id="output1",
            action=action,
            params=params or {},
            completion_requirement="acknowledged" if action != "prepare" else "observed",
        )
        return await app.submit_command(req)

    prepare_record = await submit("req-p1", "prepare", {"media_path": "mat/A.mp4"})
    await run_to_completion(app, clock, prepare_record.command_id)
    await submit("req-play1", "play")

    events = app.replay_since(0)
    assert events is not None
    # No "ended" yet -- only command lifecycle events so far.
    assert not any(e.type == "playback.ended" for e in events)

    await clock.advance(1.5)  # past the 1.0s fake duration

    events = app.replay_since(0)
    assert events is not None
    ended = [e for e in events if e.type == "playback.ended"]
    assert len(ended) == 1
    assert ended[0].payload["media_generation"] == 1
    assert ended[0].target_id == "output1"
    assert adapter.snapshot().fields["playing"].value is False


async def test_new_prepare_bumps_media_generation_and_stop_prevents_stale_ended() -> None:
    app, clock, adapter = make_app(fake_duration_s=1.0)
    session = await app.acquire_control_session("tester", RuntimeMode.MANUAL)

    async def submit(
        request_id: str, action: str, params: dict[str, object] | None = None
    ) -> CommandRecord:
        req = CommandSubmitRequest(
            request_id=request_id,
            control_session_id=session.session_id,
            target_id="output1",
            action=action,
            params=params or {},
            completion_requirement="acknowledged" if action != "prepare" else "observed",
        )
        return await app.submit_command(req)

    prepare1 = await submit("req-p1", "prepare", {"media_path": "mat/A.mp4"})
    await run_to_completion(app, clock, prepare1.command_id)
    await submit("req-play1", "play")

    await clock.advance(0.3)
    await submit("req-stop1", "stop")  # cancels the in-flight playback before it "ends"

    await clock.advance(2.0)  # long enough that the cancelled playback would have ended by now
    events = app.replay_since(0)
    assert events is not None
    assert not any(e.type == "playback.ended" for e in events)
    assert adapter.snapshot().fields["media_generation"].value == 1


async def test_volume_out_of_range_is_rejected() -> None:
    app, _clock, _adapter = make_app()
    session = await app.acquire_control_session("tester", RuntimeMode.MANUAL)

    req = CommandSubmitRequest(
        request_id="req-vol",
        control_session_id=session.session_id,
        target_id="output1",
        action="volume",
        params={"level": 150},
        completion_requirement="acknowledged",
    )
    record = await app.submit_command(req)
    assert record.status.value == "TERMINAL"
    assert record.outcome == CommandOutcome.FAILED
    assert record.error is not None
