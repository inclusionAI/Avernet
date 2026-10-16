# Tasks: Group Context API v1 Publish

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

> Source: generated from `spec-v1-publish.md` only, per request. Existing archived plan files in this directory were not read.

> Runtime boundary correction (2026-10-12): GroupContext is **not** implemented as an OpenAPI / `x-avernet-principal` runtime feature in this iteration. The only runtime HTTP surface for `bcs-cli group-context` is the direct `bcs-http` `/groupcontext/*` path, mounted exactly once, using the same backend/auth chain as `bcs-cli chat`. Do not add or re-enable `bcs-api-http` runtime routes for `/groupcontext/*`.

## Task 1: Define Group Context domain and V1 application contracts [x]
- **Goal:** Add transport-independent types for Group Context entries, scope levels, permissions, commands, responses, and errors.
- **Files:** `crates/contracts/bcs-domain/src/group_context.rs`, `crates/contracts/bcs-domain/src/lib.rs`, `crates/service-api/bcs-service-api/src/application/v1/group_context.rs`, `crates/service-api/bcs-service-api/src/application/v1/mod.rs`, `crates/service-api/bcs-service-api/tests/v1_group_context_application_contracts.rs`
- **Done when:**
  - [x] `ScopeLevel` supports exactly `group`, `session`, and `round`.
  - [x] Entry model includes `id`, stable `context_id`, `name`, `scope_level`, raw `scope`, `content`, extensible JSON-object `visible_to` / `collect_from` policies, `supersedes` pointing to the old physical `id`, `change_reason`, date-typed `valid_from`, `valid_to`, `tx_time`, `gmt_create`, and `gmt_modified`.
  - [x] Request/response contracts cover `add`, `update`, `list`, `get`, and `systemset` with the spec's success and error statuses.
  - [x] Contract tests cover default `scope_levels`, document that `get.limit` is currently a compatibility/no-op parameter because at most one active result can exist per selected group/session/round scope, permission labels `R`/`W`/`WR`, JSON policy shape `{ "userIds": [...] }`, and invalid enum values.
- **Depends on:** —

## Task 2: Add durable Group Context storage and migrations [x]
- **Goal:** Persist versioned Group Context rows with active-version uniqueness and query support for all specified access patterns.
- **Files:** `migrations/oceanbase/031_group_context.sql`, `migrations/sqlite/032_group_context.sql`, `crates/bootstrap/bcs/src/migrations.rs`, `crates/bootstrap/bcs/src/migrations/baseline.rs`, `crates/bootstrap/bcs/src/migrations/schema.rs`, `crates/services/bcs-group-context-store/Cargo.toml`, `crates/services/bcs-group-context-store/src/lib.rs`, `crates/services/bcs-group-context-store/src/memory.rs`, `crates/services/bcs-group-context-store/src/store.rs`, `crates/services/bcs-group-context-store/tests/conformance_group_context_repo.rs`, `Cargo.toml`
- **Done when:**
  - [x] OceanBase and SQLite schemas store the full entry model and preserve superseded historical rows, with `visible_to_json` / `collect_from_json` as JSON object policies rather than bare arrays.
  - [x] `context_id` uses a short string column, no `env`/tenant discriminator is added to the model, `valid_from`/`valid_to`/`tx_time` use date/time columns, and audit columns follow internal names `gmt_create`/`gmt_modified`.
  - [x] The schema enforces at most one active row per `(name, scope)` with native partial/filtered unique indexes: SQLite `WHERE valid_to IS NULL` and OceanBase `FILTER (valid_to IS NULL)`; no `env`/tenant key or `active_flag` compatibility column is added unless the target OB version proves unsupported.
  - [x] Repository APIs can create active entries, atomically supersede active entries, fetch unique active entries by `(name, scope)`, and list active entries by multiple scopes while relying on the database partial/filtered unique index as the final concurrency guard.
  - [x] Supersede writes old `valid_to = tx_time` and creates the new version with unchanged `context_id` and `supersedes` pointing to the old physical `id`.
  - [x] Conformance tests pass for memory/local DB behavior, duplicate active conflicts from the partial/filtered unique constraint, and historical version retention.
- **Depends on:** Task 1

## Task 3: Implement Group Context application semantics [x]
- **Goal:** Implement the spec's business rules independent of HTTP transport.
- **Files:** `crates/application/v1/bcs-app-group-context/Cargo.toml`, `crates/application/v1/bcs-app-group-context/src/lib.rs`, `crates/application/v1/bcs-app-group-context/src/service.rs`, `crates/application/v1/bcs-app-group-context/src/scope.rs`, `crates/application/v1/bcs-app-group-context/src/permissions.rs`, `crates/application/v1/bcs-app-group-context/tests/group_context_service.rs`, `Cargo.toml`
- **Done when:**
  - [x] Scope resolution uses query-string `groupId`, `sessionId`, or `roundId` selected by `scope_level` without concatenation; `groupId` is always required, while `sessionId`/`roundId` are required only when selected.
  - [x] `add` validates content size <= 4KB, valid JSON policy objects, non-empty `collect_from.userIds`, actor membership in `collect_from.userIds`, and returns `conflict` when an active `(name, scope)` already exists.
  - [x] `update` finds the unique active row, checks actor membership in the old row's `collect_from.userIds`, preserves old values for omitted fields, records `change_reason`, and maps missing rows to `not_found`.
  - [x] Concurrent or stale supersede attempts map to `conflict`.
  - [x] `systemset` performs update-or-create without requiring ordinary bot `actor_id` membership in `collect_from.userIds`, while still requiring valid JSON policy objects, non-empty `collect_from.userIds`, and content size <= 4KB.
  - [x] `list` returns only active rows where actor is visible through `visible_to.userIds` or writable through `collect_from.userIds`, with correct `R`/`W`/`WR` permissions.
  - [x] `get` returns only active rows where actor is visible through `visible_to.userIds` and sorts round → session → group; `limit` is accepted for forward compatibility but currently does not affect results because selected scopes yield at most group/session/round one row each.
- **Depends on:** Task 2

## Task 4: Expose `/groupcontext/*` HTTP endpoints [x]
- **Goal:** Add the five POST endpoints on the current direct BCS HTTP path and map HTTP/query/header/body fields to the V1 application service.
- **Important correction (2026-10-12):** The runtime endpoints used by `bcs-cli group-context` must be mounted **only once**, on the direct `bcs-http` router, matching the `bcs-cli chat` backend/auth path. Do **not** also mount the same `/groupcontext/*` paths from `bcs-api-http`, because BCS bootstrapping merges both routers and duplicate Axum routes panic with `Overlapping method route`. The planned `x-avernet-principal` OpenAPI boundary is out of scope for the current CLI path.
- **Files:** `crates/adapters/http/bcs-http/src/routes/group_context.rs`, `crates/adapters/http/bcs-http/src/router.rs`, `crates/adapters/http/bcs-http/tests/group_context_routes.rs`. Do **not** add GroupContext implementation work under `crates/adapters/http/bcs-api-http/*`; if duplicate `/groupcontext/*` routes exist there, remove/disable their runtime mount.
- **Done when:**
  - [x] Routes exist exactly once at runtime for `POST /groupcontext/add`, `/groupcontext/update`, `/groupcontext/list`, `/groupcontext/get`, and `/groupcontext/systemset`.
  - [x] `groupId` is required from URL query params, while `sessionId` and `roundId` are query params required only when the requested `scope_level`/`scope_levels` need them.
  - [x] `actor_id` for normal bot APIs is resolved server-side from the same trusted bot token/header chain used by `bcs-cli chat`; it is not accepted from URL query or JSON body. `systemset` still requires an authenticated direct-BCS caller but does not require ordinary bot membership in `collect_from.userIds`.
  - [x] HTTP status codes and JSON error shapes match the spec: `ok`, `invalid_param`, `permission_denied`, `not_found`, `conflict`, `payload_too_large`, and `internal_error`.
  - [x] Route tests cover BCN CLI-style query-string requests, success, invalid parameters, unauthenticated callers, permission denied, conflict, not found, payload too large, list visibility, and get isolation; the future `x-avernet-principal` OpenAPI boundary is not used by the current CLI path.
- **Depends on:** Task 3

## Task 5: Wire services in bootstrap and test support [x]
- **Goal:** Make the Group Context service available in production composition and test harnesses.
- **Files:** `crates/bootstrap/bcs/src/lib.rs`, `crates/bootstrap/bcs/src/server.rs`, `crates/bootstrap/bcs/src/plugins.rs`, `crates/service-api/bcs-services-container/src/services.rs`, `crates/service-api/bcs-services-container/src/test_support.rs`, `Cargo.toml`
- **Done when:**
  - [x] Production services container receives a configured Group Context application service backed by the selected DB/store plugin.
  - [x] The `/groupcontext/*` runtime endpoints are mounted through the direct `bcs-http` router only; bootstrap must not merge another `bcs-api-http` router that registers the same concrete paths.
  - [x] Test support can construct HTTP routers with in-memory Group Context storage.
  - [x] Existing bootstrap and HTTP adapter tests continue to compile without feature-specific setup changes.
- **Depends on:** Task 4

## Task 6: Add bcs-cli Group Context invocation support [x]
- **Goal:** Let bots call Group Context through `bcs-cli` over the same direct bcs-http backend/auth chain as `bcs-cli chat`, using the same query-string context and JSON body shape as the HTTP contract.
- **Files:** `crates/tools/bcs-cli/src/main.rs`, `crates/tools/bcs-cli/src/client.rs`, `crates/tools/bcs-cli/tests/group_context_client.rs`
- **Done when:**
  - [x] `bcs-cli` exposes commands or a supported invocation path for `groupcontext add`, `update`, `list`, `get`, and `systemset`.
  - [x] CLI calls construct BCN-style URLs with `groupId`, optional `sessionId`, and optional `roundId` as query parameters, and never send actor id in the URL query or JSON body.
  - [x] CLI request bodies preserve the spec JSON policy shape `{ "userIds": [...] }` for `visible_to` and `collect_from`.
  - [x] Normal bot calls rely on the existing `bcs-cli chat`-style token/header auth mechanism; the backend derives the actor, and the current CLI path does not require `x-avernet-principal`.
  - [x] CLI tests or scripted examples cover a representative add/update/list/get flow against the mounted HTTP API or a mock client.
- **Depends on:** Task 4, Task 5

## Task 7: Update documentation for the direct GroupContext HTTP surface [x]
- **Goal:** Document the current direct `bcs-http` `/groupcontext/*` surface used by `bcs-cli group-context`; do not implement or mount an OpenAPI runtime surface in this iteration.
- **Files:** `docs/group-context-api.md`. Do **not** add `crates/adapters/http/bcs-api-http/src/v1/group_context.rs` as a runtime router, and do **not** merge any `bcs-api-http` router that registers `/groupcontext/*`.
- **Done when:**
  - [x] Docs describe all five direct POST endpoints, request schemas, response schemas, and error statuses from the spec.
  - [x] Docs explicitly state the current runtime path is direct `bcs-http`, not OpenAPI / `x-avernet-principal`.
  - [x] `visible_to` / `collect_from` JSON object policy shape is documented in spec as `{ "userIds": [...] }`; `visible_to.userIds` empty-array semantics, `collect_from.userIds` non-empty validation, and `systemset` success response without `context_id` are documented.
  - [x] Scope-level behavior states that raw query-string `groupId`, `sessionId`, or `roundId` becomes `scope` directly, matching BCN CLI request shape.
  - [x] Runtime verification proves `/groupcontext/*` is mounted exactly once.
- **Depends on:** Task 4

## Task 8: Add end-to-end walkthrough coverage for 谁是卧底 [x]
- **Goal:** Verify the spec's complete walkthrough behavior against the HTTP API.
- **Files:** `crates/bootstrap/bcs/tests/integration_group_context.rs`, `tests/fixtures/group_context_who_is_spy.json`
- **Done when:**
  - [x] Admin/system can create or supersede a group-scoped `game_rule` context.
  - [x] `judge_bot` can add `player_word_1`, `player_word_2`, `player_role`, and `player_state` at session scope.
  - [x] Updating `player_state` supersedes the previous active row and preserves historical state.
  - [x] `judge_bot` can get both word contexts and role context.
  - [x] Player 张三 can list/get `game_rule`, `player_word_1`, and `player_state`, but cannot see `player_word_2` or `player_role`.
  - [x] The same assertions pass through the mounted HTTP router, not just service-unit APIs.
- **Depends on:** Task 5, Task 7

## Task 9: Final verification and acceptance checklist [x]
- **Goal:** Confirm the implementation satisfies every acceptance point in `spec-v1-publish.md` and does not regress existing behavior.
- **Files:** `specs/2026-09-09-group-context-api/tasks.md`, relevant test output captured in PR/commit notes
- **Done when:**
  - [x] `cargo fmt --all` passes.
  - [x] Targeted tests for domain/application/store/HTTP Group Context behavior pass.
  - [x] Relevant existing group/session/API tests pass.
  - [x] Every spec API behavior and error code has at least one passing test or documented verification note.
  - [x] `tasks.md` reflects completed status for all implementation tasks.
- **Depends on:** Task 8

---

## Groups

> Groups bundle tasks into end-to-end units of work. `implement` executes
> one group at a time and runs code review on each group before moving on.
> List groups in execution order; each task appears in exactly one group.

- **Group A — Domain and storage foundation:** Tasks 1, 2
  - Theme: Establish the versioned data model and durable active-version repository.
- **Group B — Application behavior:** Task 3
  - Theme: Implement scope, validation, permissions, supersede, and query semantics.
- **Group C — HTTP and composition:** Tasks 4, 5
  - Theme: Expose the API over HTTP and wire it into runtime/test composition.
- **Group D — CLI, published contract, and walkthrough:** Tasks 6, 7, 8
  - Theme: Add the bcs-cli bot invocation path, document the direct HTTP surface, and prove the 谁是卧底 scenario end-to-end.
- **Group E — Verification:** Task 9
  - Theme: Final spec acceptance check and regression verification.

---

## Change Set: 2026-10-12 GroupContext V1 Refinements

> Source: incremental update from `spec-v1-publish.md` section “本次新增/变更（2026-10-12）” and `plan-v1-refinements.md`.
> These tasks are appended intentionally; earlier completed v1 publish tasks remain unchanged for history.

## Task 10: Simplify 谁是卧底 demo prompt [x]
- **Goal:** Make the current demo usable for GroupContext manual testing without depending on state_machine rendering.
- **Files:** `../../singlebox/agents/group-context-undercover-test/referee/skills/groupcontext-undercover/SKILL.md`, `../../.standalone-openclaw/workspaces/groupcontext-undercover-referee/skills/groupcontext-undercover/SKILL.md`
- **Done when:**
  - [x] Referee prompt explicitly says not to use state_machine or complex collaborate run flow.
  - [x] Referee prompt focuses on `group-context add/get/list/update` and visible_to isolation.
  - [x] Referee prompt requires the BCS group chat `sessionId` for session-scoped GroupContext operations.
  - [x] Current generated referee workspace is updated so manual testing can continue before code implementation.
- **Depends on:** —

## Task 11: Update domain and service contracts for valid fields and superseded state [x]
- **Goal:** Expose the new business-validity and system-version fields in transport-independent contracts.
- **Files:** `crates/contracts/bcs-domain/src/group_context.rs`, `crates/service-api/bcs-service-api/src/application/v1/group_context.rs`, `crates/service-api/bcs-service-api/tests/v1_group_context_application_contracts.rs`
- **Done when:**
  - [x] `GroupContextEntry` includes `is_superseded: bool` and `gmt_superseded: Option<GroupContextTime>`.
  - [x] `GroupContextEntry::is_active()` means `!is_superseded`, not `valid_to.is_none()`.
  - [x] `AddGroupContext`, `UpdateGroupContext`, and `SystemSetGroupContext` accept optional `valid_from` / `valid_to` as business fields.
  - [x] `GroupContextListItem` is meta-only: `name`, `scope_level`, `scope`, `permission`, `gmt_create`, `gmt_modified`; it does not expose `content`, `valid_from`, or `valid_to`.
  - [x] `GroupContextGetItem` continues to expose content and business validity for visible current versions.
  - [x] Contract tests cover serialization/deserialization and prove list items cannot serialize `content` or valid fields.
- **Depends on:** Task 10

## Task 12: Update durable and memory stores to use `is_superseded` as the current-version marker [x]
- **Goal:** Persist system version state separately from business valid fields and keep one current row per `(name, scope)`.
- **Files:** `migrations/sqlite/032_group_context.sql`, `migrations/oceanbase/031_group_context.sql`, `crates/services/bcs-group-context-store/src/store.rs`, `crates/services/bcs-group-context-store/src/memory.rs`, `crates/services/bcs-group-context-store/tests/conformance_group_context_repo.rs`
- **Done when:**
  - [x] Schema contains `is_superseded` and `gmt_superseded` columns.
  - [x] SQLite current-version unique index uses `WHERE is_superseded = 0`.
  - [x] OceanBase current-version unique index uses `FILTER (is_superseded = 0)` or an equivalent supported implementation documented in the migration.
  - [x] Store select/insert/update mapping reads and writes `is_superseded` / `gmt_superseded`.
  - [x] `create_active` inserts `is_superseded=false`, `gmt_superseded=null`.
  - [x] `supersede_active` marks the old row `is_superseded=true`, sets `gmt_superseded=params.tx_time`, inserts the new row as `is_superseded=false`, and does not modify the old row's `valid_to`.
  - [x] `get_active` and `list_active_by_scopes` filter by `is_superseded=false`, not `valid_to IS NULL`.
  - [x] Store tests prove a current row with `valid_to < now` still blocks duplicate add and is returned by active queries.
- **Depends on:** Task 11

## Task 13: Implement application semantics for business validity and meta-only list [x]
- **Goal:** Apply patch-style update inheritance and ensure query behavior matches the refined spec.
- **Files:** `crates/application/v1/bcs-app-group-context/src/service.rs`, `crates/application/v1/bcs-app-group-context/tests/group_context_service.rs`
- **Done when:**
  - [x] `add` uses provided `valid_from` / `valid_to`, defaulting only `valid_from` to server now and `valid_to` to null.
  - [x] `update` inherits omitted `content`, `valid_from`, `valid_to`, `visible_to`, and `collect_from` from the previous current version.
  - [x] `update` can change only the valid range without changing content or permissions.
  - [x] `systemset` follows the same create/supersede validity semantics as the spec.
  - [x] `list` maps only meta fields and permission; it does not expose content or valid fields.
  - [x] `get` returns current versions regardless of whether business `valid_to` is in the past.
  - [x] Application tests cover add defaults, update inheritance, permission inheritance, list meta-only output, and no business-expiry filtering.
- **Depends on:** Task 12

## Task 14: Add HTTP time parsing and wire new request/response fields [x]
- **Goal:** Accept the specified time input formats at `/groupcontext/*` and expose the refined wire response shapes on the direct `bcs-http` runtime path.
- **Files:** `crates/adapters/http/bcs-http/src/routes/group_context.rs`, `crates/adapters/http/bcs-http/tests/group_context_routes.rs`; do not add/mount duplicate `bcs-api-http` runtime routes for the same concrete paths.
- **Done when:**
  - [x] `add`, `update`, and `systemset` request bodies accept optional `valid_from` / `valid_to`.
  - [x] HTTP parsing accepts RFC3339 datetimes with timezone.
  - [x] HTTP parsing accepts `YYYY-MM-DD HH:MM:SS` as server-local timezone time.
  - [x] HTTP parsing accepts Unix timestamp seconds and milliseconds.
  - [x] HTTP parsing rejects pure date strings such as `2026-10-12` with `invalid_param`.
  - [x] HTTP list response contains `scope`, `permission`, `gmt_create`, and `gmt_modified`, and omits `content`, `valid_from`, and `valid_to`.
  - [x] Route tests cover all accepted/rejected time forms and the list/get response shape split.
- **Depends on:** Task 13

## Task 15: Update bcs-cli Group Context commands for validity fields [x]
- **Goal:** Let bots pass business valid ranges through CLI while preserving the direct bcs-http auth path.
- **Files:** `crates/tools/bcs-cli/src/main.rs`, `crates/tools/bcs-cli/src/client.rs`, `crates/tools/bcs-cli/tests/group_context_client.rs`
- **Done when:**
  - [x] `bcs-cli group-context add` supports optional `--valid-from` and `--valid-to`.
  - [x] `bcs-cli group-context update` supports optional `--valid-from` and `--valid-to`.
  - [x] `bcs-cli group-context systemset` supports optional `--valid-from` and `--valid-to` if systemset remains content-writing in this CLI path.
  - [x] CLI forwards valid fields as JSON only when provided, preserving update patch semantics for omitted fields.
  - [x] CLI continues to omit actor id from query/body and relies on the same direct backend auth path as `bcs-cli chat`.
  - [x] CLI tests cover request body shape for valid fields and omission behavior.
- **Depends on:** Task 14

## Task 16: Update docs for refined GroupContext v1 [x]
- **Goal:** Keep the documented direct `bcs-http` API behavior aligned with the refined implementation; OpenAPI runtime support remains out of scope.
- **Files:** `docs/group-context-api.md`. Do **not** add/mount `bcs-api-http` runtime `/groupcontext/*` routes.
- **Done when:**
  - [x] Docs include `valid_from` / `valid_to` request fields for add/update/systemset.
  - [x] Docs state that list response is meta-only and omits `content`, `valid_from`, and `valid_to`.
  - [x] Docs state that `is_superseded=false` defines the current version and that `valid_from` / `valid_to` are business fields.
  - [x] Docs state that no history API is provided in this release.
  - [x] Docs list accepted time formats and explicitly reject pure date strings.
  - [x] Verification confirms the documented direct API is the only mounted runtime route set for `/groupcontext/*`.
- **Depends on:** Task 15

## Task 17: Final verification for GroupContext v1 refinements [x]
- **Goal:** Verify the refined implementation and manual demo remain usable end to end.
- **Files:** `specs/2026-09-09-group-context-api/spec-v1-publish.md`, `specs/2026-09-09-group-context-api/plan-v1-refinements.md`, `specs/2026-09-09-group-context-api/tasks.md`, relevant test output captured in PR/commit notes
- **Done when:**
  - [x] Changed Task 17 Rust files were formatted with `rustfmt`; `cargo fmt --all -- --check` was attempted and still reports pre-existing repository-wide formatting drift outside the GroupContext changes.
  - [x] Targeted domain/service/store/application/HTTP/CLI tests for GroupContext pass.
  - [x] Existing GroupContext walkthrough tests are updated for meta-only list and pass.
  - [x] Manual 谁是卧底 simplified prompt can create session-scoped word/state contexts and players can get their assigned word with the group chat session id.
  - [x] `tasks.md` reflects completed status for Tasks 10–17.
- **Depends on:** Task 16

---

## Groups for 2026-10-12 Refinements

- **Group F — Demo prompt unblock:** Task 10
  - Theme: Let manual application testing continue without state_machine rendering issues.
- **Group G — Contract and storage semantics:** Tasks 11, 12
  - Theme: Separate business validity from system current-version state.
- **Group H — Application and HTTP behavior:** Tasks 13, 14
  - Theme: Implement patch inheritance, time parsing, and meta-only list behavior.
- **Group I — CLI and docs:** Tasks 15, 16
  - Theme: Expose the refined direct bcs-http API through bot tooling and documentation; OpenAPI runtime support is not part of this iteration.
- **Group J — Verification:** Task 17
  - Theme: Final regression and manual demo acceptance.
