# Live E2E coverage stories

`e2e.sh` runs HTTP/Workbench user journeys against the shared Singlebox stack.
`../caller_e2e_coverage.sh` and `../oauth_e2e_coverage.sh` run isolated companion
processes. All producers finish before `../e2e_coverage.sh` aggregates their
runtime profiles. Unit-test profiles are outside this E2E report.

## Provider SSE failure recovery

`caller_identity_story.py` invokes `provider_sse_cases.py` after acquiring a
speaking lease and enabling its managed queue policy. The existing HTTP Provider
mock scripts each reply by its unique `provider_bot_ref`; the story enters BCS
through the public Workbench WebSocket and checks public delivery status/history.
It never posts a synthetic final callback to make an SSE case succeed.

The cases cover normal terminal replies, EOF before terminal, a truncated HTTP
body, and lag/recovery edges.
Every fault is followed by a successful turn to verify the queue lane is released.
The lag case uses producer timestamps rather than sleeping through a backlog.
Inputs remain small because the instrumented debug reader repeatedly scans its
accumulated frame buffer on each socket chunk. The existing
[SSE loop tests](../../crates/adapters/http/bcs-provider-http/src/sse_loop_tests.rs)
cover the 8 MiB terminated/unterminated frame boundary without that socket cost.
Production frame limits and timeouts are unchanged.

The coverage entrypoint exports its existing `BCS_LOG` path. When supplied, the
story also waits for one `sse.closed` summary for the actual outbound Run and
checks its close reason. An error delivery must become `failed`, while a normal
reply becomes `completed` and appears once in assistant history. The resulting
changed-line percentage is established by the CI artifacts, not by these notes.

## A2A gRPC

The caller companion runs the same A2A user story over the production gRPC mesh
binding, with a loopback-only external peer. Its fixture and invocation are
documented in the [caller companion guide](../../crates/bootstrap/bcs/tests/caller_channel/README.md).
It cancels active tasks through public `chat.abort` before proceeding to polling
and named runs. HTTP journeys also retain the invalid-replacement automatic
cleanup regression; gRPC coverage does not depend on that separate behavior.

The CI supervisor supplies a diagnostics directory inside its report tree:
`bcs/grpc-binding/`. It retains `server.log`, `grpc-peer.log`,
`network-traces.json` (written after graceful BCS shutdown), and `peer-state.json`
(captured before cleanup). Peer diagnostics record method/task identities,
Artifact append/final flags, states and observed cancellations, without request
bodies, configuration or credentials. These files survive story failures and are
included in the existing Singlebox artifact upload.
