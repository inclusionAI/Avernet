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
