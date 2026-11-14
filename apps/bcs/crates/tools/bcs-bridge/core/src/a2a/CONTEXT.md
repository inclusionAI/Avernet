# Bridge A2A server adapter

Provides an opt-in A2A 1.0 HTTP+JSON / JSON-RPC server, public Agent Cards and
SSE on the existing gateway listener. Consumes the pinned `a2a-lf` 0.4.2 wire
models and Bridge's existing `runtime::dispatch`, engine-neutral run events and
cancellation handles. It does not implement an engine, select a BCS outbound
binding, or own Bot registration policy. Public and internal Bridge distributions
receive the adapter through `bcs-bridge-core`; OCB needs no private implementation.

The Provider is the authentication principal: its existing gateway bearer can
access all of its configured Bots. Task/message lookup additionally checks the
Bot, and A2A contexts use a separate session-key prefix. Public URLs come only
from validated configuration, never request Host/Forwarded headers. Public Cards
contain neither tokens nor engine configuration/working-directory paths.

Admission is serialized per Provider before message-ID deduplication and runtime
session acquisition. Once accepted, a background owner drains the runtime stream
even when the HTTP caller disconnects. HTTP subscribers are observers, not
execution owners. Only exact Task cancellation or existing runtime lifecycle
rules stop execution. The Task terminal is published after runtime cleanup has
released the session slot; a successful cancel response is not merely an ACK.
Unsupported native interaction waits fail explicitly and deny/cancel the run.

Tasks are bounded process-local projections, not durable jobs. At most 128 Tasks
are retained per Provider, with 1 MiB input and answer limits and a five-minute
terminal TTL; admission fails instead of evicting active or unexpired Tasks.
The adapter discards the completed run's gateway replay buffer after draining it.
Each subscriber uses a bounded 64-event broadcast buffer; lag closes only that
subscription with a protocol error, and a new subscription starts with a current
snapshot. No token replay cursor or restart Task recovery is advertised.

## Access cost and compatibility

Engine text deltas are transient Working status messages, not answer artifacts:
Claude/Codex commentary need not be a prefix of their authoritative final text.
Only that final text populates the single `answer` artifact; an empty answer is
not replaced by progress. Streams remain live, but clients must render status
messages separately to show progress. The existing BCS client does not render
these messages as answer deltas; its append-only projection remains unchanged.

Each progress chunk updates a byte counter and replaces the latest status
message, then broadcasts one event: no SQL, credential reads, growing-queue
scans, accumulated transcript or full accumulated-output copies per token.
Cumulative progress and the final answer are independently bounded to 1 MiB.
Runtime retains its existing session mapping/inject persistence behavior.
Get/List/admission lazily sweep at most 128 Task entries plus their message-ID
index; List snapshots only the requested page (maximum 100). Existing gateway
and plugin protocols are unchanged and A2A routes are absent without `[a2a]`.

Contract and usage: `../../../cli/A2A.md` (from this directory).
Conformance: `core/tests/a2a_server.rs`, local Task/config unit tests, and the
BCS outbound client's Bridge interoperability test.
