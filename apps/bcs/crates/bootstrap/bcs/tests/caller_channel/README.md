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
must not appear in the database or debug logs.

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
