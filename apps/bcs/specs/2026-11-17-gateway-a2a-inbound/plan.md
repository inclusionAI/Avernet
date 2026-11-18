# Plan: Gateway — inbound A2A server

Status: reviewed on the PR (2026-11-17) and implemented; see "As built" at the
end for where the code differs from this plan.

Review outcomes: Q1, Q2 accepted as proposed and treated as signed off by the
executor team; Q4, Q5, Q7–Q10, the contract diffs, the admission order and the
task index (decisions row 32) accepted; Q6 changed to follow A2A 1.0; a
`bcs-service-api` version bump added. Q3 was not answered and stays a proposal.

Sources: the decisions page (rows 1–31), the Gateway deep dive, the two PR
walkthroughs, and `dev` at `5c3e30ea`. Store mapping: [research.md](research.md).

## Approach

Five layers, each in its own crate, in the BCS call direction: the A2A adapter
calls the application service, which calls the core service, which calls the
repository ports, which the store providers implement over `bcs-store-api`.
Admission reuses the shared `StoreInboxProducer`; the Gateway adds the readers
(`TaskReaderPort`, the `get_spec` read) and one shared blocking read per served
session. `tasks/get` returns the stored snapshot as is; a stream is the
snapshot, then the `ev` entries after it, then live entries.

## Questions for review

Numbered so you can answer by number. Q1–Q3 cross into the Worker's side.

1. **Stored shape of an `ev` entry (Worker writes it, we read it).** No codec
   exists in `bcs-dist-store`, and the Notion samples (flat `type` / `state` /
   `text_ref`) don't match the merged `OutputEvent`. Proposal: fields `v`,
   `tid`, `kind`, `body` (JSON of the merged `OutputBody`), id = `EventId`
   (research §4). Needs the executor team's sign-off like the other codecs.
2. **Format of the display state and of artifact content.** The merged model
   carries them as opaque `Payload`s, and decision 25 says the format is still
   to be agreed. The adapter must turn them into A2A objects. Proposal: they
   are already A2A-shaped JSON, passed through:
   snapshot = `{"artifacts": [...], "history": [...], "status_message": {...}?}`,
   artifact content = `{"artifact": {...}, "append": bool, "last_chunk": bool}`.
   Isolated in one adapter file so a different answer is a one-file change.
3. **Who resolves `Payload::Ref` (large content).** Proposal: the core service
   inlines a referenced snapshot and referenced artifact content through
   `BlobStorePort` before returning them; `result_ref` in a terminal event is
   not fetched in the stream (the final Task carries the final snapshot).
4. **Session id of a new session must be deterministic.** `admit` lives in the
   session's shard and `input_id` includes the session id, so a retried first
   message can only be de-duplicated if the retry computes the same session
   id. Proposal: derive it from `(tenant, principal, group, messageId)`:
   shard = hash mod `shard_count` (configuration), suffix = 16 hex digits of
   the hash. The decisions page says "shard + random part" and leaves shard
   choice open; this keeps the format and replaces "random" with "derived".
5. **`SessionStorePort` in the Gateway process.** The distributed README
   forbids a one-method spec-reader port, and the Worker's provider doesn't
   exist. Proposal: a Gateway-side provider whose `get_spec` is real and whose
   Worker operations (`acquire`, `load`, `commit`, …) fail with
   `NotApplied { code: "worker_only_operation" }`. It is not a delayed-contract
   stub: the Gateway must never call them.
6. **Subscribe to a finished task — settled in review: follow A2A 1.0.**
   `SubscribeToTask` on a task in a terminal state returns
   `UnsupportedOperationError` (-32004); the client reads the final state with
   `GetTask`. This is an adapter rule: the application's snapshot-first stream
   still yields the final snapshot and ends, which `SendStreamingMessage` needs
   when a retried send finds its task already finished.
7. **Reply without an interaction id.** A2A has no field for it. Proposal:
   when the task has exactly one open interaction, a message with that
   `taskId` answers it; more than one open → `Invalid { code:
   "interaction_ambiguous" }`.
8. **Sibling crate for the Gateway's providers** (`bcs-dist-gateway-store`,
   depending on `bcs-dist-store`) instead of new modules in the shared crate.
   Reason: the Connector session edits `bcs-dist-store` at the same time; only
   the four shared changes in research §4 touch it.
9. **No process wiring in this PR.** No production `bcs-store-api` plugin
   exists in Avernet (decision 26) and task I1 owns composition. The adapter
   exposes a `router(...)`; an end-to-end test composes all layers over the
   test store model.
10. **Branch.** `totalfrank/sweet-babbage-gcypcq` cannot be pushed: a remote
    branch named `totalfrank` already exists. Proposal:
    `totalfrank-gateway-a2a-inbound`.

Proposed decisions-page row (apply only after you agree):

> **32 — Per-session task index** (2026-11-17): `…:tasks:<sid>` (sorted set,
> member = task id, score = `created_at` ms), written only by the Gateway,
> right after the task creation in admission, with `ZADD NX`. `tasks/list`
> requires a session filter in the MVP, pages the index by score, and reads
> each task's snapshot (`ResultStorePort::get_final` after the task key
> expired). No expiry for now; retention follows the final-records task.
> *Why:* tasks of a session can be listed without scanning keys; `NX` makes
> the admission retry harmless.

## Affected components

| Crate | Layer | Role |
| --- | --- | --- |
| `crates/service-api/bcs-service-api` | contracts | approved edits (below) |
| `crates/test-support/bcs-test-support` | test support | harness functions follow the contract edits |
| `crates/services/bcs-dist-store` | shared store | `tasks` key, index write in admission, `ev` codec, `Duplicate` removed |
| `crates/services/bcs-dist-gateway-store` (new) | store | `TaskReaderPort` provider, `SessionReaders`, `get_spec` provider |
| `crates/services/bcs-dist-gateway` (new) | core | `DistGatewayCoreService` |
| `crates/application/bcs-app-dist-gateway` (new) | application | `DistGatewayService`: identity, routing, admission, query, subscribe, wait, cancel, list |
| `crates/adapters/http/bcs-dist-gateway-http` (new) | delivery adapter | A2A 1.0 JSON-RPC + SSE, resume id, agent card, error mapping |

Not touched: Connector-owned code, the Worker side, `crates/bootstrap`.

## Contract changes (stop before applying)

Already approved in the brief; shown here as the exact edits. To be called out
in the PR description for the executor team.

```diff
# crates/service-api/bcs-service-api/src/port/repo/distributed/inbox.rs
 pub enum AdmitOutcome {
     Accepted(AdmissionReceipt),
-    Duplicate(AdmissionReceipt),
     ContentConflict,
     Quarantined,
 }
-    /// Retry completes partial activation before returning Duplicate. Conflicting
+    /// Retry completes partial activation before returning Accepted; an input
+    /// already admitted with the same digest is Accepted too. Conflicting
```

```diff
# crates/service-api/bcs-service-api/src/core/dist_gateway.rs
-pub enum CoreAdmission {
-    Accepted(AdmissionReceipt),
-    Duplicate(AdmissionReceipt),
-}
+pub struct CoreAdmission(pub AdmissionReceipt);
```

```diff
# crates/service-api/bcs-service-api/src/application/dist_gateway.rs
 pub enum GatewayError {
+    #[error("task is finished and cannot be canceled")]
+    NotCancelable,
     ...
 }
+#[derive(Debug, Clone, Copy, PartialEq, Eq)]
+pub enum WaitUntil { InputOrFinal, Final }
+#[derive(Debug, Clone, PartialEq, Eq)]
+pub struct WaitTask {
+    pub tid: Tid,
+    pub until: WaitUntil,
+    /// Coverage seen before the admission this wait follows; states at or
+    /// before it don't end the wait (a reply to an open question).
+    pub after: Option<EventId>,
+    pub timeout: std::time::Duration,
+}
 pub trait DistGatewayService: Send + Sync {
+    /// Returns once the task reaches the requested state AND the stored
+    /// snapshot covers that event; at the timeout returns the current view.
+    async fn wait_task(&self, caller: &GatewayCaller, request: WaitTask)
+        -> Result<TaskView, GatewayError>;
 }
```

Version (API-1): removing a public variant is a breaking change, so
`bcs-service-api` goes `6.1.0` → `7.0.0`, with a "Version 7.0.0" section in its
`CONTEXT.md` listing the three edits. The Connector PR (#4133) bumps to `6.2.0`
(additive); whichever lands second rebases onto the other's version, so the
final value is `7.0.0` or `7.1.0`. #4133 does not name `Duplicate` in a match,
so it compiles before and after this edit. The contract edit stays the first,
self-contained commit; on every rebase the tree is searched again for
remaining `Duplicate` uses (README, harness, model).

```diff
# crates/service-api/bcs-service-api/Cargo.toml
-version = "6.1.0"
+version = "7.0.0"
```

`CoreAdmission` becoming a struct is my reading of "remove
`CoreAdmission::Duplicate`": a one-variant enum says nothing. Say so if you
prefer the enum kept. Also updated in the same commit: the port doc, the
distributed `README.md` ("before returning Duplicate"), the harness in
`bcs-test-support/src/contract/distributed/{inbox,services,state}.rs`, and
`bcs-dist-store`'s `compare` and tests.

Two documented deviations from the merged port docs, both by decision 30 (no
code-shape change, comments only):

- `TaskReaderPort::restore` says "fold snapshot S and events (S,H]". The MVP
  returns the stored snapshot with the cursor at S; the subscriber receives the
  entries after S as events.
- `DistGatewayService::cancel` keeps returning a receipt; the adapter follows
  it with `wait_task(Final, 4 s)` to build the A2A Task (decision 28a).

## Data model changes

No SQL. Store keys and shapes:

```text
# new key (bcs-dist-store/src/layout.rs)
bcn:v1:c01:main:s0042:tasks:s0042.7f3a9c       ZSET  member = tid, score = created_at ms

# new shared codec (bcs-dist-store/src/codec/ev.rs), see Q1
…:ev:s0042.7f3a9c  12-1  v 1  tid s0042.7f3a9c.b21e  kind status  body '{"kind":"status",…}'
```

Ids and tokens generated by the Gateway:

```text
sid       s0042.9c41e7a05b3d2f18          s<shard:04>.<16 hex of H("sid", tenant, principal, group, messageId)>
tid       <sid>.5521c0de9a41e7b0          <sid>.<16 hex of H("tid", sid, messageId)>
input_id  in-<32 hex>                     user:   H("user", tenant, principal, group, sid, messageId)
                                          reply:  H("human_reply", principal, sid, tid, messageId)
                                          cancel: H("cancel", principal, sid, tid, request_id)
digest    sha256:<64 hex>                 canonical JSON of {kind, tid, interaction?, content|reason}
cursor    backend "tbase", generation "1", value "<commit>-<index>"; "0-0" = before the first entry
page      "1." + base64url("<score>:<tid>")
SSE id    "1." + base64url("<tid>:<value>")   format 1 implies backend "tbase", generation "1"
```

`H` = SHA-256 over length-prefixed fields. Generation is the constant `"1"`
with a comment that it only changes if a future rebuild procedure recreates a
stream. `request_id` of a cancel = the JSON-RPC request id.

## API / interface changes

Shared store (`bcs-dist-store`):

```rust
// src/layout.rs
pub fn tasks(&self, sid: &Sid) -> Result<String, StoreError>;      // …:tasks:<sid>

// src/codec/ev.rs
pub fn encode_ev_entry(event: &OutputEvent) -> Result<(StreamId, Fields), StoreError>;
/// Ok(None): not decodable or unsupported version; the reader skips it.
pub fn decode_ev_entry(entry: &StreamEntry) -> Option<OutputEvent>;
pub fn event_id(id: StreamId) -> Option<EventId>;
pub fn stream_id(id: EventId) -> StreamId;
```

```diff
# src/inbox/producer.rs — admit(), right after the task create-once
+        // Listed even when the task already existed: InsertOnly keeps the
+        // first score, so a retry repairs a missing index entry.
+        self.zset.add(&tasks_key, &[entry(tid, created_at)], ZAddMode::InsertOnly)
```

Store providers (`bcs-dist-gateway-store`):

```rust
pub struct GatewayStoreConfig {
    pub backend: BackendId,            // "tbase"
    pub read_block: Duration,          // 1 s
    pub read_batch: NonZeroU32,        // 100
    pub subscriber_buffer: usize,      // 256
}
pub struct StoreTaskReader { /* json, zset, stream, results, layout, readers */ }
impl StoreTaskReader {
    /// Fails when a store lacks AtomicJson, ZSet or Stream.
    pub fn new(json, zset, stream, results: Arc<dyn ResultStorePort>,
               layout: KeyLayout, config: GatewayStoreConfig) -> Result<Self, StoreError>;
}
impl TaskReaderPort for StoreTaskReader { /* restore, validate_resume, follow, list */ }

pub struct GatewaySessionStore { /* json, layout */ }
impl SessionStorePort for GatewaySessionStore { /* get_spec real; the rest worker_only_operation */ }
```

Core (`bcs-dist-gateway`):

```rust
pub struct DistGatewayCore {
    inbox: Arc<dyn InboxProducerPort>,
    tasks: Arc<dyn TaskReaderPort>,
    sessions: Arc<dyn SessionStorePort>,
    blobs: Arc<dyn BlobStorePort>,
}
impl DistGatewayCoreService for DistGatewayCore { /* admit, restore_task, validate_resume, follow_task, list_tasks */ }
```

Application (`bcs-app-dist-gateway`):

```rust
pub struct DistGatewayConfig {
    pub shard_count: u32,
    pub snapshot_cover_retry: Duration,   // 50 ms between task re-reads
    pub snapshot_cover_limit: Duration,   // 2 s, then return what is stored
    pub max_page: NonZeroU32,             // 50
}
pub struct DistGateway { /* core, groups: Arc<dyn DistGroupDirectoryPort>, clock, config */ }
impl DistGatewayService for DistGateway { /* submit, get_task, subscribe, wait_task, cancel, list_tasks */ }
```

Adapter (`bcs-dist-gateway-http`):

```rust
pub struct GatewayHttpConfig {
    pub blocking_send_wait: Duration,   // 60 s
    pub cancel_wait: Duration,          // 4 s
    pub keep_alive: Duration,           // 15 s
    pub max_message_bytes: usize,       // 256 KiB
}
/// The caller is taken from a request extension set by the auth layer in
/// front of this router; a request without one is refused.
pub fn router(gateway: Arc<dyn DistGatewayService>, config: GatewayHttpConfig) -> axum::Router;
```

```text
POST /a2a/{group}                               JSON-RPC 2.0, header A2A-Version: 1.0
GET  /a2a/{group}/.well-known/agent-card.json   capabilities.streaming true, pushNotifications false

SendMessage             submit → returnImmediately ? get_task : wait_task(InputOrFinal, 60 s)
SendStreamingMessage    submit → subscribe(Snapshot)                       [SSE]
GetTask                 get_task
SubscribeToTask         Last-Event-ID decodes ? subscribe(Resume) : subscribe(Snapshot)   [SSE]
                        task already terminal → -32004 UnsupportedOperation, no stream
CancelTask              cancel → wait_task(Final, 4 s)
ListTasks               list_tasks (contextId required)
*PushNotificationConfig, or a push config in SendMessage → -32003
```

| `GatewayError` | JSON-RPC |
| --- | --- |
| `NotFound` | -32001 TaskNotFound |
| `NotCancelable` | -32002 TaskNotCancelable |
| — (`SubscribeToTask` on a finished task) | -32004 UnsupportedOperation |
| `AccessDenied` | -32600 with reason "access denied" (to align with the auth Issue) |
| `Conflict`, `Invalid` | -32602 |
| `Quarantined` | -32603, reason "session unavailable" |
| `Unavailable`, `OutcomeUnknown` | -32603 with `data.retryable = true` |

## Key behaviour

**Admission (application → core → shared provider).** The application builds
ids, the envelope, and for a new session the `SessionSpec` from
`DistGroupDirectoryPort::resolve`; for a new task the `Submitted` snapshot. The
core checks before calling the provider:

| Input | Core check | Failure |
| --- | --- | --- |
| new session | spec scope = caller scope | `ScopeMismatch` |
| new task, existing session | `get_spec` exists and its scope = caller scope | `NotFound` / `ScopeMismatch` |
| reply | task exists, same scope and session, status input-required or auth-required, interaction open | `NotFound` / `ScopeMismatch` / `InvalidTransition` |
| cancel | task exists, same scope, not terminal | `NotFound` / `ScopeMismatch` / `InvalidTransition` → `NotCancelable` |

Provider outcomes: `Accepted` → receipt; `ContentConflict` → `Conflict`;
`Quarantined` → `Quarantined`; `OutcomeUnknown` → `OutcomeUnknown { request_id }`.
The provider's write order is unchanged apart from the index write:
`GET admit → spec/task create-once → ZADD tasks NX → XADD inbox → listener read
(D1 stub) → ZADD active LT → XADD wake → SET admit NX`.

**Subscribe (application).**

```text
Snapshot:  restore → Snapshot{view, cursor}
             terminal or expired → end
             else follow(cursor)
Resume(c): validate_resume(c)
             Valid        → ResumeAccepted{c}, follow(c)
             Gap          → as Snapshot
             InvalidScope → AccessDenied
follow:    Events{…} per batch; end after a batch holding input-required,
           auth-required or terminal; Gap → restore again, emit Snapshot, go on
```

`validate_resume` in the provider: wrong backend or generation → `Gap`; task of
another scope → `InvalidScope`; task finished or expired → `Gap`; position
before `snapshot_event_id` → `Gap`; otherwise `Valid`.

**SessionReaders (store provider).** One task per session with subscribers:

```rust
// bcs-dist-gateway-store/src/readers.rs
struct Reader { position: StreamId, tx: broadcast::Sender<Arc<Batch>>, subscribers: usize }
// join(sid, after):
//   no reader  → start one at `after`; no catch-up
//   reader at P → subscribe to tx under the lock, then range (after, P] in pages,
//                 then the receiver, dropping ids ≤ max(after, P)
// loop: read(one key, block 1 s) → decode → send; never blocks on a subscriber
// leave: last subscriber gone → the loop stops after its current read
```

A subscriber that falls more than `subscriber_buffer` batches behind gets
`NotApplied { code: "subscriber_lagged" }` (→ `Unavailable`, retryable) and its
stream ends; the client resumes from its last id. Three consecutive read
failures end the reader and its subscribers with the read error. Each
subscriber's stream keeps only its own task's entries; no per-event `task` read.

**Wait (application).** Read the snapshot; if it already satisfies `until` and
is newer than `after`, return it. Otherwise follow from the snapshot's cursor
until an event satisfies `until`, then re-read the snapshot every 50 ms (at
most 2 s) until it covers that event, and return it. At `timeout`, return the
current snapshot.

**Query and list.** `get_task` = the stored snapshot (expired → final record).
`list_tasks` requires a session, reads one index page, then each task's
snapshot; the status filter is applied after the read, so a page can be shorter
than `limit` while `next` is still set.

**Delayed contract D1.** Unchanged: `read_listener` stays the stub in
`bcs-dist-store`; its test already asserts `need_contract_not_implemented`. An
admission test in the Gateway asserts `wake` is written on every input.

## Store access cost

| Operation | Calls |
| --- | --- |
| admit, new task | existing sequence + 1 `ZADD` (index) |
| get task | 1 `get_json`; + 1 `get_final` when expired; + 1 blob read when the snapshot is a reference |
| subscribe | 1 `get_json`, then 0..n `range` pages of 100 for catch-up |
| live stream | 1 blocking `read` per second per **served session** per Gateway, whatever the subscriber count |
| wait | 1 `get_json` + the shared reader + up to 40 `get_json` in the 2 s cover window |
| list page | 1 `ZRANGE` + up to `limit` (≤ 50) `get_json`, + `get_final` per expired task |

A subscriber with no coverage on a long session reads the untrimmed entries of
earlier tasks once (pages of 100, filtered by `tid`).

## Dependencies

```diff
# apps/bcs/Cargo.toml (workspace members + dependencies; four new crates)
+    "crates/services/bcs-dist-gateway-store",
+    "crates/services/bcs-dist-gateway",
+    "crates/application/bcs-app-dist-gateway",
+    "crates/adapters/http/bcs-dist-gateway-http",
```

No new third-party crates: `axum`, `futures`, `tokio`, `base64`, `sha2`, `hex`
and `a2a-lf` (A2A 1.0 types, already pinned by `bcs-a2a-http`) are in the
workspace. The adapter adds `a2a-lf` as its own dependency at the same pin.

## Risks and mitigations

- **Risk:** Q1/Q2 answers differ from the proposal.
  **Mitigation:** both are isolated (one codec file, one adapter file) and
  covered by shape tests that pin the stored form.
- **Risk:** one blocking read per session exhausts provider connections.
  **Mitigation:** readers stop with their last subscriber; the count is logged
  as a gauge; the limit itself is TBase question 8, not designed around.
- **Risk:** a snapshot that never covers the event (Worker died between `ev`
  and `task`) holds a blocking send.
  **Mitigation:** the cover window is bounded (2 s), then the stored snapshot
  is returned; the next owner's redo publishes it.
- **Risk:** contract edits break the Connector session's branch.
  **Mitigation:** the edits are one small commit, first in the PR, and named in
  the description; `Duplicate` call sites fail to compile rather than misbehave.
- **Risk:** PR size (four new crates).
  **Mitigation:** one commit per layer; if it exceeds a reviewable size, split
  into store / core+application / adapter, one branch each.

## Alternatives considered

- **Gateway providers inside `bcs-dist-store`.** Fewer crates, but both
  sessions would edit one crate; rejected (Q8).
- **Read "latest `ev` id" before admission** (deep dive §3 step 3). Not needed:
  the subscriber starts at the snapshot's coverage, or at the beginning when
  there is none, and filters by task. Saves one read on every send.
- **JSON of the whole cursor as the SSE id.** Longer ids; backend and
  generation are constants for format 1, so they are implied by the version.
- **Random session id + a lookup key from `messageId` to session.** One more
  key and write order to keep correct; the derived id needs neither (Q4).
- **mpsc channel per subscriber.** A full channel would need a policy inside
  the read loop; a broadcast channel drops the slow receiver by construction.
- **Digest helpers from `bcs-dist-store` in the application crate.** An
  application crate would depend on a store crate; the application keeps its
  own small identity module (digests are only compared with themselves).

## Rollout

Nothing is wired into a binary, so merging changes no running behaviour. Order
inside the PR: contract edits → shared store → Gateway store → core →
application → adapter.

## Test strategy

Run with `cargo nextest run -p <crate>`; store-backed tests use
`bcs_test_support::store_model::Model` and a `FakeWorker` helper that appends
`ev` (explicit ids) and then publishes `task`, in that order.

```rust
// bcs-dist-store/tests/admission.rs (extended)
fn new_task_is_listed_in_the_session_index_once() {}
fn retry_after_lost_index_write_repairs_the_index() {}
fn same_digest_retry_is_accepted() {}                 // was Duplicate
// bcs-dist-store/tests/building_blocks.rs
fn ev_entry_round_trips_and_pins_its_fields() {}
fn ev_entry_with_unknown_version_is_skipped() {}

// bcs-dist-gateway-store/tests/task_reader.rs
fn restore_returns_stored_snapshot_with_cursor_at_coverage() {}
fn restore_of_expired_task_reads_the_final_record() {}
fn resume_before_coverage_or_on_finished_task_is_gap() {}
fn resume_in_another_scope_is_invalid_scope() {}
fn list_pages_a_session_in_creation_order_and_filters_status() {}
// bcs-dist-gateway-store/tests/readers.rs
fn late_subscriber_sees_no_gap_and_no_duplicate_while_entries_are_written() {}
fn two_subscribers_share_one_blocking_read() {}       // counted through the recorder
fn slow_subscriber_is_closed_and_others_continue() {}
fn reader_stops_after_last_subscriber_leaves() {}
fn entries_of_other_tasks_are_skipped() {}
// bcs-dist-gateway-store/tests/session_store.rs
fn worker_operations_are_refused() {}

// bcs-dist-gateway/tests/core.rs            (in-memory fakes of the ports)
fn reply_to_closed_interaction_is_invalid_transition() {}
fn cancel_of_finished_task_is_invalid_transition() {}
fn new_task_in_foreign_session_is_scope_mismatch() {}
// + the harness dist_gateway_core_service_contract_tests

// bcs-app-dist-gateway/tests/{identity,admission,subscription,wait,cancel,query}.rs
fn same_message_selects_same_session_task_and_input() {}
fn changed_content_is_conflict() {}
fn store_failure_is_outcome_unknown_and_retry_completes() {}
fn snapshot_then_events_then_end_on_input_required() {}
fn resume_gets_only_later_events_without_snapshot() {}
fn unusable_cursor_falls_back_to_snapshot() {}
fn wait_returns_snapshot_that_covers_the_event() {}
fn wait_returns_current_view_at_timeout() {}
fn reply_wait_ignores_the_question_it_answers() {}
fn cancel_of_finished_task_is_not_cancelable() {}
fn list_without_session_is_invalid() {}
// + the harness dist_gateway_service_contract_tests

// bcs-dist-gateway-http/tests/{jsonrpc,sse,cursor,errors,e2e}.rs
fn cursor_round_trips_and_rejects_other_versions() {}
fn first_sse_frame_is_the_task_and_ids_are_cursors() {}
fn last_event_id_resumes_without_snapshot() {}
fn every_gateway_error_maps_to_its_code() {}
fn push_config_methods_return_32003() {}
fn subscribe_to_finished_task_returns_32004() {}
fn retried_streaming_send_on_finished_task_sends_the_task_and_closes() {}
fn agent_card_declares_no_push_notifications() {}
fn e2e_send_stream_reply_cancel_over_the_store_model() {}
fn admission_always_writes_wake_while_d1_is_stubbed() {}
```

Checks before every push: `cargo check --all-targets` and `cargo nextest run`
for the touched crates, `scripts/ci/arch-check.sh`, the 1,000-line limit, no
`cargo fmt`. Not run here: anything against a real TBase (no provider in
Avernet).

## As built

Differences from the plan above, and what is left open.

- **One event per follow item.** `TaskReaderPort::follow` yields one event per
  item, so every event has its own cursor (the SSE `id:`), instead of a batch
  with one cursor.
- **Stricter resume and follow checks** (from the code review of this change).
  A position is a gap when the task is finished, when it is before the
  snapshot's coverage, or when it is beyond the newest `ev` entry. The last
  rule stops a made-up `Last-Event-ID` from starting the session's shared
  reader at a position where it would deliver nothing to anyone. `follow`
  repeats the check when it starts, because the task may have advanced or
  finished since it was restored.
- **No endless gap loops.** A subscription that gets a gap right after a fresh
  snapshot ends with a retryable error (`task_position_unavailable`); a wait
  pauses between attempts and stops at its deadline.
- **A wait survives a failed event read.** When the event stream cannot be
  read, `wait_task` returns the current view, like at its timeout: the input
  is already admitted.
- **Conformance entries.** `tests/conformance_*.rs` run the shared harnesses
  against `StoreTaskReader`, `GatewaySessionStore` (spec read; the commit
  harness is waived in `session.rs`) and `DistGatewayCore`.
- **Shared test fixtures.** The other crates include
  `bcs-dist-gateway-store/tests/common/mod.rs` by path (store model, fake
  Worker, in-memory result and blob stores) instead of a published test crate.
- **Tests ran with `cargo test`**; `cargo nextest` is not installed in the
  build environment.

Changed after the PR reviews (2026-11-17):

- **A new shared reader starts at the stream's newest entry**, never at a
  subscriber's position; everything before it is that subscriber's own paged
  catch-up read. One subscriber's cursor can no longer place the reader for
  the others, and history is not re-broadcast to subscribers that are up to date.
- **A blocking read has a client-side time limit** (`read_block` +
  `read_grace`); a read that never returns counts as a failed read.
- **Task ids keep 64 bits of the digest** (16 hex), like session ids.
- **A stream ends after a gap only when the gap follows a snapshot that was
  itself sent because of a gap**; an ordinary advance or finish right after
  the first snapshot gets a fresh snapshot.
- **A stream ends after any snapshot that shows the task waiting for input**,
  at its start or when a snapshot is re-sent after the task moved on (the
  question is then in the snapshot, not in an event). A stream started by a
  reply stays open only while a snapshot still shows the question it answered
  (same coverage as before the reply); a newer question ends it. Adapter rule,
  like the finished-task rule.
- **A group directory answer for another group id is refused** before
  anything is written: the session would be frozen for an id its own URL does
  not carry and could never be reached.
- **The shared harnesses test the group rule**: `get_task` through another
  group is NotFound, and `session_group` returns the created group and
  NotFound for another scope.
- **Admission:** a repairing index insert counts as a write of the call, and
  a repaired entry is scored with the stored task's creation time.
- **A malformed page token is invalid input** (-32602), not a retryable error.
- **A session or task is reached only through the group it was created for**
  (decided 2026-11-18). The URL is `/a2a/{group}`; the group creates the spec
  on a session's first message, and every later request that names a session
  or task (new task, reply, get, subscribe, wait, cancel, list) is refused
  with "not found" when its group is not the one frozen in the spec. Contract:
  `get_task` takes the group, `SubscribeTask`, `WaitTask`, `CancelTask` and
  `ListTasks` carry it, and `DistGatewayCoreService::session_group` reads the
  frozen group. Cost: one spec read per such request. Access to the group is
  still checked with the directory only when a session is created.
- **Another caller's session is "not found"**, where it used to be "access
  denied" for a new task or a listing: the session check now comes first and
  does not reveal that the session exists.

Delayed, tracked as Active Tasks:

- a retried reply or cancel refused after it took effect (open item 1);
- detecting an `ev` trim while a subscriber is being fed (the check and the
  reads are separate steps; only a reader behind the new snapshot is affected);
- a warning log when a wait returns early because the event stream could not
  be read.

Open, each needing a decision:

1. **A retried reply or cancel can be refused after it took effect.** The
   state checks (is the question still open, is the task still running) run
   before the admission record is consulted. If the first attempt was admitted
   and the Worker has already acted on it, the retry gets
   `interaction_not_open` / `invalid_task_state` (reply) or "not cancelable"
   (cancel) instead of the original success. Fixing it needs an "already
   admitted?" read on `InboxProducerPort`, which is a contract change. New
   messages are not affected.
2. **The SSE id format assumes the backend name `tbase`.** With another
   `GatewayStoreConfig.backend` no frame carries an id and resume never works.
3. **A slow client can still be dropped while live.** History is no longer
   pushed through the shared reader (see below), so only a client that reads
   live events too slowly is closed and has to reconnect.
4. **Q3** (the core inlines large content through `BlobStorePort`) is
   implemented as proposed; it was not covered by the plan review.
