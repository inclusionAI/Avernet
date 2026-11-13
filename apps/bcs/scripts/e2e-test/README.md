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
body, oversized terminated and unterminated frames, and lag/recovery edges.
Every fault is followed by a successful turn to verify the queue lane is released.
The lag case uses producer timestamps rather than sleeping through a backlog.
The mock's writes are bounded; production frame limits and timeouts are unchanged.

The coverage entrypoint exports its existing `BCS_LOG` path. When supplied, the
story also waits for one `sse.closed` summary for the actual outbound Run and
checks its close reason. An error delivery must become `failed`, while a normal
reply becomes `completed` and appears once in assistant history. The resulting
changed-line percentage is established by the CI artifacts, not by these notes.

## A2A gRPC

The caller companion runs the same A2A user story over the production gRPC mesh
binding, with a loopback-only external peer. Its fixture and invocation are
documented in the [caller companion guide](../../crates/bootstrap/bcs/tests/caller_channel/README.md).
