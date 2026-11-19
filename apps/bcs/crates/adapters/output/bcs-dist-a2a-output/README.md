# Distributed A2A output adapter

`A2aOutputAdapter` implements the pure
[`DistOutputAdapterPort`](../../../service-api/bcs-service-api/src/port/dist_output.rs).
The Worker-side `WorkerOutputReader` injects it after reading neutral committed
output. Neither Worker storage nor core services import A2A DTOs. Other formats
can implement the same port and be selected by the host composition root.

## Encoding

A format-1 `DisplaySnapshot` becomes an ordered A2A artifact list. JSON strings
become text parts; all other JSON values become data parts without flattening.
Artifact identity length-frames the node name and includes the neutral
`(node, artifact_id, class)` tuple. Full views, Upsert replacements and AppendText
deltas therefore address the same artifact, including when node names contain
delimiters. Upsert emits `append: false`; AppendText emits only its text delta
with `append: true`. The adapter does not invent a final-chunk flag. Node and
class metadata accompany artifacts and update events. Thinking may appear as a
streamed event but is invalid inside a persisted display snapshot.

The adapter rejects unknown display versions, unresolved references and non-text
AppendText values. It performs no storage, network, identity generation or quota
accounting. Formatting does not modify the neutral snapshot or event, and its
JSON result is never used as a persisted `ArtifactUpdate`.

## Message sources and limits

`DisplaySnapshot` has no message history or status-message fields. Ordinary
Worker snapshots therefore omit A2A `history` and `status.message`. Explicit
`history` and `status_message` values supplied alongside the neutral snapshot
are preserved; artifacts are not converted into invented messages. Structured
input and interaction payload conversion remain outside this output adapter
(A02).

The Gateway HTTP adapter decodes the formatted artifact fields and supplies the
A2A Task, status, authentication, JSON-RPC error and SSE envelopes. It does not
interpret neutral display units or Upsert/AppendText operations.

## Verification

`cargo test -p bcs-dist-a2a-output` covers full views, JSON values, stable identity,
append semantics, explicit message sources and invalid content. Gateway core and
HTTP tests exercise reference resolution and GetTask/SSE through the injected
Worker facade. These tests do not establish real-provider throughput or a
production runtime composition.
