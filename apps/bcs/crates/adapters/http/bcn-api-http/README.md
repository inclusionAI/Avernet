# BCN A2A delivery adapter

This opt-in, target-bound router exposes POST `/` and public
`/.well-known/agent-card.json`. It calls only `GatewayService` plus its injected
`Authenticator`; routes neither advance sessions nor invoke connectors.
Mount one router per configured target. Authentication supplies environment,
tenant, namespace and principal; message metadata cannot set caller identity.
Every task operation retains Gateway policy checks and the route's target binding.

The six supported A2A 1.0 JSON-RPC methods are `SendMessage`,
`SendStreamingMessage`, `GetTask`, `ListTasks`, `CancelTask`, and
`SubscribeToTask`. Wire DTOs and errors reuse pinned `a2a-lf = 0.4.2` models.
`A2A-Version` is case insensitive as an HTTP header; major/minor 1.0 is required,
with an optional numeric patch ignored for negotiation. Missing/empty versions
mean unsupported 0.3. Version errors occur before authentication/admission.
Push configuration methods return PushNotificationNotSupported; extended Card
returns UnsupportedOperation; unknown methods return MethodNotFound.

Business messages accept only text/plain. JSON written as text is ordinary
text. Data/File parts, incompatible requested output modes and push configuration
are rejected before submit. Bodies are bounded to 96 KiB; Gateway independently
bounds payloads and rejects nontext on initial messages and original-task replies.
History and list status/time filters are explicitly unsupported. Lists are
bounded to 100 items (default 50); artifacts are omitted unless includeArtifacts
is true. totalSize is the exact pre-pagination collection count. Tasks sort by
status.timestamp descending, with task digest breaking equal-time ties. The
opaque nextPageToken binds caller/target/context/page size and collection revision.
Any collection admission or update invalidates an older token with InvalidParams;
restart listing from the first page. This deliberately trades long-lived cursors
for bounded, internally consistent pages without silent duplicates or skipped
updates. Status/time filters and history remain explicitly Unsupported.

SendMessage waits for terminal/input-required/auth-required observation unless
returnImmediately is true. Cancellation reports current state after recording
intent, without claiming unobserved downstream cancellation. Terminal tasks cannot
be newly subscribed. Fresh subscriptions begin with the current Task snapshot;
unnegotiated Last-Event-ID also starts at the current snapshot, without replaying
old deltas. Only explicit `A2A-Extensions: urn:avernet:bcn:cursor` enables retained
cached-state replay using `Last-Event-ID: epoch:sequence`; the response acknowledges
the URI in A2A-Extensions. The client must retain its already-applied state at that
cursor. A valid negotiated cursor may start with updates and no Task: this is the
extension's replay mode, not the standard first-Task lifecycle. Fresh/expired
cursors rebuild from the current Task. Resets replace aggregate progress; deltas append.
Terminal hard events cause a fresh authorized GetTask to materialize saved output.
Produced output emits replacement artifact (`append=false`, `lastChunk=true`)
followed by terminal status. A task that never produced text skips an empty artifact
update. Clearing previously streamed text uses one empty Text Part with append=false
and lastChunk=true: its canonical textual content is empty, matching GetTask's
omitted no-output artifact. This guarantees textual content equivalence, not identical
artifact inventory. Only the first snapshot is a Task object; later Reset snapshots
become standard artifact replacement and status updates. A2A 1.0 status has no legacy `final` boolean.

The Card declares two optional extension URNs:

- `urn:avernet:bcn:interaction-id`: Message.metadata carries a full typed value
  `{ "scope": { "environment": "local", "tenant": "t", "namespace": "bcn-v1" }, "value": "..." }`.
  Scope must match authentication; task/context still address the original task.
  Omission is valid only when Gateway finds exactly one current interaction.
  Each Task.status.message part carries the same metadata key for its question.
  Questions never become artifact output. GetTask and fresh/recovered subscriptions
  expose all current questions, including after one parallel reply is consumed.
- `urn:avernet:bcn:cursor`: Last-Event-ID format and replacement/terminal rules above.

Safe public errors omit internal storage/transport diagnostics. No production
credentials, identity strategy or endpoint policy are supplied by this adapter.

Task/Get/List/Reset status.timestamp is the exact stored updated_at_ms, formatted
as RFC3339. The store updates ordering atomically whenever this timestamp changes,
including soft token projection. Nonterminal status deltas carry no publication
time, so their optional timestamp is omitted; terminal status uses authoritative
GetTask. Admission/publication rejects times beyond year9999 before persistence.
Collection authorization occurs before any index/count read and grants visibility
of the full caller/target/optional-context collection, including its count. Current
per-task authorization remains fail-closed; policies must have consistent visibility
within an authorized collection. Targetless listing requires an explicit policy hook.

Blocking reply submission obtains SubmitOutcome from Gateway: its interaction ID
comes from the retained durable reply receipt, including originally inferred IDs.
A fresh reply waits for that specific question to resolve; an already-consumed
exact retry returns the current interrupted view without waiting for another
question. Missing receipt evidence fails explicitly. A new reply whose checkpointed
question has not yet reached the durable public snapshot receives a retriable
Conflict before acceptance; retry after question publication. Deduplicated replies
are checked first and remain valid after their question has disappeared.

Protocol references are pinned to [A2A v1.0.0](https://a2a-protocol.org/v1.0.0/specification/),
including default blocking SendMessage (§3.1.1), lifecycle event ordering (§3.1.2),
and the nonempty Artifact.parts requirement (§4.1.7).
