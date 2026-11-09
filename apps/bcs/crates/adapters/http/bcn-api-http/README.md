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
bounded to 100 items; nextPageToken is opaque. totalSize is the returned page size,
not an unbounded count of all stored tasks.

SendMessage waits for terminal/input-required/auth-required observation unless
returnImmediately is true. Cancellation reports current state after recording
intent, without claiming unobserved downstream cancellation. Terminal tasks cannot
be newly subscribed. Fresh subscriptions begin with the current Task snapshot;
`Last-Event-ID: epoch:sequence` resumes events, and expired epochs return a
replacement Task snapshot. Resets replace aggregate progress; deltas append.
Terminal hard events cause a fresh authorized GetTask to materialize saved output,
then emit replacement artifact (`append=false`, `lastChunk=true`) followed by
terminal status. A2A 1.0 status has no legacy `final` boolean.

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
