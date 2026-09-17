# Stage 8 soak test report

- started_at: 2026-09-17 11:25:41
- requested duration: 15.0 min
- motor kill/restart every N cycles: 5
- motor restarts performed: 132
- total cycles: 664
- succeeded: 664
- failed/faulted/error: 0

## Latency (Show start -> back to IDLE), seconds
- min: 0.091
- max: 0.374
- mean: 0.162
- p95: 0.348

## Notes / remaining limits
- Single-PC simulation (separate OS processes over real loopback TCP,
  not separate physical machines) -- doc section 20 item 6 (real LAN/
  internet scope, Broker/file-server placement) still needs a real
  venue network before this can be called LAN-verified.
- No fixed latency pass/fail target was set for this pass (measure-only
  was chosen); compare the numbers above against a real target once one
  exists (doc section 20 item 3).
- Fault injection covers Motor process kill+restart between cycles
  only -- not Video, not a mid-command network partition, and not
  power loss (doc section 20 item 4 lists these as real requirements
  to collect, not invent).