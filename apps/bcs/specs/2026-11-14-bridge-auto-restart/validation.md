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

## PR #4002 review follow-up (2026-11-14)

Reviewed head: `e8541851493e9341b00f1a386da3f928a353ccac`. The foreground-start finding was confirmed: with the supervisor lock held and the worker lock free, the original binary started another configuration and served HTTP ping successfully.

The fix rejects public foreground starts while a supervisor owns the runtime. A separate startup lock also serializes public starts through the initial worker-to-supervisor handoff and failure cleanup; internal daemon children bypass that startup lock. The user-facing behavior is documented in the [bridge CLI guide](../../crates/tools/bcs-bridge/cli/README.md).

Validation with Rust/Cargo 1.94.1 on macOS arm64:

- All four bridge crates and their test executables compiled with `cargo test --offline --locked --manifest-path apps/bcs/Cargo.toml -p bcs-bridge-cli -p bcs-bridge-app -p bcs-bridge-core -p bcs-bridge-qwenwork --no-run`.
- Direct CLI red/green checks used separate temporary homes, configurations, ports and test credentials. Under a held supervisor lock, the original foreground worker reached `running`/`ready` and HTTP ping 200; the fixed binary rejected the same start within two seconds with a supervisor error. A held startup lock rejected both public foreground and daemon starts; releasing it restored foreground startup. An internal `--daemon-child` still reached `running`/`ready` and ping 200 while both launcher and supervisor locks were held. Every test instance was stopped and cleaned up.
- Library tests: app **55 passed, 1 failed**; core **176 passed, 2 ignored**; QwenWork **44 passed, 1 ignored**. The app failure was the existing `credentials::tests::load_rejects_non_utf8_environment`, which launches its own test executable. The library suite is therefore not reported as fully passing.
- The daemon integration executable compiled, including the new startup-lock case and the expanded crash/replacement regression. Full execution remained blocked before the target assertion: macOS AMFI terminated Rust-spawned local executables with `SIGKILL` and logged `Unrecoverable CT signature issue`. A standalone Rust `Command` reproduction showed the same failure, while direct Python execution succeeded. Re-signing local test artifacts did not resolve it. No system security settings were changed.
- `git diff --check` passed; all modified source files remain below 1,000 lines. The startup handoff race is covered by lock-lifetime review and a deterministic startup-lock check, not a full OS scheduling reproduction. No live BCS/engine deployment, host reboot, or complete Singlebox suite was run for this follow-up.

## Stop/start handoff review follow-up (2026-11-14)

Reviewed head: `74d18ca2ba23809213cd7489c39164a6a00d3c89`. The stop/start handoff finding was confirmed: while the startup lock remained held, the previous binary reported a successful stop, but the unfinished launch could subsequently activate a supervisor and schedule a restart.

`stop` now acquires the startup lock before inspecting either lifecycle owner and retains it through shutdown. A missing runtime directory returns immediately without creating it; an existing runtime creates the lock if necessary so first launches and older instances use the same fence. Waiting longer than 10 seconds returns an explicit error requiring retry. This wait is separate from the existing supervisor shutdown timeout.

Validation with Rust/Cargo 1.94.1 on macOS arm64:

- The held-startup regressions failed before the fix because stop returned success prematurely. All **6 lifecycle unit tests** passed after the fix, including supervisor publication during the wait, shutdown fencing, an existing runtime without a startup lock, bounded timeout and an absent runtime directory.
- `cargo test --offline --locked --manifest-path apps/bcs/Cargo.toml -p bcs-bridge-app -p bcs-bridge-cli -- --test-threads=1` passed **143 tests, 0 failed, 0 ignored**, including **11/11 daemon integration tests**. The earlier local AMFI failure did not recur in this run; the previously failing app self-spawn test also passed.
- Direct CLI verification held the startup lock while launching real internal worker and supervisor processes. Stop remained pending until the handoff was released, then stopped both processes; status remained stopped after 5.5 seconds. A continuously held lock produced exit code 1 after 10.047 seconds without reporting success or writing a supervisor stop record. Missing/empty/profile-only runtime cases and stopping a worker without an existing startup lock passed. These cases used independent temporary homes, ports and test credentials; all processes were cleaned up.
- The earlier foreground-start fix was rechecked through real CLI/status/HTTP calls: public starts still reject active supervisor ownership and startup contention, while internal daemon children can initialize under those locks.
- `git diff --check` and the 1,000-line source-file limit passed. No live deployment, host reboot, full BCS workspace or Singlebox coverage run was performed for this follow-up.
