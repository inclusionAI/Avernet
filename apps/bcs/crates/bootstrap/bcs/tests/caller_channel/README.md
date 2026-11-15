# Caller execution live-process fixture

`bcs-caller-e2e` requires the `test-utils` feature and links a synthetic external
channel through the public channel ingress API. The ordinary `bcs` executable
does not register this channel, even when built with `test-utils`.

Run from `apps/bcs`:

```sh
cargo build -p bcs --features test-utils --bin bcs-caller-e2e
python3 scripts/e2e-test/caller_execution_story.py --binary target/debug/bcs-caller-e2e
```

The isolated story starts a real BCS process and mock HTTP provider on ephemeral
loopback ports. It uses public HTTP APIs for setup and delivery; read-only SQLite
inspection verifies terminal states and credential non-persistence. It covers
Bot-only binding validation, authenticated ingress, missing credentials, concurrent
DM/group identities, queued caller behavior after configuration changes, default
mode, callback/SSE delivery, provider authentication, and durable queue failure
when ephemeral credentials are lost on process restart. Synthetic credentials
must not appear in the database. Both local and preproduction environments run:
local logs remain redacted; temporary preproduction diagnostics must match the
Provider wire values in console, text and JSON outputs.

The recording network provider implements the optional read-only log identity
hook. The story compares real HTTP success, authentication and validation error
logs with completed network records and checks matching identity pairs in the
production text and JSON outputs. It also checks that ordinary request logs and
excluded health requests stay uncorrelated, the SSE terminal produces one close
summary without changing delivery, and restart without a provider emits no
identity. All assertions observe process output after graceful writer shutdown;
they do not invoke domain logic or formatters from the test.

`scripts/caller_e2e_coverage.sh` instruments this
story into the same profile pool as `scripts/e2e_coverage.sh`. Existing global
and changed-line coverage gates remain unchanged. This fixture does not test a
private channel plugin or BaaS runtime behavior.

The same test executable supports `--cache-fixture-port` with the
`task-cache-e2e` cache factory. Its loopback-only producer endpoint populates
coordination cache records in the shared in-memory plugin. The task-cache story
creates task lifecycles through HTTP/WS and checks full-intent lookup/cancellation
through the signed public routes and internal MCP routes. Read-only SQLite
inspection identifies admitted deliveries for correlation fixtures. Missing,
rejected, orphaned and legacy correlations are exercised without modifying
persisted lifecycle state. The fixture is absent from the ordinary BCS binary.

```sh
python3 scripts/e2e-test/task_cache_story.py --binary target/debug/bcs-caller-e2e
```

The companion coverage runner also runs the existing A2A user story with a
deployment-owned gRPC mesh route. The external
[gRPC peer](../a2a_grpc/runner.rs) translates protobuf requests/events to the
story's scripted JSON-RPC peer, preserving its send, streaming, recovery,
polling and cancellation assertions. BCS uses its normal gRPC client; neither
the fixture nor the story calls that client or domain services directly.
The story requires all five RPC methods to reach the peer, which also validates
the deployment routing metadata. Both peer and BCS stop before aggregation.
The gRPC journey uses explicit Workbench cancellation for both active tasks,
then completes polling and named runs. HTTP stories continue to exercise the
invalid-replacement automatic cleanup case.

```sh
cargo build -p bcs --features test-utils --bin bcs-caller-e2e --bin bcs-a2a-grpc-peer
python3 scripts/e2e-test/custom_binding_story.py --binary target/debug/bcs-caller-e2e \
  --grpc-peer-binary target/debug/bcs-a2a-grpc-peer \
  --diagnostics-dir target/cov-e2e/grpc-binding
```

This gRPC invocation is separate from the custom-binding invocation; both feed
the existing E2E profile pool. It does not merge unit-test profiles or change
coverage thresholds or exclusions. See the
[SSE story guidance](../../../../../scripts/e2e-test/README.md) for the shared
Singlebox producer's additional fault journeys and retained gRPC diagnostics.
