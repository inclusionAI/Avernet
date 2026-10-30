# Manager-worker task presentation

Manager-worker Bot deliveries identify assignments with `T-<8 hex>`. The prefix
comes from the first eight hexadecimal characters of the stored coordination
intent ID (`bcs_intent_<32 hex>`). Legacy assignments without a valid intent ID
use the UUID task ID with hyphens removed. Internal routing, deduplication and
persistence continue using complete IDs. A short reference is not a credential
or evidence of admission, delivery, completion, or safe reassignment.

The v2 assignment producer may add `task_ref` to its compact acknowledgement.
Consumers ignore this display field, even if it conflicts with `intent_id`, and
resolve only the complete validated intent. Inline v1 and cached worker-message
intents remain compatible.

Worker dispatch text:

```text
[来自 Manager 的任务 ref=T-12345678]
<original assignment body>
```

Manager result text:

```text
[任务完成] ref=T-12345678 | Worker：Worker | 任务：summary
---
<response-mode-selected body>
```

Failure, cancellation, confirmed timeout and unconfirmed timeout use headings
`任务失败`, `任务中断`, `任务超时`, and `任务超时，停止结果未确认`, respectively.
An unconfirmed timeout does not declare the Worker stopped. This presentation
change introduces no retry or cancellation policy.

Metadata is bounded to 80 Unicode characters and rendered on one line. The body
is not rewritten. Managed canonical `run_reply.task_result_text` contains the
body, while delivery preparation adds the envelope. Historical abnormal results
with the exact old metadata prefix are read without duplicating that prefix;
successful Worker text is never interpreted as a legacy envelope. Existing human
display messages remain unchanged. Timeout uncertainty keeps its existing human
text and carries separate persisted metadata for Bot-only rendering.

Both queued and legacy direct manager-worker sends use these forms. Ordinary
group messages and legacy worker-message intents do not adopt assignment headers.
No additional database/cache reads or writes are introduced: rendering uses the
already loaded delivery projection and canonical message. The timeout notice
adds bounded metadata to the existing canonical write, not another record.

## Control APIs and persistence

`POST /tasks/status {task_refs:[...]}` and `POST /tasks/cancel {task_ref,reason}`
call application services; native tools use equivalent `task.status/task.cancel`
WS methods. V1 intentionally permits global lookup within the store environment;
refs are not credentials. Status batches contain 1–20 selectors, normalized and
request-deduplicated. SQL uses three task-only indexes (task ID, intent ID and unresolved
intent marker; SQLite migration 038, MySQL 038), one bounded-output batch query and no canonical-body reads. It groups
by full task identity before collision detection; both identity indexes can match. Unresolved request markers remain queryable
as `unknown` until an authoritative assignment or rejection supersedes them;
expired authoritative rows and persisted legacy Result intents still suppress an
obsolete marker. Up to 256 matching process-local legacy intents are supplied as
bound suppression evidence before the SQL candidate limit. If that budget is
exceeded or admission races the snapshot, resolution fails closed (no cancel)
instead of falsely reporting a unique match. The
query plan uses index ranges, but cost still depends on the number of matching
retained identities (a `LIMIT` is not a total scan-cost guarantee).

Uncertainty notices are suppressed by either the authoritative assignment intent
or the normalized task UUID, including native WS assignments without an intent.
The SQL batch uses separate existing-index existence checks for those identities;
this adds no database round trip, schema change, or canonical-message read.

### MySQL migration numbering compatibility

The task-handle migration is MySQL **038_task_handle_lookup.sql**; the merged
dev chain already owns **037_session_speaking_leases.sql**. SQLite remains
**038_task_handle_lookup.sql**. The MySQL renumbering preserves the original SQL
bytes and was explicitly approved for this unmerged PR.

A database that already recorded the old PR's **037_task_handle_lookup.sql**
must not be upgraded as though it had applied the new dev 037 migration. Keep
that database and its version/checksum records intact. Disposable PR validation
must use a fresh database; a retained deployment needs an owned, separately
reviewed reconciliation plan before adopting this chain. Do not delete, relabel,
or overwrite applied migration records to make startup succeed.

Confirmed terminal tasks leave the lookup after 24 hours; running/uncertain
Worker tasks do not. In particular, uncertainty expiry with possible send is
still `unknown`. Rejection records expire from rejection time, independently
of whether their Manager notification was delivered. Rejections are server
System notices, never fake Worker deliveries, with stable `task-rejected:<intent>`
identity. Capacity rejection reuses the already admitted Worker identity
instead of indexing a second pseudo-task, and notifies the Manager of a definite
failure rather than dispatch uncertainty; dispatch uncertainty and timeout uncertainty have separate keys.

The cancel wait defaults to 5000ms, configurable to 100–15000ms in delivery
policy. It subscribes to post-commit delivery changes, with a 1s read fallback.
Only confirmed stops or atomic never-sent cancellation return `cancelled`;
ambiguous/absent refs never change any task. CAS conflicts re-read state. The
cancel service propagates persistence failures as application errors rather than
successful `unknown` responses; unconfirmed transport outcomes remain `unknown`.
The legacy cancellation handler survives the synchronous wait and retains confirmed
stop facts while retrying terminal publication, without repeating abort. Stop
confirmation shares the terminal-event lock with Worker finals; the winning
terminal cannot be replaced by the other path. Uncertainty-notice dedup facts
are persisted independently of mutable error codes.
Legacy Provider exact-task cancellation is refused (`cancel_failed`) because a
Bot/Session abort cannot safely establish exclusive scope from a TTL snapshot;
managed Provider cancellation retains the existing pinned-scope safety checks.

`timeout_s` is optional and clamped to policy min/max (defaults 1/10500 seconds).
The managed deadline starts at send-start; omitted values retain the existing
Provider timeout minus five-minute reserve. Legacy sends keep their existing
context identity and get a retained deadline handler. The legacy completion guard
uses the same clamped custom timeout instead of the fixed default TTL. Queued
HTTP Worker dispatches also carry an explicit Provider execution budget of at
least the task timeout plus the five-minute abort reserve (or the larger existing
Provider budget), matching their callback correlation lifetime. There are no soft-timeout
reminders. Activity timestamps are process-local soft state and are omitted when
lost. No automatic retry classification or retry budget is widened.

## Compatibility release gate

Current tools do not advertise Worker-message emission. Server `task.message`,
cached Worker intents and historical TaskLeg::Message deserialization remain for
one compatibility release. Calls produce a warning and the low-cardinality
`bcs_deprecated_task_message_total` counter. Remove live handling only after old
producers are upgraded, the 24h cache window is over, and durable pending work is
drained. Historical decoding has its own retention boundary. Do not remove these
paths merely to satisfy a source-code grep in the first rollout.
