# Manager-Worker task panel projection

The authenticated `message.delivery.updated` event and existing session message-delivery
query/cancel responses include an optional `task` on managed Task **dispatch** deliveries.
Ordinary deliveries and result/context legs omit the field. Older consumers can ignore it;
older servers omit it, and clients must not infer tasks from Worker identity or segment count.

`task` allowlists `intent_id` (optional complete assignment identity), `task_ref`, `summary`, `manager_id`, `worker_id`, `worker_name`,
`queued_at`, `started_at`, `last_activity_at`, `finished_at`, and `timed_out`.
Times are server epoch milliseconds; nullable times remain unknown. The reference uses
assignment intent's first eight hexadecimal characters, falling back to the legacy task UUID,
matching the existing Bot-facing task handle. `timed_out` records timeout intent and only
confirms a timeout when cancellation is terminal; cancel_unknown remains unknown.
`last_activity_at` is the latest persisted delivery update, augmented by received Worker
message timestamps in the frontend. A text segment's final state is not task completion.

This is an additive Service API projection consumed by HTTP/WS adapters and the workbench.
Existing Session authorization and source-message audience checks apply unchanged. No new
endpoint, cancellation authority, transport metadata, credentials, or database reads are added.
The projection reads the already-loaded row. Normal delivery views remain unchanged on the wire.

The workbench uses the existing single-delivery cancellation endpoint for both queued and
running tasks; it does not abort every run of a Worker. Cancellation without terminal evidence
is displayed as unknown. Real collaboration messages remain in the chat; explicit UI-only
status records and the historical system ledger summary are hidden only for Manager-Worker groups.

On opening/reconnecting the conversation, task state is rebuilt through the authenticated
source-message delivery query. Recovery queries the latest 100 loaded message IDs plus known active task sources, so a
long-running task is still refreshed after its source scrolls out of the latest page. Requests
are sequential batches of at most 100 IDs, repeated at 10-second intervals: a stationary view
with A active source IDs needs at most 6 × ceil((100 + A) / 100) batches per minute. Normal live updates use WS.
The frontend retains at most 100 terminal task deliveries per session, alongside active tasks.
No hard state is added. Legacy tasks without managed delivery references remain unclassified.

Validation: `cargo test -p bcs-service-api --lib` covers projection, reference compatibility,
non-task/result omission and sensitive-field exclusion. Frontend task projection and component
tests cover status mapping, aggregation, timers, unread acknowledgement, recovery, and scoped
cancellation; the ordinary Active Runs suite remains the regression contract.


The workbench additionally resolves complete `intent_id` values retained from real
`bcs_assign_task` tool receipts through the BCS worker-task status API. It sends
`POST /openapi/v1/collaboration/worker-tasks/status` with `task_refs` in batches of
at most 20. BCS serves this path directly through its protected Principal boundary and
requires a Human identity. The existing collaboration gateway route forwards it
unchanged; Python orchestration `/collaboration/tasks` keeps its existing owner. Internal MCP clients keep using the master-routed
`/tasks/status` and `/tasks/cancel` endpoints unchanged.

At most 100 newest loaded assignments are refreshed every 10 seconds (at most
5 status queries per cycle, 30 per minute, each backed by one bounded indexed
lookup). No bodies are fetched or tasks created by polling. The server's
`not_found`, `ambiguous`, and `unknown` responses remain uncertain; confirmed
terminal rows expire after 24 hours. Relative running/idle seconds may supply
sampled runtime/activity; the frontend does not invent terminal timestamps.
A confirmed response upgrades the original receipt rather than adding another
row. Exact-task cancellation uses the complete intent when no delivery ID is
available; it never falls back to a Bot-wide abort.


The browser adapter depends on the application-owned `TaskHandleService` and
`TaskHandleError` contracts. `TaskHandleApplicationService` translates core validation
failures into `InvalidRequest` and all other core failures into `Unavailable` before
returning to HTTP. Adapters map only those application categories to HTTP 400/503;
core enum variants and diagnostics are not part of the public boundary.

Task identity is the complete `intent_id`, normalized to lowercase from the admitted
assignment. `task_ref` is only a display label and can collide. Clients must join
receipts and deliveries only by complete identity; legacy deliveries without it
remain keyed by `delivery_id` and must not inherit a cancellation identity from a
matching short label. This additive projection uses the existing persisted row
and adds no database columns or repository reads.
