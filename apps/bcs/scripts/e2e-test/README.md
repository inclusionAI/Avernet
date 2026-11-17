# Live E2E coverage stories

`e2e.sh` runs HTTP/Workbench user journeys against the shared Singlebox stack.
`../caller_e2e_coverage.sh` and `../oauth_e2e_coverage.sh` run isolated companion
processes. All producers finish before `../e2e_coverage.sh` aggregates their
runtime profiles. Unit-test profiles are outside this E2E report.

## Deleted group recipients

The default `stories` inventory runs
[`queued_target_failure_story.py`](queued_target_failure_story.py) against the
shared instrumented BCS and Provider mock. It creates a Group/Session, deletes
one Provider Bot through HTTP, and sends Workbench messages with explicit
mentions. Public delivery queries must retain the deleted recipient as Failed
with `delivery_target_unavailable`, while the real scheduler completes healthy
recipients. The journey covers deleted Send/Inject targets, idempotent replay,
all reply targets failing, and absence of duplicate/failed Provider requests.
Queue policy is paused through its CAS API for deterministic admission checks
and restored during cleanup. No database edits or unit-test profiles supply
coverage. Run it against a local-auth BCS and the existing Provider mock:

```bash
python3 apps/bcs/scripts/e2e-test/queued_target_failure_story.py \
  --base-url "$BCS_API_BASE_URL" --mock-url "$BCS_E2E_MOCK_BASE_URL" --human 001
```

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

## TC friend-auth-sync authorization

The `edge_permission` suite loads
[`tc_friend_auth_sync.sh`](tc_friend_auth_sync.sh) for the Human-to-TC-Bot
work-order callback journey. It provisions a protected Bot owned by `85020`,
then submits a pending request as a different Human (`153998`). In Singlebox's
local auth mode, each call explicitly selects its `X-Mock-User-Id`; the signed
`X-Avernet-Principal` forwarded to work-order/sync ports carries the same Human.
A forwarded principal alone does not select the local authenticated caller.

The applicant's approval must return 403 without a friend-auth-sync call. The
owner's approval must succeed and produce exactly one grant for applicant
`153998`, preserving owner `85020` in the re-addressed principal (`iss=bcs`,
`aud=backend`). Both identities remain explicit during cleanup and listing so
this scenario does not inherit the suite's default mock user. The test uses the
existing Provider/Judge HTTP mock and does not bypass receiver authorization.

## Invitation membership and consent

The default `stories` inventory includes `invitation_story.sh` /
[`invitation_story.py`](invitation_story.py). It follows real V1 Group/Session
share links using independently authenticated Humans and a disposable owned Bot.
BCS itself issues the invitation tokens and persists all resources; the story
never edits the database, calls service internals or manufactures invitation
signatures.

The journey distinguishes Bot control and parent-Group access from direct Human
membership, proves preflight does not join, explicitly accepts before checking
member navigation metadata, preserves Human modes/message scope on retries, and
observes member removal on the next request. It also checks invalid/expired links
(including an already joined Human), deleted targets, forbidden sharing, the
minimal response shape and private/non-cacheable responses. All Group/Session/Bot
fixtures are cleaned up via HTTP, including on story failure. Failure messages
omit token-bearing URLs and response bodies.

For focused validation against a running local-auth/gateway-principal BCS:

```bash
python3 apps/bcs/scripts/e2e-test/invitation_story.py --base-url "$BCS_API_BASE_URL"
```

Running the default coverage flow executes it against the shared instrumented
server before profile aggregation. Unit-test profiles remain outside the E2E
report; a focused story's global line/method percentages are not the complete
Singlebox stack's global coverage result.
