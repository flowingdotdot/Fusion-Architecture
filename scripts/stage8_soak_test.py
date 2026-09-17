"""Stage 8 soak/verification harness (doc section 19: "LAN 통합·장시간 운영·성능
측정", deliverable "현장 시나리오 검증 보고"). Spawns real Motor + Video Runtime
processes -- separate OS processes talking over real loopback TCP/HTTP, not
in-process Fakes -- and drives a Scheduler against them in a repeated Show-cycle
loop. The user chose single-PC simulation over real multi-PC LAN exposure for
this pass (that would additionally need the auth/TLS work doc section 4
requires once a Runtime is reachable from outside loopback).

Every ``--kill-motor-every`` cycles, the Motor process is killed and restarted
to exercise doc item 4 ("연결 상실... 복구 시 장비별 동작") -- the Scheduler's next
``arm()`` naturally re-probes reachability and acquires a fresh control session,
so recovery is "does the system keep going", not a new mechanism.

Per-cycle latency (submit-Show-start to Show back to IDLE) is measured and
reported with no fixed pass/fail target -- the user chose "measure only" for
this pass (doc item 3 calls out timing tolerance as something to collect from
real requirements, not invent).

Not part of the normal ``pytest -q`` suite: this runs for minutes, not
milliseconds, and binds the same fixed ports (8101, 8103) Motor/Video always
use, so it will conflict with anything else already listening there. Run
directly (from an activated venv):

    python scripts/stage8_soak_test.py --duration-minutes 5 --kill-motor-every 3

Writes a report to ``--report`` (default: docs/stage8_soak_report.md).
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from fusion.apps.scheduler.runtime import SchedulerRuntimeApp
from fusion.contracts.timeline import Cue, Timeline
from fusion.core.clock import RealClock
from fusion.core.show_controller import ShowState
from fusion.transport.client import RuntimeClient

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_SRC = REPO_ROOT / "src"
MOTOR_URL = "http://127.0.0.1:8101"
VIDEO_URL = "http://127.0.0.1:8103"
MOTOR_MODULE = "fusion.apps.motor.main"
VIDEO_MODULE = "fusion.apps.video.main"


def spawn(module: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-m", module],
        cwd=str(REPO_SRC),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop(proc: subprocess.Popen[bytes]) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


async def wait_until_reachable(client: RuntimeClient, timeout_s: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            await client.get_info()
            return
        except Exception as exc:  # noqa: BLE001 - polling for readiness, not a real failure yet
            last_exc = exc
            await asyncio.sleep(0.2)
    raise TimeoutError(f"runtime never became reachable: {last_exc}")


@dataclass
class CycleResult:
    outcome: str  # "SUCCEEDED" | "FAULTED" | "ERROR"
    latency_s: float | None
    detail: str | None = None


@dataclass
class SoakReport:
    started_at: str
    duration_minutes: float
    kill_motor_every: int
    cycles: list[CycleResult] = field(default_factory=list)
    motor_restarts: int = 0

    def render(self) -> str:
        succeeded = [c for c in self.cycles if c.outcome == "SUCCEEDED"]
        failed = [c for c in self.cycles if c.outcome != "SUCCEEDED"]
        latencies = [c.latency_s for c in succeeded if c.latency_s is not None]
        lines = [
            "# Stage 8 soak test report",
            "",
            f"- started_at: {self.started_at}",
            f"- requested duration: {self.duration_minutes} min",
            f"- motor kill/restart every N cycles: {self.kill_motor_every or 'disabled'}",
            f"- motor restarts performed: {self.motor_restarts}",
            f"- total cycles: {len(self.cycles)}",
            f"- succeeded: {len(succeeded)}",
            f"- failed/faulted/error: {len(failed)}",
        ]
        if latencies:
            latencies_sorted = sorted(latencies)
            p95_index = max(0, int(len(latencies_sorted) * 0.95) - 1)
            lines += [
                "",
                "## Latency (Show start -> back to IDLE), seconds",
                f"- min: {min(latencies):.3f}",
                f"- max: {max(latencies):.3f}",
                f"- mean: {statistics.mean(latencies):.3f}",
                f"- p95: {latencies_sorted[p95_index]:.3f}",
            ]
        if failed:
            lines += ["", "## Failed cycles"]
            for c in failed:
                lines.append(f"- outcome={c.outcome}: {c.detail}")
        lines += [
            "",
            "## Notes / remaining limits",
            "- Single-PC simulation (separate OS processes over real loopback TCP,",
            "  not separate physical machines) -- doc section 20 item 6 (real LAN/",
            "  internet scope, Broker/file-server placement) still needs a real",
            "  venue network before this can be called LAN-verified.",
            "- No fixed latency pass/fail target was set for this pass (measure-only",
            "  was chosen); compare the numbers above against a real target once one",
            "  exists (doc section 20 item 3).",
            "- Fault injection covers Motor process kill+restart between cycles",
            "  only -- not Video, not a mid-command network partition, and not",
            "  power loss (doc section 20 item 4 lists these as real requirements",
            "  to collect, not invent).",
        ]
        return "\n".join(lines)


async def run_cycle(scheduler: SchedulerRuntimeApp) -> CycleResult:
    start = time.monotonic()
    try:
        await scheduler.arm()
    except Exception as exc:  # noqa: BLE001 - Motor may be mid-restart; report and keep the loop going
        return CycleResult(outcome="ERROR", latency_s=None, detail=f"arm failed: {exc}")

    scheduler.start()
    deadline = time.monotonic() + 20.0
    while scheduler.controller.state in (ShowState.RUNNING, ShowState.HOLDING):
        if time.monotonic() > deadline:
            return CycleResult(outcome="ERROR", latency_s=None, detail="cycle timed out mid-run")
        await asyncio.sleep(0.02)

    latency = time.monotonic() - start
    if scheduler.controller.state == ShowState.FAULTED:
        detail = scheduler.controller.last_error
        try:
            # doc: no Resume, Abort-then-rearm only -- this is how a FAULTED Show
            # gets back to IDLE so the next cycle's arm() is even possible.
            await scheduler.abort()
        except Exception as exc:  # noqa: BLE001 - best-effort; next cycle's arm() will surface if still stuck
            detail = f"{detail} (recovery abort also failed: {exc})"
        return CycleResult(outcome="FAULTED", latency_s=latency, detail=detail)

    return CycleResult(outcome="SUCCEEDED", latency_s=latency)


async def main_async(args: argparse.Namespace) -> SoakReport:
    motor_proc = spawn(MOTOR_MODULE)
    video_proc = spawn(VIDEO_MODULE)
    clock = RealClock()
    motor_client = RuntimeClient(MOTOR_URL)
    video_client = RuntimeClient(VIDEO_URL)
    report = SoakReport(
        started_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        duration_minutes=args.duration_minutes,
        kill_motor_every=args.kill_motor_every,
    )
    try:
        await wait_until_reachable(motor_client)
        await wait_until_reachable(video_client)

        scheduler = SchedulerRuntimeApp({"motor": motor_client, "video": video_client}, clock)
        scheduler.load_timeline(
            Timeline(
                cues=[
                    Cue(
                        cue_id="move",
                        at_ms=0,
                        runtime="motor",
                        target_id="motor01",
                        action="move",
                        params={"position": 50},
                    ),
                    Cue(
                        cue_id="prep",
                        at_ms=0,
                        runtime="video",
                        target_id="output1",
                        action="prepare",
                        params={"media_path": "fake.mp4"},
                    ),
                ]
            )
        )

        end_at = time.monotonic() + args.duration_minutes * 60
        cycle = 0
        while time.monotonic() < end_at:
            cycle += 1
            result = await run_cycle(scheduler)
            report.cycles.append(result)
            print(f"cycle {cycle}: {result.outcome} latency={result.latency_s}")

            if args.kill_motor_every and cycle % args.kill_motor_every == 0:
                print("  -> killing and restarting Motor process (fault injection)")
                stop(motor_proc)
                motor_proc = spawn(MOTOR_MODULE)
                report.motor_restarts += 1
                try:
                    await wait_until_reachable(motor_client, timeout_s=15.0)
                except TimeoutError as exc:
                    print(f"  !! motor did not come back: {exc}")

            await asyncio.sleep(args.cycle_pause_s)
    finally:
        await motor_client.aclose()
        await video_client.aclose()
        stop(motor_proc)
        stop(video_proc)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-minutes", type=float, default=5.0)
    parser.add_argument("--cycle-pause-s", type=float, default=1.0)
    parser.add_argument(
        "--kill-motor-every", type=int, default=5, help="0 disables fault injection"
    )
    parser.add_argument(
        "--report", type=Path, default=REPO_ROOT / "docs" / "stage8_soak_report.md"
    )
    args = parser.parse_args()

    report = asyncio.run(main_async(args))
    text = report.render()
    print("\n" + text)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(text, encoding="utf-8")
    print(f"\nreport written to {args.report}")


if __name__ == "__main__":
    main()
