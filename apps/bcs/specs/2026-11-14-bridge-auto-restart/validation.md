# Validation

Date: 2026-11-14. Public source baseline: `7adc5670dc40f35e266bd9f68eca895511190337`.

## Implemented coverage

- `--auto-restart` requires `--daemon`; ordinary daemon behavior remains covered by the existing tests.
- A killed worker is replaced by a new PID while the supervisor PID remains stable.
- A duplicate start is rejected while the supervisor is waiting to restart a worker.
- `stop` during a worker outage disables the supervisor and does not restart after the restart window.
- `stop` after a successful restart shuts down both lifecycle owners and remains stopped after the restart window.
- Backoff is 5/10/20/40/60 seconds and remains capped for sustained failures.
- A stale stop record cannot stop a new supervisor instance.

## Executed tests

Toolchain: `rustc 1.93.0 (254b59607 2026-01-19)` and Cargo 1.93.0, matching the distribution workflow toolchain.

```sh
cargo test --locked --manifest-path apps/bcs/Cargo.toml \
  -p bcs-bridge-cli -p bcs-bridge-app -p bcs-bridge-core -p bcs-bridge-qwenwork \
  -- --test-threads=1
```

Result: **509 passed, 0 failed, 4 ignored**. The ignored tests are the existing opt-in real Codex socket test, long-stream resource stress test, native live completion test and live QwenWork desktop question test; they are not counted as passed.

The daemon integration suite included in this run passed **10/10** tests, including the two new auto-restart lifecycle tests.

## Limits

No live BCS deployment, real engine or host/workspace reboot was exercised. Auto-restart covers worker process exit, not hung-process health checks, supervisor restart after a host reboot, or recovery of in-flight runs.
