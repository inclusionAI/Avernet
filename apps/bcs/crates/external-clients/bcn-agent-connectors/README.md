# BCN agent connectors

This external-client crate implements the neutral `bcn-runtime-api::port::AgentConnector`
for one frozen target. Bootstrap supplies the selected Card/Provider endpoint,
policy-checked `ClientWithDefaults<reqwest::Client>` and credential defaults.
DNS, redirects, TLS, credential resolution and target authorization remain caller
responsibilities. No discovery, credential lookup, transport fallback, callback
registration or automatic resend occurs here. Peer diagnostics are not returned.
The legacy coordination runtime and its in-flight sessions are unchanged.

## Binding and capabilities

Every send checks the complete frozen binding and scoped identities. Handle-based
operations check target and binding version before I/O. SDK types stay in this
crate and the existing `bcs-a2a-http` external client; neutral APIs gain no HTTP
or SDK dependency. Existing REST and JSON-RPC clients implement all five A2A wire
operations. Required Card extensions and unsupported capability claims are rejected.
The current projection negotiates text only; data/file input is Unsupported.

Provider 2.0 advertises streaming with no query, subscription, input continuation
or idempotency guarantee. `send_message` is explicitly Unsupported; the runtime
selects `send_streaming_message` from frozen capabilities. Provider capabilities
may advertise exclusive-context cancellation, but never single-task cancellation.
`chat.abort` additionally requires the caller's exclusive-context assertion, a
previously observed remote run ID, and a successful response naming that exact run
among the affected IDs. An ACK/boolean without matching evidence remains Unknown.

Direct context reuse is different from continuing an input-required task. A new
Direct task supplies a context-only handle (the runner clears terminal task IDs).
Both connectors reuse the actual remote context without reviving an old remote
run. Provider rejects handles with a remote task ID on send because input
continuation is unsupported. A2A checks its input-continuation capability before
sending to a remote task. Direct A2A Message responses preserve server-assigned
context without inventing a task ID. Local session/message IDs never masquerade
as server-assigned A2A context/task IDs.

Scoped dispatch identity is serialized deterministically into Provider request
`id` and `X-BCN-Message-Id`; the A2A message ID is the scoped message identity.
Stable identifiers do not authorize replay. `expires_at_ms` supplied by the
runner is its supervision deadline, not a downstream enforced-expiry guarantee.
These connectors advertise Unsupported idempotency and do not transmit or rely
on that field for duplicate suppression or downstream expiry.

## Observation and bounds

Provider SSE uses shared `bcs-protocol` block parsing, typed events and sequence
classification. UTF-8 is decoded only after complete lines; CR/LF/CRLF, comments,
and multiline data are supported. Duplicate/regressed sequences are ignored;
gaps, malformed/unsupported content and identity changes stop observation.
Final nonempty text replaces accumulated text; empty final text preserves it.
A response with JSON ACK, failed transport or premature EOF never sends another
POST. Provider ACK is uncertain because callback completion is not wired here.

Provider bounds are 256 KiB per frame/control response, 8 MiB wire bytes per stream observation and
64 KiB serialized accumulated output. Constructor timeout bounds headers, each
stream read and the entire abort response; runtime supervision owns overall task
time. A2A reuses existing wire bounds (1 MiB SSE event and 8 MiB unary response)
and adds an 8 MiB cumulative decoded-event budget per stream observation,
including empty artifact metadata, plus a 64 KiB projected-output limit. Exceeding
the observation budget retains the known handle and partial output without
confirming remote termination. Unary Message and Task replies carry an
optional `observation_error` separately from the remote execution error. Old
payloads omit it and remain readable. Projection failures preserve the actual
remote status and any actual handle; direct Message replies stay Message replies,
including when no context was returned. No remote task ID is invented. No token/event adds storage or credential-store calls.

This MVP accepts only text/plain business messages. The admission layer must
reject Data/File parts before accepting work, including continuation replies;
connectors additionally reject nontext inputs before network I/O. Required Card
text input/output modes and acceptedOutputModes=text/plain make the negotiation
explicit. Multimodal stream/artifact projection requires a separate extension.

Streams emit a validated Accepted handle before local observation loss.
`ObservationFailed` retains bounded partial output and does not establish remote
termination. Explicit terminal output overflow or unsupported terminal content retains termination evidence via
`FailedWithOutput`. Remote terminal snapshots preserve their handle/status.
Query, subscribe and cancellation always use the frozen endpoint and exact
remote task ID. Neither a transport error nor dropping a stream cancels a task.

## Validation

`tests/a2a.rs` and `tests/bcn_sse.rs` exercise real connector consumers against
loopback HTTP fixtures, including protocol DTOs, requests, state projection,
uncertainty, bounds, context reuse and cancellation evidence. Shared Provider
framing remains covered by the original `bcs-provider-http` tests. These tests
establish protocol behavior only, not production durability or capacity.
