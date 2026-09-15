# Fusion

미디어아트·설치 프로젝트의 상위 실행 순서를 제어하는 Show Control 시스템. 설계 기준 문서는
[`docs/Fusion_New_Development_Spec_v1.0.md`](docs/Fusion_New_Development_Spec_v1.0.md) 참고.

현재 개발 대상은 Scheduler / Motor / Video / Setting이며, Fusion LED는 사용자 요구사항 확정 전까지 개발을 보류한다.

## 현재 상태

문서 19절 기준 1~3단계 구현 완료 (Headless Motor Runtime, Fake Motor, HTTP/WebSocket API,
request_id 중복 제거·제어 세션·실행 세대, 단순 Timeline/Trigger/ShowController, Motor·Scheduler
최소 PySide6 UI). 실제 모터 Plugin, Video, Setting, MQTT, 외부 OSC/UDP Endpoint는 아직 없음.

## 실행

```bash
uv sync                      # 개발 의존성 설치
uv sync --group studio       # PySide6 UI까지 필요할 때

uv run fusion-motor           # Headless Motor Runtime (127.0.0.1:8101)
uv run fusion-motor-ui        # Motor 수동 제어 UI (별도 프로세스)
uv run fusion-scheduler-ui    # Scheduler UI (Arm → Start → Timeline 실행)
uv run fusion-scheduler       # Scheduler 데모 스크립트 (CLI)

uv run pytest -q               # 테스트
uv run ruff check .            # 린트
uv run mypy src/fusion         # 타입 검사
```
