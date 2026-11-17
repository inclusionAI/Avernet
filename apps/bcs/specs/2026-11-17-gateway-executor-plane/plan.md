# Plan: Gateway of the distributed executor plane (MVP)

Status: draft for review. Implements [spec](spec.md); storage mapping and the
findings behind several choices are in [research](research.md). Base: `dev`
`7d644824`. "Decision n" refers to the project's design decision log.

## 1. Shape

```text
PA ── JSON-RPC + SSE ─▶ adapters/http/bcs-dist-gateway-http      (G6)
                          │ only bcs_service_api::application
                          ▼
                        application/bcs-app-dist-gateway         (G1–G5)
                          │ DistGatewayCoreService, DistGroupDirectoryPort
                          ▼
                        services/bcs-dist-kernel  (gateway module)
                          │ InboxProducerPort, TaskReaderPort, SessionStorePort::get_spec,
                          │ ResultStorePort::get_final, BlobStorePort::get
                          ▼
                        services/bcs-dist-store   (the only crate that names bcs_store_api)
                          │ KvStorePlugin, JsonStorePlugin, ZSetStorePlugin, StreamStorePlugin
                          ▼
                        configured store plugin (out of repo) / conformance model in tests
```

Crate names follow the merged task list. No new workspace dependency versions;
`sha2`, `hex`, `base64`, `axum`, `futures`, `tokio` are already workspace deps.

## 2. Pull requests, in order

| # | Title | Content | Size |
| --- | --- | --- | --- |
| 1 | `feat(bcs): add shared dist store layout and admission provider` | this spec folder; `bcs-dist-store` with `layout`, `version`, `error`, `capability`, only the codecs admission needs (`admit`, `inbox`, `spec`, initial `task`, inbox position token), and `inbox` (`InboxProducerPort`: `admit` + `append_stream`, D1 stub); the exported store test model (Q7) | small; unblocks the Connector |
| 2 | `refactor(bcs): align dist gateway contracts with settled decisions` | the contract edits of §3 only | tiny; for the executor team to review |
| 3 | `feat(bcs): add dist task reader with shared session readers` | the `ev`, stored-`task` and cursor codecs; `task_feed` (`TaskReaderPort`); `session` (`get_spec`); the task index (Q1) | medium |
| 4 | `feat(bcs): add dist gateway core and application services` | `bcs-dist-kernel::gateway`, `bcs-app-dist-gateway` | medium |
| 5 | `feat(bcs): add inbound A2A JSON-RPC and SSE gateway adapter` | `bcs-dist-gateway-http` | medium |

PR 1 changes no merged contract: until PR 2 lands, the provider returns
`AdmitOutcome::Accepted` for both a new and an already-admitted input and never
constructs `Duplicate`. PR 1 holds nothing that can wait for PR 3.

Workspace `Cargo.toml` gets one `members` line and one `[workspace.dependencies]`
line per new crate, appended next to the existing dist entries.

## 3. Changes to merged contracts (need explicit approval)

Nothing in this section is applied before the owner approves it; a review
agreement on this PR is not that approval. All of it is PR 2.

**C1. `GatewayError::NotCancelable`** (decision 28b), in
`application/dist_gateway.rs`:

```rust
    #[error("task is not cancelable")]
    NotCancelable,
```

**C2. One admission success** (decision 24). Remove the `Duplicate` variant:

```rust
// port/repo/distributed/inbox.rs
pub enum AdmitOutcome { Accepted(AdmissionReceipt), ContentConflict, Quarantined }
// core/dist_gateway.rs
pub enum CoreAdmission { Accepted(AdmissionReceipt) }
```

Follow-on edits: `InboxProducerPort::admit` doc ("…before returning Accepted"),
two sentences in `port/repo/distributed/README.md`, the harness assertions in
`bcs-test-support/src/contract/distributed/{inbox,state,services}.rs`, and the
scripted replies in `tests/distributed_harness/{inbox,session_spec,services}.rs`.
Alternative without a contract change: keep the variant, never return it, and
relax the harness to accept either. I recommend removing it; a variant no
provider can return correctly invites callers to branch on it.

Effect on the Connector (plan in #4111, §5.2): it currently treats both
`Accepted` and `Duplicate` as success; after C2 it matches only `Accepted`. The
PR 2 description states this for the Connector session and the executor team.

**C3. One waiting use case** (decisions 28a and 30), in `DistGatewayService`:

```rust
    /// Wait until the task needs input or is final and the stored snapshot
    /// covers that event; at `timeout` return the current view. Not an error.
    async fn await_settled(
        &self,
        caller: &GatewayCaller,
        tid: &Tid,
        timeout: std::time::Duration,
    ) -> Result<TaskView, GatewayError>;
```

Blocking send is `submit` then `await_settled(60 s)`; cancel is `cancel` then
`await_settled(4 s)`. Follow-on edit: the scripted `DistGatewayService` in
`tests/distributed_harness/ports.rs`. Alternative without a contract change:
the adapter composes `subscribe` and `get_task` itself, which puts the bounded
"read until the snapshot covers the entry" retry into a delivery adapter. I
recommend C3.

Not changed, noted only: the `TaskReaderPort::restore` doc says "fold snapshot S
and events (S,H]"; with decision 30 the provider returns the stored snapshot
with the cursor at S (H = S). `AdmitOutcome::Quarantined` stays unused until D1.

## 4. `bcs-dist-store` (shared pieces first)

```text
crates/services/bcs-dist-store/
  src/lib.rs            module list and re-exports only
  src/layout.rs         KeyLayout { cluster, namespace }: every key of the catalogue; id parsing
  src/version.rs        CREATE, commit, seal, claim, next
  src/error.rs          bcs_store_api::StoreError → port StoreError; need_contract()
  src/capability.rs     require(&dyn StorePlugin, &[StoreCapability])
  src/codec/{admit,inbox,spec,task,position}.rs                      (PR 1)
  src/codec/{ev,cursor}.rs, stored-task decoding                     (PR 3)
  src/inbox/{mod,producer,listener}.rs
  src/task_feed/{mod,reader,session_readers,subscriber,list}.rs     (PR 3)
  src/session/spec_reader.rs                                         (PR 3)
  tests/{layout,version,codec,admission_order,admission_faults,...}.rs
```

`bcs-dist-store` is the crate that exports the shared helpers: `layout`,
`version`, `codec`, `error` and `capability` are public modules. The Connector
plan (#4111) puts its providers in a separate `bcs-dist-connector-store` that
depends on this crate, so the two sessions share no source file. Identifiers
stay vendor-neutral (`Store…`).

Not in this plan: a `MembershipPort` provider (heartbeat copies A and B, and
the liveness check in the Connector's claim). The Gateway does not use
membership; who builds it is the owner's decision. `layout` already carries the
`hb` and `member(s)` key builders it will need.

- **`layout`**: `bcn:v1:<cluster>:<namespace>:<shard>:<kind>:<id>` for `owner`,
  `spec`, `ckpt`, `inbox`, `admit`, `active`, `wake`, `task`, `ev`, `disp`,
  `sched`; plus the global `cmd`, `held`, `hb`, `member(s)` names. Shard text is
  `s%04d` and is parsed from the session id; ids with `{ } :` or whitespace are
  rejected as `StoreError::Invalid`. `cluster` and `namespace` are constructor
  arguments; nothing is read from the environment.
- **`version`**: as in research §2; all return `Result<Version, StoreError>`.
- **`error`**: `OutcomeUnknown` maps to `OutcomeUnknown` and nothing else does;
  `InvalidInput`, `WrongType`, `LimitExceeded` map to `Invalid`; the rest to
  `NotApplied` with a machine code. No provider message text is forwarded.
- **`inbox::producer::StoreInboxProducer`** holds `Arc<dyn KvStorePlugin>`,
  `Arc<dyn JsonStorePlugin>`, `Arc<dyn ZSetStorePlugin>`,
  `Arc<dyn StreamStorePlugin>`, a `KeyLayout` and a clock. `new` checks `Kv`,
  `AtomicJson`, `ZSet`, `Stream` and fails otherwise. `admit`:

  1. validate ids against each other and the shard → `Invalid`;
  2. `kv.get(admit)`: found → same digest `Accepted` with the stored `tid`,
     different `ContentConflict`; a read error here is `NotApplied` (nothing
     written);
  3. new session: create `spec` with `Newer(1)`; if it exists, read it and
     require the same scope and group, else `ContentConflict`;
  4. new task: create `task` with `Newer(1)`; "exists" is fine;
  5. `stream.append(inbox, Auto, …)`, never retried here;
  6. `listener::read_listener(…)`: the D1 stub. Its doc comment names D1; its
     error is logged at debug level and treated as "no valid listener";
  7. `zset.add(active, sid @ now_ms, Lower)`;
  8. no valid listener: `stream.append(wake, Auto, {sid})`, plus the sampled
     trim of research §4.2;
  9. `kv.put_if_absent(admit, …)`: `false` → `kv.get`, compare digest.

  Any error from step 3 on returns `StoreError::OutcomeUnknown { operation_id:
  input_id }`. `append_stream` runs steps 5–8 for an intermediate agent event
  and returns the `InboxPos`.
- **Round trips**: new session and task 8; new task 7; reply or cancel 6;
  already admitted 1; intermediate agent event 3 until D1 (then 1–2). No loop,
  no scan; the sampled wake trim adds one call per ~128 wakes.

## 5. Reading output (`task_feed`, PR 3)

- **`restore`**: one `get_json(task)`. Missing, or owned by another scope →
  `None`. Otherwise `Live { view, cursor }` with `covered_through` and the
  cursor at `snapshot_event_id`; a task with no coverage yet gets the
  start-of-stream token. A terminal stored status returns `Final(view)`.
- **Cursor token**: `ReplayToken { backend, generation, value }`. `backend` is
  one constant for the deployment, with no cluster id in the MVP (value: Q12).
  `generation` is the constant `"1"`: `ev` ids come from `commit_seq`, which
  never restarts, so it changes only if a future rebuild procedure (restore,
  migration, per-task streams) recreates a stream. That is written as a comment
  next to the constant. It is stored nowhere yet. `value` is `<major>-<minor>`.
- **`validate_resume`**: token from another backend or log generation → `Gap`;
  one `get_paths(task, [scope, sid, snapshot_event_id])`; other scope →
  `InvalidScope`; missing task or position before `snapshot_event_id` → `Gap`;
  else `Valid`.
- **`follow`**: one scope read when the stream starts, none per event. Backed by
  **`SessionReaders`**:
  - one tokio task per session with local subscribers, looping on
    `stream.read(&[ev key], { max_entries: 100, block: 1 s })`; exactly one key
    per call (decision 23). It starts at the first subscriber's position and
    exits within one block period after the last subscriber leaves.
  - join under one lock: the subscriber's channel is registered and the
    reader's position `R0` captured atomically with fan-out. A subscriber
    behind `R0` reads `(pos, R0]` itself with paged `range`, then drains the
    channel, dropping ids at or below what it already delivered. The shared
    reader never moves backwards.
  - each subscriber has a bounded channel (256 events). Fan-out uses
    `try_send`; a full channel closes that subscriber with a retryable error
    and never blocks the loop.
  - entries of other tasks are skipped; unknown kinds advance the position and
    are logged; `next` is the cursor of the last delivered entry.
  - gap evidence: `page.recovery` when the provider returns it; a detected gap
    yields `FollowItem::Gap`. Nothing else is built for the trim race until the
    executor side answers Q3.
  - a store error is sent to every subscriber of that session and the reader
    exits; clients reconnect and resume.
- **`list`**: over the task index `…:tasks:<sid>` (Q1): `zset.range` for one
  page, then one `task` read per listed id (bounded by the page size, capped at
  50, issued concurrently), and `get_final` for ids whose `task` key expired.
  A request without a session filter is `Invalid { code: "session_required" }`.
  The index has no expiry in the MVP.
- **`session::spec_reader`**: research §4.6.
- **Cost**: per subscriber one or two `task` reads plus the catch-up pages;
  per served session one blocking read, re-issued every second when idle.

## 6. Core service (`bcs-dist-kernel::gateway`, PR 4)

Implements `DistGatewayCoreService` over `Arc<dyn …Port>`; no store or plugin
types. `check-store-boundaries.sh` gets `crates/services/bcs-dist-kernel/src`
added to its target list so R10 covers the new service crate. The Connector
plan adds `bcs-dist-connector` to the same list: expect a one-line rebase
conflict there.

- `admit`: envelope, spec and task must agree with `scope`; an existing session
  is checked through `get_spec` (scope and group) → `ScopeMismatch` /
  `NotFound`; a reply or cancel reads the task once: reply requires
  input-required and an open interaction, cancel requires a non-final task →
  `InvalidTransition`. Then `InboxProducerPort::admit`; `OutcomeUnknown` and
  `Unavailable` are kept apart.
- `restore_task`: reader `restore`; `None` → `ResultStorePort::get_final` →
  view from the final record; still `None` → `NotFound`. A snapshot stored by
  reference is loaded through `BlobStorePort::get`.
- `validate_resume`, `follow_task`, `list_tasks`: thin mappings.

## 7. Application service (`bcs-app-dist-gateway`, PR 4)

- `identity.rs`: digests are SHA-256 over length-prefixed fields, hex encoded.
  - session id (new session) = `s<shard:04>.<20 hex>` from digest(tenant,
    principal, group, messageId); shard = that digest modulo the configured
    shard count (see Q6);
  - task id = `<sid>.<12 hex>` from digest(messageId);
  - `input_id`: user input digest(tenant, caller, target, session, messageId);
    reply and cancel digest(caller, sid, kind, request id);
  - `business_digest` = digest of the canonical JSON (sorted keys) of the body.
- `routing.rs`: caller → `TaskScope` (a `Public` caller is `AccessDenied`); no
  session → resolve the group through `DistGroupDirectoryPort` and freeze the
  spec; session without task → new task; with task → human reply (the
  interaction given, or the task's single open one).
- `admission.rs`, `query.rs`, `cancel.rs`: map core results to `GatewayError`
  (`InvalidTransition` on cancel → `NotCancelable`). Inline content above the
  session's `max_inline_bytes` is `Invalid { code: "payload_too_large" }` in the
  MVP.
- `subscription.rs`: Snapshot → `Snapshot { view, cursor }`, stop if final,
  else follow. Resume → `validate_resume`: `Valid` → `ResumeAccepted` then
  follow; `Gap` → the Snapshot path; `InvalidScope` → `NotFound`. A `Gap` while
  following restores again (at most 3 times per stream). The stream ends after
  an input-required, auth-required or terminal event.
- `await_settled` (C3): restore; if not settled, follow until the settling
  event `E`; then restore until `covered_through ≥ E` (5 tries, 50 ms apart);
  at the time limit return the latest view.
- Limits are a plain config struct with defaults: blocking wait 60 s, cancel
  wait 4 s, subscriber buffer 256, read block 1 s.

## 8. HTTP adapter (`bcs-dist-gateway-http`, PR 5)

- Depends on `bcs-service-api` (application only), `axum`, `serde`, `base64`.
  Exposes `router(state)`; no binary, no bootstrap registration.
- Methods, A2A 1.0 names as `bcs-a2a-http` speaks them (`SendMessage`,
  `SendStreamingMessage`, `GetTask`, `SubscribeToTask`, `CancelTask`,
  `ListTasks`); no 0.3 aliases unless Q4 says a PA needs them, and then in one
  mapping table; any push-notification method, or a webhook in a send, → -32003. Agent
  card with `capabilities.streaming: true`, `pushNotifications: false`.
- The target group comes from the route (`/groups/{group}/a2a`); see Q9.
- SSE: `id:` = `1.` + base64url(JSON `[sid, tid, backend, generation, value]`),
  with the task id shortened to its suffix when it starts with the session id.
  `Last-Event-ID` that fails to decode means "no resume id". Keep-alive comment
  every 15 s.
- Mapping lives in one file: `TaskView` → A2A `Task`; `Status` → status update
  (`final` for input-required and auth-required); `Artifact` → artifact update;
  `Terminal` → final status update. The display-state and artifact payload
  shapes are not agreed yet, so inline JSON that already has A2A field names is
  passed through and anything else is wrapped as a data part.
- Errors: `NotFound` -32001; `NotCancelable` -32002; `Conflict`, `Invalid`
  -32602; `AccessDenied` an auth-range error (open issue on auth placement);
  `Quarantined`, `Unavailable`, `OutcomeUnknown` -32603 with a retry hint.
- The caller identity is produced by a resolver the composition root injects;
  the adapter never reads it from the body. Request bodies above 256 KiB are
  rejected.

## 9. Open questions for review

"Owner" marks a decision that is the owner's; "executor side" marks a question
the owner takes to the Worker team. Review agreement so far is noted, not final.

| # | Question | Recommendation | Who decides |
| --- | --- | --- | --- |
| Q1 | `list` has no index to enumerate (research §4.5) | add `…:tasks:<sid>`, written only by the Gateway in admission right after the task creation; no Worker change; no expiry in the MVP; session filter required. It is a new key in the catalogue, so a decision-log row is proposed before PR 3 | owner (review: agreed) |
| Q2 | Stored shapes of `spec`, `task`, `inbox`, `ev` (research §4.7) | serde of the merged types, codecs in `bcs-dist-store` | executor side |
| Q3 | A trim can race a live reader (research §4.3) | preferred: a Worker trim rule, "trim only entries of tasks that have ended, after their final snapshot". Fallback: a `prev` field on `ev` entries. Nothing is designed around either yet | executor side |
| Q4 | A2A version | 1.0 as `bcs-a2a-http` speaks it. No 0.3 aliases unless a known PA still speaks 0.3 | owner |
| Q5 | `wake` trimming (research §4.2) | sampled trim by the writer | owner |
| Q6 | Session id and shard for a new session | deterministic from (tenant, principal, group, messageId); shard by hash over a configured shard count, replaceable once shard placement is decided | owner |
| Q7 | In-memory store for tests, needed by this plan and by the Connector plan (#4111 §13 item 8) | export the conformance model from `bcs-test-support` as a module named as a test model, not a provider, in PR 1, after checking with the PR #4070 author. Not `#[path]` includes from two crates | owner (review: agreed) |
| Q8 | Five PRs but one session branch | sequential PRs on the session branch, restarted from `dev` after each merge; or one branch per PR | owner |
| Q9 | How a PA names the target group | one A2A endpoint and agent card per group, group id in the route | owner |
| Q10 | Contract edits C1–C3 (§3) | approve all three | owner (review: agreed) |
| Q11 | A retried, unfinished admission appends a second `inbox` entry with the same `input_id`. The Worker rule "`admit` exists with the same digest → process it" lets both copies through, so the input would be processed twice | the Worker drops an `input_id` it has already consumed (for example by tracking consumed ids until the `admit` expiry). Needs confirmation before PR 1 relies on it | executor side |
| Q12 | Value of the cursor's `backend` constant | a constructor argument with one constant per deployment. Review suggested the store product's name; this repository's rule is to keep vendor names out of code, so a neutral default (`"store"`) is proposed | owner |
| Q13 | Who builds the `MembershipPort` provider the Connector needs | not the Gateway's scope; `layout` has the key builders | owner |

## 10. Tests

- **PR 1**: key names against the documented examples; version helper bounds;
  round trips of the admission codecs; recorded call order of admission; same input twice; same id
  with different content; fault injected after each step, then a retry; D1 stub
  returns `need_contract_not_implemented` and admission still writes one wake;
  missing capability fails construction; `append_stream` path.
- **PR 2**: `cargo check`/`cargo test` for `bcs-service-api`, `bcs-dist-model`,
  `bcs-test-support --test distributed_harness`.
- **PR 3**: `ev` and cursor codec round trips; restore (live, null coverage,
  final, other scope); list over the index, including an expired `task` key; resume
  validation; follow with a writer task appending concurrently (no gap, no
  repeat); late join behind the shared reader; two subscribers share one read
  (read-call counter); reader stops after the last subscriber; slow subscriber
  closed alone; gap evidence; `task_reader_port_contract_tests`.
- **PR 4**: core and application with hand-written port fakes: routing, every
  error row, subscribe modes, gap rebuild, `await_settled` covered and timed
  out, cancel on a finished task; `dist_gateway_service_contract_tests`.
- **PR 5**: JSON-RPC method table, error codes, agent card, SSE id round trip,
  `Last-Event-ID` resume, keep-alive, push-notification rejection, and one flow
  through adapter → application → core → store → conformance model with a
  scripted writer standing in for the Worker.

Commands, from `apps/bcs`: `cargo nextest run -p <crate>` (or `cargo test -p`),
`cargo clippy -p <crate> --all-targets -- -D warnings`,
`bash scripts/ci/check-store-boundaries.sh`, `check-deps.sh`,
`check-import-rules.sh`. No `cargo fmt`.

## 11. Risks

- The D1 stub triples writes for intermediate agent events until D1 is settled.
- Until Q3 is answered, a trim that races a live read is detected only when the
  provider returns `StreamRecovery` evidence (research §4.3).
- Until Q11 is answered, a retried admission can make the Worker process one
  input twice (research §6).
- The display-state format is unsettled; the A2A mapping is isolated in one
  file so it can change without touching services.
- The conformance model is a model: durability, failover and blocking-read
  limits are only proven against the real provider.
