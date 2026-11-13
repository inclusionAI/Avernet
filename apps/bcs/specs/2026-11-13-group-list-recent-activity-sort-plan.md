# V1 Group/Session lists: sort by conversation last activity — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let V1 `GET /openapi/v1/collaboration/groups` **and** `GET /openapi/v1/collaboration/groups/{group_id}/sessions` order their full filtered result by the conversation's real last-activity time (`sort=last_activity`) and carry `last_activity_at` on group and session summaries, with byte-compatible defaults and field values identical across sort modes.

**Architecture:** Read-time aggregates over the persisted log and session table (design D1/D2): `MAX(bcs_messages.created_at)` per group (Q1) and per session (Q3), plus per-group `MAX(bcs_group_sessions.gmt_create)` (Q2), exposed as defaulted trait methods through `MessageRepoPort` / `SessionRepoPort` → application `GroupMessageHistoryService` (implemented by `bcs-message::MessageService` only) and `SessionManagementService` (implemented by `bcs-session`) → the V1 facades. Both facades use the D4 two-path flow: `last_activity` looks up the full candidate set before the global sort; the default/`created_at` path paginates first and looks up only the page's ids. No schema migration and no new index on the default path; the D2 evidence gate may authorize the additive covering index, never a denormalized column or write-path change. Legacy endpoints and `list_public_groups` untouched.

**Tech Stack:** Rust, Tokio, async-trait defaulted trait methods, axum Query DTOs, sqlx-style stores behind `bcs-db-api` (one SQL impl serving MySQL and SQLite via `DbSqlFlavor`), in-memory stores for tests.

**Spec:** [2026-11-13-group-list-recent-activity-sort-design.md](2026-11-13-group-list-recent-activity-sort-design.md) (decisions D1–D8; the plan argues from it).

## Global Constraints

- Work item: 2026080400118003354. Branch/lifecycle (commit, push, PR) is owned by the dispatching pipeline task, not by this plan — no `git` mutation steps appear below.
- **Do not start Task 1 before the requester answers D8 CF-1…CF-4 in the doc-PR thread.** The tests below encode the proposed defaults (global clock, session creation counts as activity, all persisted sessions related, id-ASC tie-break); a veto on CF-1 re-opens the design rather than amending this plan.
- No schema migration and no new index **on the default path**; the additive covering index(es) `(env, group_id, created_at)` / `(env, session_id, created_at)` may be added only through the D2 evidence gate (Task 8), as a new uniquely numbered migration per dialect following the frozen-migration rules. No denormalized column, no change to `bcs-group*`/session/message write paths in this work item.
- Layered call direction: adapters → `service-api::application` → `core` → `port`; V1 facades consume application services (the `GroupMessageHistoryService` precedent; `SessionServiceImpl` already holds it and keeps its existing `SessionRepoPort` usage for list/count).
- No `cargo fmt` / global formatting; only touched lines change style.
- Compiles clean with default trait methods: no existing noop/test implementation of `MessageRepoPort`, `SessionRepoPort`, `GroupMessageHistoryService`, or `SessionManagementService` may require edits.
- Cargo discipline (workspace rule): build via the worktree `.cargo/config.toml` target-dir (`/tmp/cargo/Avernet-target`); `df -h /tmp` before heavy runs; focused `cargo test -p <pkg> --test <name>` only, no workspace-wide regression until final verification.
- Default ordering of every existing endpoint stays exactly as today; no per-item activity queries on any path (anti-goal).

## Review Focus

Inputs/conditions the spec does not spell out, and the expected behavior (each pinned by a test in the owning task):

1. More candidates than one SQL bind chunk holds → chunk, results stay complete (Tasks 2, 3).
2. No-row fallbacks at both levels: pre-cutoff/never-messaged group or session reports its own `created_at`; a group with zero sessions and zero messages likewise; never an error (Tasks 2, 3, 5, 6).
3. `sort=LAST_ACTIVITY`, `sort=last_activity ` (case/whitespace variants) → 400 `invalid_request` on both endpoints, not silent default (Task 7).
4. Activity-store failure on **either** population path (full-set under `last_activity`, page-scoped under `created_at`) → 500; no silently mis-ordered or mis-dated 200 (Tasks 5, 6).
5. DM and catalog behavior: DM summaries populate the field and sort; `public-groups` items omit the field and keep `updated_at DESC` exactly (Task 5).
6. Cross-sort consistency: the same item returns the *same* `last_activity_at` under no-sort, `created_at`, and `last_activity`; tests use message times far later than creation so an accidental `created_at` fallback fails (Tasks 5, 6).
7. Unified two-level semantics: a brand-new empty session appears at the top of session `sort=last_activity` (created_at fallback) **and** lifts its group; after session deletion its `created_at` drops out of the group key while its messages keep counting (Tasks 3, 5; D8 CF-2/CF-3 defaults).
8. Scan-cost honesty: store tests assert correctness under skewed volumes (one candidate with deep history among shallow ones); Task 8 attaches `EXPLAIN` evidence and seeded-volume timings per D2 — no cost claim ships without it.

## Tasks

### Task 1: Service-API contract surface

**Files:**
- Modify: `crates/service-api/bcs-service-api/src/port/repo/message.rs` (trait `MessageRepoPort`)
- Modify: `crates/service-api/bcs-service-api/src/port/repo/session.rs` (trait `SessionRepoPort` `:139`)
- Modify: `crates/service-api/bcs-service-api/src/application/group_message.rs` (trait `GroupMessageHistoryService`, `:63`)
- Modify: `crates/service-api/bcs-service-api/src/application/session.rs` (trait `SessionManagementService`, `:56`)
- Modify: `crates/service-api/bcs-service-api/src/application/v1/group.rs` (`ListGroups` `:312`, `NormalGroupSummary` `:143`, `DirectMessageGroupSummary` `:170`)
- Modify: `crates/service-api/bcs-service-api/src/application/v1/session.rs` (`ListSessions` `:155`, `SessionSummary` `:40`)

**Interfaces:**
- Produces:
  - `async fn last_activity_by_group_ids(&self, group_ids: &[String]) -> Result<std::collections::HashMap<String, u64>, MessageRepoError>` and `async fn last_activity_by_session_ids(&self, session_ids: &[String]) -> Result<std::collections::HashMap<String, u64>, MessageRepoError>` on `MessageRepoPort`, default bodies `Ok(Default::default())`; doc: store-owned env isolation, ms epoch, ids with no rows are absent from the map.
  - `async fn max_created_at_by_group_ids(&self, group_ids: &[String]) -> ServiceResult<std::collections::HashMap<String, u64>>` on `SessionRepoPort`, default body `Ok(Default::default())`; doc: max over currently-persisted sessions only (hard-deleted sessions contribute nothing), epoch ms from `gmt_create`.
  - The two message methods on `GroupMessageHistoryService` (error type `GroupUseCaseError`), default bodies returning empty maps.
  - `async fn max_created_at_by_group_ids(&self, group_ids: &[String]) -> Result<std::collections::HashMap<String, u64>, SessionUseCaseError>` on `SessionManagementService`, default body an empty map.
  - `pub enum GroupListSort { CreatedAt, LastActivity }` with `impl Default` = `CreatedAt`, in `application/v1/group.rs` next to `ListGroups`; same-shaped `pub enum SessionListSort` in `application/v1/session.rs` next to `ListSessions`.
  - `ListGroups` gains `pub sort: GroupListSort`; `ListSessions` gains `pub sort: SessionListSort`.
  - `NormalGroupSummary`, `DirectMessageGroupSummary` and `SessionSummary` gain `#[serde(default, skip_serializing_if = "Option::is_none")] pub last_activity_at: Option<u64>` (epoch ms, D1/D4 semantics documented on the field).

- [ ] **Step 1: Write failing serde tests** in or beside the two `v1` modules: each summary type without `last_activity_at` deserializes to `None` (backward compat); `Some(123u64)` round-trips; `None` is omitted from serialized JSON.
- [ ] **Step 2: Run focused suite RED:** `cargo test -p bcs-service-api --lib` — new tests fail (field/type missing).
- [ ] **Step 3: Implement** the trait methods, enums, `sort` fields, and summary fields exactly as above.
- [ ] **Step 4: Run GREEN:** `cargo test -p bcs-service-api --lib`; then `cargo check -p bcs-test-support -p bcs-message -p bcs-message-flow -p bcs-message-store -p bcs-session -p bcs-session-store -p bcs-app-group -p bcs-app-session` to prove every existing implementation compiles unedited against the defaulted methods.
- [ ] **Step 5:** Fix any struct-literal construction sites of the three summaries the compiler flags inside `service-api` only (tests elsewhere are handled by their owning tasks).

### Task 2: `bcs-message-store` implementations (memory + SQL)

**Files:**
- Modify: `crates/services/bcs-message-store/src/memory.rs`
- Modify: `crates/services/bcs-message-store/src/mysql.rs`
- Test: `crates/services/bcs-message-store/src/memory_tests.rs`, `crates/services/bcs-message-store/src/mysql_tests.rs` (SQLite flavor)

**Interfaces:**
- Consumes: Task 1's `MessageRepoPort::last_activity_by_group_ids` / `last_activity_by_session_ids`.
- Produces: both stores override both methods. SQL: Q1/Q3 per D2, chunk size following the store's existing IN-list batching precedent (see `get_messages_by_ids`), one round trip per chunk; aggregates merge across chunks. Memory: single env-filtered pass over stored messages per shape.

- [ ] **Step 1: Write failing tests** (same logical cases for both stores, both methods, following each file's existing fixtures):
  - empty id list → empty map and no query work;
  - two groups / two sessions with several messages each → per-id `MAX(created_at)`, correct ms values;
  - unknown/no-row ids → absent from map (Review Focus #2);
  - ids beyond one chunk (`> chunk_size` distinct ids) → all present (Review Focus #1);
  - one candidate with deep history (thousands of messages) next to shallow ones → correct maxima (Review Focus #8; functional assertion only — cost evidence is Task 8);
  - messages of another env never counted (env isolation case already conventional in `mysql.rs`).
- [ ] **Step 2: Run RED:** `cargo test -p bcs-message-store --lib last_activity` (or the file-specific test filter matching house style) — fails, methods unimplemented.
- [ ] **Step 3: Implement** both overrides (both methods).
- [ ] **Step 4: Run GREEN:** same focused filter; then `cargo test -p bcs-message-store --lib` to catch regressions in neighbors.

### Task 3: `bcs-session-store` + `bcs-session` (`max_created_at_by_group_ids`)

**Files:**
- Modify: `crates/services/bcs-session-store/src/memory.rs`, `crates/services/bcs-session-store/src/mysql.rs` (or its `mysql_operations/` split, per current layout)
- Modify: `crates/services/bcs-session/src/application/sessionmanagementserviceimpl_2.rs` (`impl SessionManagementService for SessionManagementServiceImpl` `:6` — override the new method, delegating to the repo, error mapping in the file's existing style)
- Test: the stores' existing test suites for each flavor; `crates/services/bcs-session` tests for the delegation

**Interfaces:**
- Consumes: Task 1's `SessionRepoPort::max_created_at_by_group_ids` / `SessionManagementService::max_created_at_by_group_ids`.
- Produces: Q2 per D2 (SQL aggregate or store-side fold per the store's conventions), chunked binds; memory: single pass. SQL conversion of `gmt_create` → epoch ms reuses the store's existing Session projection helper, no new conversion logic.

- [ ] **Step 1: Write failing tests**: empty ids → empty map; two groups with several sessions each → per-group max epoch ms; hard-deleted session stops contributing (insert, delete via the store's own delete, assert the max drops to the remaining session or absence) (Review Focus #7 store half); chunk overflow complete; other env never counted.
- [ ] **Step 2/3/4:** RED (`cargo test -p bcs-session-store --lib` focused filter) → implement → GREEN; then the bcs-session delegation test (stub repo map out, error propagates) via `cargo test -p bcs-session --lib` focused filter.

### Task 4: `bcs-message::MessageService` delegation

**Files:**
- Modify: `crates/services/bcs-message/src/lib.rs` (`MessageService` `:33`, `impl GroupMessageHistoryService for MessageService` `:383`)
- Test: `crates/services/bcs-message/src/tests.rs`

**Interfaces:**
- Consumes: Task 1 trait methods; `MessageService.message_repo` (`Arc<dyn MessageRepoPort>`).
- Produces: both `MessageService::last_activity_by_*_ids` methods delegating to the repo and mapping `MessageRepoError` → `GroupUseCaseError` with the file's existing error-mapping style. `BcsGroupMessageHistory` (bcs-message-flow) is NOT modified — defaulted empty maps stand (D5).

- [ ] **Step 1: Write failing tests** in `src/tests.rs` for each method: stub repo returning a fixed map → same map out; stub repo error → `GroupUseCaseError` propagated (not swallowed into empty).
- [ ] **Step 2/3/4:** RED via `cargo test -p bcs-message --lib last_activity`; implement; GREEN; then `cargo test -p bcs-message --lib`.
- [ ] **Step 5:** `cargo check -p bcs-message-flow` to record that the untouched fallback still compiles.

### Task 5: V1 `bcs-app-group` two-path sorting and projection

**Files:**
- Modify: `crates/application/v1/bcs-app-group/src/lib.rs` (`GroupServiceImpl` `:64` — add `group_message_history: Option<Arc<dyn GroupMessageHistoryService>>` field and `pub fn with_group_message_history(...) -> Self`, matching `with_bot_authority_hook` style)
- Modify: `crates/application/v1/bcs-app-group/src/service.rs` (`list_groups` `:7`, `list_public_groups` `:129`)
- Modify: `crates/application/v1/bcs-app-group/src/projections.rs` (`project_summary` `:182` gains `last_activity_at: Option<u64>` param, assigned onto both summary structs)
- Test: `crates/application/v1/bcs-app-group/tests/v1_group_service.rs` (+ its module dir if the suite splits cases there)

**Interfaces:**
- Consumes: Task 1 `GroupListSort` / summary field, Task 3/4 service methods.
- Produces (D4/D5 flow):
  - `sort == LastActivity`, non-empty filtered set: batched history lookup (message MAX) + batched session-created lookup (Task 3's `SessionManagementService` method via the facade's existing `sessions` handle) over **all filtered ids**; merge per D1 by `max()`; sort key = merged map value, fallback `group.created_at`; order `DESC`, tie `group_id ASC`; then paginate; project with the same merged value.
  - default / `sort == CreatedAt`: unchanged comparator and pagination **first**; the two lookups then run once for the **page's** ids only and populate projections; the field value is identical for a given group under every mode.
  - no wiring / empty sets → empty maps → `created_at` fallback everywhere (D4/D6).
  - lookup error on **either** path → `ApplicationError::internal` (D6/Review Focus #4).
  - `list_public_groups`: no activity calls; projects `last_activity_at: None` (D4).

- [ ] **Step 1: Write failing tests** with stub `GroupMessageHistoryService` and stub `SessionManagementService` (maps wired; one erroring each; construction without `with_group_message_history`):
  - `sort: LastActivity` orders by the merged map with `created_at` fallback, `group_id ASC` tie on equal keys, `total`/pagination slice after the global sort;
  - default/`CreatedAt` sort ordering byte-identical to current behavior (regression guard);
  - **cross-sort consistency (Review Focus #6):** same group under no-sort, `CreatedAt`, `LastActivity` returns the *same* `last_activity_at`; fixture message time far later than `created_at` so an accidental fallback fails;
  - unified semantics (Review Focus #7): a zero-message group whose newest artifact is an empty session ranks by that session's `created_at`; merged map prefers a later session `created_at` over an earlier message max;
  - zero-message, zero-session group → `Some(group.created_at)`;
  - `last_activity_at` is `Some` on normal and DM summaries (Review Focus #5);
  - `membership: SessionOnly` group participates and carries the field;
  - erroring history service → request errors **on both sort modes**; erroring session-created lookup → same;
  - unwired facade → `created_at` ordering preserved and field `Some(created_at)`;
  - `list_public_groups` unchanged ordering, field `None`/absent (Review Focus #5).
- [ ] **Step 2: Run RED** on the new cases: `cargo test -p bcs-app-group --test v1_group_service`.
- [ ] **Step 3: Implement** the wiring field, two-path flow, sort selection, and projection plumbing.
- [ ] **Step 4: Run GREEN**, then the crate's other suites that construct `GroupServiceImpl::new` to prove the `Option` wiring kept them compiling: `cargo test -p bcs-app-group` (focused `--test` filters acceptable per house rule; report what ran).

### Task 6: V1 `bcs-app-session` two-path sorting and projection

**Files:**
- Modify: `crates/application/v1/bcs-app-session/src/lib.rs` (`list` `:278` — consume the already-injected `self.history`; no constructor change)
- Modify: `crates/application/v1/bcs-app-session/src/projections.rs` (`project_summary` gains `last_activity_at: Option<u64>` param, assigned onto `SessionSummary`)
- Test: `crates/application/v1/bcs-app-session` (existing suite layout: `src/tests.rs` for facade units, `tests/` for integration, follow house style)

**Interfaces:**
- Consumes: Task 1 `SessionListSort` / summary field, Task 4 `MessageService::last_activity_by_session_ids`.
- Produces (D4/D5 flow, viewer scope unchanged):
  - default / `sort == CreatedAt`: today's repo pagination (`list_by_group` ordered page) runs first; one batched history lookup over the **page's** session ids populates the field; session with no rows → `Some(session.created_at)`;
  - `sort == LastActivity`: keep `count_by_group` for `total`, fetch the full viewer-scoped candidate set with `list_by_group(group_id, status, 0, total, None, participant_id)`, run one batched lookup over **all** candidate ids, sort in-app by `(activity DESC, session_id ASC)` (this in-app sort is the authoritative order for this mode), paginate in-app, project;
  - lookup error on **either** path → `ApplicationError::internal` (D6/Review Focus #4);
  - collected/avatar projections (`collected` gating, batched avatar read) unchanged on both paths.

- [ ] **Step 1: Write failing tests** with a stub `GroupMessageHistoryService` (map; erroring):
  - default ordering unchanged and field populated with the real value (message time ≫ `created_at`, Review Focus #6);
  - cross-sort consistency for the same session under all three modes;
  - `LastActivity` orders globally before pagination: craft an order that crosses the page boundary (page 1 tail vs page 2 head) and assert both pages reflect one global order; `total` unchanged;
  - no-message session sorts by its `created_at` fallback and reports it (Review Focus #2/#7);
  - viewer scope: a session the View Actor does not participate in never appears or affects the page under either sort;
  - erroring history service → request errors **on both sort modes** (Review Focus #4);
  - `collected` gating and avatar projection unchanged.
- [ ] **Step 2: Run RED** on the focused suite.
- [ ] **Step 3: Implement** the two-path flow and projection plumbing.
- [ ] **Step 4: Run GREEN**, then the crate's remaining suites (`cargo test -p bcs-app-session`, focused filters acceptable; report what ran).

### Task 7: V1 HTTP adapters + OpenAPI contracts

**Files:**
- Modify: `crates/adapters/http/bcs-api-http/src/v1/openapi/dto/group.rs` (`ListGroupsQuery` `:45` — add `#[serde(default)] pub sort: SortQuery`; `pub enum SortQuery { CreatedAt, LastActivity }`, `#[serde(rename_all = "snake_case")]`, `impl Default`, and `pub fn sort_filter(&self) -> GroupListSort` mirroring `membership_filter`/`kind_filter`)
- Modify: `crates/adapters/http/bcs-api-http/src/v1/openapi/dto/session.rs` (`ListSessionsQuery` `:254` — same shape mapping to `SessionListSort`)
- Modify: `crates/adapters/http/bcs-api-http/src/v1/openapi/routes/group.rs` (`list_groups` `:40` — pass `sort: query.sort_filter()`) and `routes/session.rs` (`list_sessions` `:82` — same)
- Modify: `api-contracts/v1/openapi/groups.yaml` and `api-contracts/v1/openapi/sessions.yaml` (list parameters + ordering descriptions per D3; D7 instability note on the `last_activity` description on both) and `api-contracts/v1/domain-models.yaml` (optional `last_activity_at` on both group summary models and on `SessionSummary`; note the public-catalog omission)
- Test: `crates/adapters/http/bcs-api-http/tests/group_routes/` and the session routes suite (existing locations)

**Interfaces:**
- Consumes: Tasks 5/6 application behavior.
- Produces: `GET …/groups?sort=last_activity` and `GET …/groups/{id}/sessions?sort=last_activity` reach the application enums; unknown variants → axum Query rejection → existing `invalid_request` 400 path (D3).

- [ ] **Step 1: Write failing route tests**: `sort` absent and `sort=created_at` → 200 with current ordering on both endpoints; `sort=last_activity` forwards (stubbed services observe `LastActivity`); `sort=LAST_ACTIVITY` / trailing-space variants → 400 on both (Review Focus #3); listed items' JSON contains `last_activity_at` on both endpoints while `public-groups` items do not.
- [ ] **Step 2/3/4:** RED → implement → GREEN via `cargo test -p bcs-api-http --test group_routes` and the session suite (file-filtered per layout).
- [ ] **Step 5:** Update the three YAML files in the same task; run whatever contract/drift check exists for them (report the exact command and result; if none is wired for these files, state that explicitly in the verification report).

### Task 8: Bootstrap wiring, D2 cost verification, final sweep

**Files:**
- Modify: `crates/bootstrap/bcs/src/server/resource_builders.rs` (`GroupServiceImpl::new` `:579` — chain `.with_group_message_history(group_message_history.clone())`; `SessionServiceImpl::new` `:605` is unchanged, it already receives the history service)

**Interfaces:**
- Consumes: Task 5's `with_group_message_history`.

- [ ] **Step 1:** Implement wiring; `cargo check -p bcs` compiles; the bootstrap test at `crates/bootstrap/bcs/tests/state_machine_history_read.rs:98` constructs the facade without the optional wiring and must keep compiling — fix only if the compiler disagrees.
- [ ] **Step 2: D2 cost-verification evidence** (attach command output to the implementation PR):
  - `EXPLAIN QUERY PLAN` for Q1/Q3 against the SQLite flavor and for Q2's chosen shape — capture via the store test harness or a documented manual run; record per-candidate range-scan shape explicitly (no "index max" claim without a plan that shows it);
  - `EXPLAIN` for the same shapes on real MySQL if the environment provides it (otherwise reported unavailable);
  - seeded skewed-volume runs (few candidates × deep history; many candidates × shallow history) with timings on the SQLite flavor;
  - decision recorded: ship read-time aggregates as-is, or add the D2 covering index(es) as a new uniquely numbered migration per dialect (frozen-migration rules) with before/after evidence. Anything beyond (denormalization) stops the work item and re-opens the design.
- [ ] **Step 3: Focused verification sweep** (record real output in the spec's verification section): `df -h /tmp` first; then, in order: `cargo test -p bcs-service-api --lib`, `cargo test -p bcs-message-store --lib`, `cargo test -p bcs-session-store --lib`, `cargo test -p bcs-message --lib`, `cargo test -p bcs-session --lib`, `cargo test -p bcs-app-group`, `cargo test -p bcs-app-session`, `cargo test -p bcs-api-http --test group_routes` + session route suite, plus the OpenAPI contract check from Task 7.
- [ ] **Step 4:** Report genuinely-unavailable gates (live MySQL, full singlebox E2E, full workspace regression) as not run, with reasons — mirror the verification-report style of existing specs.

## Verification commands

```sh
df -h /tmp
cargo check -p bcs-service-api -p bcs-test-support -p bcs-message-flow
cargo test -p bcs-service-api --lib
cargo test -p bcs-message-store --lib
cargo test -p bcs-session-store --lib
cargo test -p bcs-message --lib
cargo test -p bcs-session --lib
cargo test -p bcs-app-group
cargo test -p bcs-app-session
cargo test -p bcs-api-http --test group_routes   # plus the session routes suite
cargo check -p bcs
# D2 evidence: EXPLAIN QUERY PLAN / EXPLAIN captures and skewed-volume timings (Task 8 step 2)
git diff --check
```

## Contract (acceptance pinned by tests above)

`sort=last_activity` globally orders by the D1 unified activity key (message-log max merged with session-creation max for groups; message-log max for sessions; `created_at` fallbacks), id-ASC tie-breaks, before offset/limit slicing, `total` pre-pagination, on **both** endpoints. Absent/`created_at` sort reproduces today's ordering exactly, and the same item reports the same `last_activity_at` under every mode. A new empty session lifts its group; a deleted session's messages keep counting while its creation time drops out. `last_activity_at: Option<u64>` is `Some` on every `list_groups`/`list_sessions` item (normal, DM, session-only, viewer-scoped), omitted on `list_public_groups`. Invalid `sort` → 400 on both endpoints; any activity-store failure → 500 on either population path. No migrations/new indexes on the default path (D2 gate may authorize the additive covering index with recorded evidence), no write-path changes; legacy `/groups*` endpoints untouched.
