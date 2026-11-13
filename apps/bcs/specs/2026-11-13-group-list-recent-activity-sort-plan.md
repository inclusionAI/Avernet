# V1 Group list: sort by conversation last activity — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let V1 `GET /openapi/v1/collaboration/groups` order its full filtered result by the conversation's real last-activity time (`sort=last_activity`) and carry `last_activity_at` on group summaries, with byte-compatible defaults.

**Architecture:** Read-time aggregate over the existing persisted message log (`MAX(bcs_messages.created_at) GROUP BY group_id`, one batched query per list call) exposed through `MessageRepoPort` → application `GroupMessageHistoryService` (implemented by `bcs-message::MessageService` only) → V1 `GroupServiceImpl`, which already sorts globally before paginating. No schema migration, no write-path change, legacy endpoints and `list_public_groups` untouched.

**Tech Stack:** Rust, Tokio, async-trait defaulted trait methods, axum Query DTOs, sqlx-style store behind `bcs-db-api` (one SQL impl serving MySQL and SQLite via `DbSqlFlavor`), in-memory store for tests.

**Spec:** [2026-11-13-group-list-recent-activity-sort-design.md](2026-11-13-group-list-recent-activity-sort-design.md) (decisions D1–D7; the plan argues from it).

## Global Constraints

- Work item: 2026080400118003354. Branch/lifecycle (commit, push, PR) is owned by the dispatching pipeline task, not by this plan — no `git` mutation steps appear below.
- No schema migration, no new index, no changes to `bcs-group*`, session, or message write paths.
- Layered call direction: adapters → `service-api::application` → `core` → `port`; V1 facades never hold repo ports (they hold application services — the `GroupMessageHistoryService` precedent).
- No `cargo fmt` / global formatting; only touched lines change style.
- Compiles clean with default trait methods: no existing noop/test implementation of `MessageRepoPort` or `GroupMessageHistoryService` may require edits.
- Cargo discipline (workspace rule): build via the worktree `.cargo/config.toml` target-dir (`/tmp/cargo/Avernet-target`); `df -h /tmp` before heavy runs; focused `cargo test -p <pkg> --test <name>` only, no workspace-wide regression until final verification.
- Default ordering of every existing endpoint stays exactly as today.

## Review Focus

Inputs/conditions the spec does not spell out, and the expected behavior (each pinned by a test in the owning task):

1. An actor in more groups than one SQL bind chunk holds → query must chunk, results stay complete (Task 2).
2. A pre-cutoff / never-messaged group has no `bcs_messages` rows → reports `created_at`, never errors (Tasks 2, 4).
3. `sort=LAST_ACTIVITY`, `sort=last_activity ` (case/whitespace variants) → 400 `invalid_request`, not silent default (Task 5).
4. Activity store failure mid-list → request fails 500; no silently mis-ordered 200 (Task 4).
5. DM groups and `list_public_groups`: DM summaries populate the field and sort; public catalog items omit the field and keep `updated_at DESC` exactly (Task 4).

## Tasks

### Task 1: Service-API contract surface

**Files:**
- Modify: `crates/service-api/bcs-service-api/src/port/repo/message.rs` (trait `MessageRepoPort`)
- Modify: `crates/service-api/bcs-service-api/src/application/group_message.rs` (trait `GroupMessageHistoryService`, `:63`)
- Modify: `crates/service-api/bcs-service-api/src/application/v1/group.rs` (`ListGroups` `:312`, `NormalGroupSummary` `:143`, `DirectMessageGroupSummary` `:170`)

**Interfaces:**
- Produces:
  - `async fn last_activity_by_group_ids(&self, group_ids: &[String]) -> Result<std::collections::HashMap<String, u64>, MessageRepoError>` on `MessageRepoPort`, default body `Ok(Default::default())`; doc: store-owned env isolation, ms epoch, ids with no rows are absent from the map.
  - `async fn last_activity_by_group_ids(&self, group_ids: &[String]) -> Result<std::collections::HashMap<String, u64>, GroupUseCaseError>` on `GroupMessageHistoryService`, default body returning an empty map.
  - `pub enum GroupListSort { CreatedAt, LastActivity }` with `impl Default` = `CreatedAt`, in `application/v1/group.rs` next to `ListGroups`.
  - `ListGroups` gains `pub sort: GroupListSort`.
  - Both summary structs gain `#[serde(default, skip_serializing_if = "Option::is_none")] pub last_activity_at: Option<u64>` (epoch ms, D1/D4 semantics documented on the field).

- [ ] **Step 1: Write a failing serde test** in or beside `application/v1/group.rs` tests: `NormalGroupSummary` without `last_activity_at` deserializes to `None` (backward compat); `Some(123u64)` round-trips; `None` is omitted from serialized JSON.
- [ ] **Step 2: Run focused suite RED:** `cargo test -p bcs-service-api --lib` — new test fails (field/type missing).
- [ ] **Step 3: Implement** the trait methods, enum, `sort` field, and summary fields exactly as above.
- [ ] **Step 4: Run GREEN:** `cargo test -p bcs-service-api --lib`; then `cargo check -p bcs-test-support -p bcs-message -p bcs-message-flow -p bcs-message-store -p bcs-app-group` to prove every existing implementation compiles unedited against the defaulted methods.
- [ ] **Step 5:** Fix any struct-literal construction sites of the two summaries the compiler flags inside `service-api` only (tests elsewhere are handled by their owning tasks).

### Task 2: `bcs-message-store` implementations (memory + SQL)

**Files:**
- Modify: `crates/services/bcs-message-store/src/memory.rs`
- Modify: `crates/services/bcs-message-store/src/mysql.rs`
- Test: `crates/services/bcs-message-store/src/memory_tests.rs`, `crates/services/bcs-message-store/src/mysql_tests.rs` (SQLite flavor)

**Interfaces:**
- Consumes: Task 1's `MessageRepoPort::last_activity_by_group_ids`.
- Produces: both stores override the method. SQL: `SELECT group_id, MAX(created_at) FROM bcs_messages WHERE env = ? AND group_id IN (<chunked binds>) GROUP BY group_id`, chunk size following the store's existing IN-list batching precedent (see `get_messages_by_ids`), one round trip per chunk; aggregates merge across chunks. Memory: single pass over stored messages, env-filtered.

- [ ] **Step 1: Write failing tests** (same logical cases for both stores, following each file's existing fixtures):
  - empty `group_ids` → empty map and no query work;
  - two groups with several messages each → per-group `MAX(created_at)`, correct ms values;
  - unknown/group-with-no-rows → absent from map (Review Focus #2);
  - ids beyond one chunk (`> chunk_size` distinct ids) → all present (Review Focus #1);
  - messages of another env never counted (env isolation case already conventional in `mysql.rs`).
- [ ] **Step 2: Run RED:** `cargo test -p bcs-message-store --lib last_activity` (or the file-specific test filter matching house style) — fails, method unimplemented.
- [ ] **Step 3: Implement** both overrides.
- [ ] **Step 4: Run GREEN:** same focused filter; then `cargo test -p bcs-message-store --lib` to catch regressions in neighbors.

### Task 3: `bcs-message::MessageService` delegation

**Files:**
- Modify: `crates/services/bcs-message/src/lib.rs` (`MessageService` `:33`, `impl GroupMessageHistoryService for MessageService` `:383`)
- Test: `crates/services/bcs-message/src/tests.rs`

**Interfaces:**
- Consumes: Task 1 trait methods; `MessageService.message_repo` (`Arc<dyn MessageRepoPort>`).
- Produces: `MessageService::last_activity_by_group_ids` delegating to the repo and mapping `MessageRepoError` → `GroupUseCaseError` with the file's existing error-mapping style. `BcsGroupMessageHistory` (bcs-message-flow) is NOT modified — defaulted empty map stands (D5).

- [ ] **Step 1: Write failing test** in `src/tests.rs`: service with a stub repo returning a fixed map → same map out; stub repo error → `GroupUseCaseError` propagated (not swallowed into empty).
- [ ] **Step 2/3/4:** RED via `cargo test -p bcs-message --lib last_activity`; implement; GREEN; then `cargo test -p bcs-message --lib`.
- [ ] **Step 5:** `cargo check -p bcs-message-flow` to record that the untouched fallback still compiles.

### Task 4: V1 `bcs-app-group` sorting and projection

**Files:**
- Modify: `crates/application/v1/bcs-app-group/src/lib.rs` (`GroupServiceImpl` `:64` — add `group_message_history: Option<Arc<dyn GroupMessageHistoryService>>` field and `pub fn with_group_message_history(...) -> Self`, matching `with_bot_authority_hook` style)
- Modify: `crates/application/v1/bcs-app-group/src/service.rs` (`list_groups` `:7`, `list_public_groups` `:132`)
- Modify: `crates/application/v1/bcs-app-group/src/projections.rs` (`project_summary` `:182` gains `last_activity_at: Option<u64>` param, assigned onto both summary structs)
- Test: `crates/application/v1/bcs-app-group/tests/v1_group_service.rs` (+ its module dir if the suite splits cases there)

**Interfaces:**
- Consumes: Task 1 `GroupListSort` / summary field, Task 3 service method.
- Produces:
  - `list_groups` behavior: after the existing in-memory filter, when `command.sort == GroupListSort::LastActivity` and the filtered set is non-empty, call the wired history service once with all filtered group ids (no wiring or empty set → empty map, per D6); sort key = `map.get(&group.id).copied().unwrap_or(group.created_at)`, order key `DESC`, tie-break `group_id ASC`; otherwise the unchanged `cmp_by_created_at_desc_group_id_asc`. Pagination unchanged. Look up the same map when projecting page items: `Some(key)` per D1 fallback.
  - `list_public_groups`: no activity call; projects `last_activity_at: None` (D4).
  - Activity-lookup error → propagate as `ApplicationError::internal` (D6).

- [ ] **Step 1: Write failing tests** with a stub `GroupMessageHistoryService` (one wired with a map; one that errors; construction without `with_group_message_history`):
  - `sort: LastActivity` orders by the map with `created_at` fallback, `group_id ASC` tie on equal keys, and `total`/pagination slice after the global sort;
  - default/`CreatedAt` sort is byte-identical to current behavior (ordering regression guard);
  - `last_activity_at` is `Some` on normal and DM summaries (Review Focus #5), including a zero-message group (fallback value);
  - `membership: SessionOnly` group participates and carries the field;
  - erroring history service → request errors (Review Focus #4);
  - unwired facade → `created_at` ordering preserved and field `Some(created_at)`;
  - `list_public_groups` unchanged ordering, field `None`/absent (Review Focus #5).
- [ ] **Step 2: Run RED** on the new cases: `cargo test -p bcs-app-group --test v1_group_service`.
- [ ] **Step 3: Implement** the wiring field, sort selection, projection plumbing, and call-site updates.
- [ ] **Step 4: Run GREEN**, then the crate's other suites that construct `GroupServiceImpl::new` to prove the `Option` wiring kept them compiling: `cargo test -p bcs-app-group` (focused `--test` filters acceptable per house rule; report what ran).

### Task 5: V1 HTTP adapter + OpenAPI contract

**Files:**
- Modify: `crates/adapters/http/bcs-api-http/src/v1/openapi/dto/group.rs` (`ListGroupsQuery` `:45` — add `#[serde(default)] pub sort: SortQuery`; add `pub enum SortQuery { CreatedAt, LastActivity }`, `#[serde(rename_all = "snake_case")]`, `impl Default`, and `pub fn sort_filter(&self) -> GroupListSort` mirroring `membership_filter`/`kind_filter`)
- Modify: `crates/adapters/http/bcs-api-http/src/v1/openapi/routes/group.rs` (`list_groups` `:40` — pass `sort: query.sort_filter()` into `ListGroups`)
- Modify: `api-contracts/v1/openapi/groups.yaml` (`list_groups` parameters + ordering description per D3; document D7 instability on the `last_activity` description) and `api-contracts/v1/domain-models.yaml` (optional `last_activity_at` on both summary models; note omission on public catalog items)
- Test: `crates/adapters/http/bcs-api-http/tests/group_routes/` (existing list-suite location)

**Interfaces:**
- Consumes: Task 4 application behavior.
- Produces: `GET …/groups?sort=last_activity` reaches the application enum; unknown variant → axum Query rejection → existing `invalid_request` 400 path (D3).

- [ ] **Step 1: Write failing route tests**: `sort` absent and `sort=created_at` → 200 with current ordering; `sort=last_activity` forwards (assert stubbed service observed `GroupListSort::LastActivity`); `sort=LAST_ACTIVITY` / trailing-space variant → 400 (Review Focus #3); a listed item's JSON contains `last_activity_at` while `public-groups` items do not.
- [ ] **Step 2/3/4:** RED → implement → GREEN via `cargo test -p bcs-api-http --test group_routes` (file-filtered per suite layout).
- [ ] **Step 5:** Update the two YAML files in the same task; run whatever contract/drift check exists for them (report the exact command and result; if none is wired for these files, state that explicitly in the verification report).

### Task 6: Bootstrap wiring and final verification

**Files:**
- Modify: `crates/bootstrap/bcs/src/server/resource_builders.rs` (`GroupServiceImpl::new` `:579` — chain `.with_group_message_history(group_message_history.clone())`)

**Interfaces:**
- Consumes: Task 4's `with_group_message_history`.

- [ ] **Step 1:** Implement wiring; `cargo check -p bcs` compiles; the bootstrap test at `crates/bootstrap/bcs/tests/state_machine_history_read.rs:98` constructs the facade without the optional wiring and must keep compiling — fix only if the compiler disagrees.
- [ ] **Step 2: Focused verification sweep** (record real output in the spec's verification section):
  `df -h /tmp` first; then, in order: `cargo test -p bcs-service-api --lib`, `cargo test -p bcs-message-store --lib`, `cargo test -p bcs-message --lib`, `cargo test -p bcs-app-group`, `cargo test -p bcs-api-http --test group_routes` (and the DTO/serialization suite if separate), plus the OpenAPI contract check from Task 5.
- [ ] **Step 3:** Report genuinely-unavailable gates (live MySQL, full singlebox E2E, full workspace regression) as not run, with reasons — mirror the verification-report style of existing specs.

## Verification commands

```sh
df -h /tmp
cargo check -p bcs-service-api -p bcs-test-support -p bcs-message-flow
cargo test -p bcs-service-api --lib
cargo test -p bcs-message-store --lib
cargo test -p bcs-message --lib
cargo test -p bcs-app-group
cargo test -p bcs-api-http --test group_routes
cargo check -p bcs
git diff --check
```

## Contract (acceptance pinned by tests above)

`sort=last_activity` globally orders by `MAX(bcs_messages.created_at)` per group (fallback `created_at`), `group_id ASC` tie-break, before offset/limit slicing; `total` is pre-pagination. Absent/`created_at` sort reproduces today's ordering exactly. `last_activity_at: Option<u64>` is `Some` on every `list_groups` item (normal, DM, session-only), omitted on `list_public_groups`. Invalid `sort` → 400; activity-store failure → 500. No migrations, no write-path changes; legacy `/groups*` endpoints untouched.
