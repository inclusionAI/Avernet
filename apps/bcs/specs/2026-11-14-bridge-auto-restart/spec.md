# Bridge daemon auto-restart

> Date: 2026-11-14
> Status: implemented; see validation evidence
> Owner: public BCS `bcs-bridge` application lifecycle
> Public source baseline: `7adc5670dc40f35e266bd9f68eca895511190337`
> [Execution plan](plan.md) · [Validation](validation.md)

## Intent

Add an opt-in auto-restart mode without changing existing daemon semantics:

```sh
bcs-bridge start --daemon --auto-restart
```

A detached supervisor observes the existing worker instance. If the worker exits without an explicit `bcs-bridge stop`, the supervisor starts a replacement after a bounded delay. `bcs-bridge stop` disables the supervisor before stopping the worker, so an intentional stop never causes a restart.

## Existing contracts to preserve

- `start --daemon` without `--auto-restart` keeps its current one-worker behavior.
- One local worker per runtime directory; foreground and background startup share the existing instance lock.
- PID values remain diagnostic. Stop requests use the verified instance ID and private control socket, never a bare PID signal.
- Configuration, profile, working-directory override, environment and `bridge.log` are retained across restarts.
- Registration and configuration replacement remain fenced while either a worker or a supervisor is active.
- Restart restores the process and its connections; it does not promise recovery or replay of in-flight runs.

## Design

The initial worker uses the existing daemon launch path. After it becomes ready, the launcher detaches a supervisor with its own lock and instance record under the same private runtime directory.

The supervisor polls the worker through the existing verified control path:

- a verified running worker is left alone;
- an unlocked worker is considered exited and is started again after a delay;
- an ambiguous locked worker is not duplicated; the supervisor waits and retries observation;
- repeated startup failures use exponential backoff from 5 seconds to 60 seconds.

A supervisor-specific stop record carries its random instance ID. `stop` writes that record before stopping the worker. The supervisor checks it while observing and during restart backoff, then exits and removes its runtime record. A stale stop record from an older supervisor cannot stop a new instance.

## Scope

- Add the public `--auto-restart` flag; require `--daemon`.
- Add supervisor lifecycle, status, stop and registration fencing.
- Keep the worker IPC and Bridge service contracts unchanged.
- Add Unix daemon integration tests for crash restart, intentional stop, duplicate startup and stopped-worker handling.
- Do not add hang detection, host reboot persistence, in-flight run recovery or a new configuration file format.
