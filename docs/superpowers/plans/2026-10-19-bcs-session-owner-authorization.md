# BCS Session-owner Authorization Implementation Plan

> **For agentic workers:** Use executing-plans to implement this plan task-by-task, inline without delegation.

**Goal:** Allow owners to change their Session Bot's mode and allow Session membership to grant read-only Group detail access.

**Architecture:** Keep authorization in the V1 application facades. Preserve management gates and identity selection. Add a fallible, Group-scoped batch existence query through the Session service/repository contracts, implemented by memory and SQL stores; do not loop over owned Bots or load full Sessions.

**Tech Stack:** Rust, async-trait, Tokio, SQLite/MySQL repository implementations.

## Approved design

- Existing Session managers retain mode mutation rights. A Human may additionally change an existing owned Bot participant's auto/muted mode, but not its message_view_scope or other Session fields.
- Existing Group detail rules remain. Also allow the selected Actor, or a Human's exact-created_by owned Bot, to participate in any Session of that Group. Membership does not confer Group editing/deletion/member-management rights.
- Preserve Human self-mode rules, missing-participant non-disclosure, public Group reads, inactive/missing Group handling, and error propagation.
- No schema, deployment configuration, or HTTP request/response changes. Service and repository contracts gain an additive fallible query; implementations without the capability fail closed.

## Tasks

- [x] Add standalone regression suites for both facades using real in-memory repositories. Run `cargo test -p bcs-app-session --test session_owner_mode` and `cargo test -p bcs-app-group --test session_group_detail`; verify the permitted operations currently return Forbidden.
- [x] Add `has_group_session_participant(group_id: &str, actor_ids: &[String]) -> Result<bool, ...>` to the Session service and repository. SQL uses Group/env predicates, bounded actor chunks and `LIMIT 1`; memory performs one locked scan with an Actor set. Wire both Session service implementations.
- [x] In Bot-mode authorization, use `can_manage_session(...) || principal_can_act_as(...target...)`. Keep scope authorization separate. In Group detail authorization, preserve direct/owned Group participation, then perform one batch existence request for the selected Actor and owned Bots.
- [x] Add shared repository conformance coverage for empty batches, mixed actors, wrong Group, removals/deletion, and memory/SQLite equivalence; add query-count and query-failure checks.
- [x] Update OpenAPI and application/repository contract documentation, including propagation and access-cost analysis.
- [x] Check source line counts; split the existing oversized Session facade by responsibility without global formatting. Keep new regression files below 1,000 lines.
- [x] Run affected facade, Session service/store and contract suites; run whitespace and applicable architecture checks. Record limitations; do not commit or push without a separate request.

## Access-cost acceptance

Group details retain the existing single owned-Bot lookup. The additional existence query returns at most one row per chunk of at most 200 Actor IDs, stops at the first match, and is scoped by environment and Group. No per-Bot Session queries, full Session reads, caches or transactions are added. Database failures remain errors, never success or misleading Forbidden responses. Production MySQL load is not inferred from SQLite tests.

- For `A` candidate Actors, the added SQL cost is zero queries for empty input, otherwise at most `ceil(A / 200)` queries, with at most 202 bound parameters per query. A hit short-circuits subsequent batches. The existing participant index starts with `(env, group_id, bot_uuid)`: SQLite creates it in bootstrap `migrations/schema.rs`; MySQL migrations `004_add_session_collection.sql` and `005_add_session_collection_timestamp.sql` create `idx_collected` and `idx_collected_at` with that prefix. The Session join verifies current parent existence with its environment/Session key. No migration is needed; actual MySQL optimizer plans remain unmeasured.
- Memory storage builds one Actor set and takes one read lock to scan matching Session rosters. Completed Sessions retain membership until removal/deletion; no status restriction is added.
- Owner mode changes retain the existing management check and add only a targeted Bot ownership check on its denial. This can add one registry lookup and, if `created_by` does not match, one creator-edge lookup. There is no enumeration of all owned Bots and no additional write or transaction.

## Propagation analysis

The affected consumer (影响的 consumer) is the V1 Group detail facade. The new capability flows through `SessionManagementService`, the runtime-cleanup decorator, `SessionRepoPort`, and both production memory/SQL stores. External implementations still compile through an explicit unconfigured-error default, but must implement this capability to serve Session-only Group detail reads. Existing direct Group participation remains a short circuit. The Service API crate advances from 0.2.0 to 0.3.0 for the additive contract. No Plugin API, wire shape, schema, configuration, or management policy changes. Rollback restores the prior authorization rules without a data migration.

## Validation notes

- Both reported positive authorization scenarios failed with the original implementation before the fixes; negative permission cases remained denied.
- Four affected application/service/store crates passed after implementation and again after the mechanical Session facade split. Final expanded validation is recorded below when complete.
- HTTP Group/Session route suites: 65 tests passed.
- OpenAPI: all 72 public operations validated; two exports were byte-identical. The Gateway compatibility gate accepted the generated candidate.
- The existing Gateway snapshot has unrelated pre-existing drift from current source contracts. Its trial regeneration was restored rather than including unrelated schema changes; only BCS source contracts change in this patch.
- The expanded five-crate test run passed 576 tests (including repository/service conformance suites); the separate HTTP suites passed another 65 tests.
- Full architecture validation encountered pre-existing failures, including an unset variable in `check-deps.sh`, existing import/trait/conformance violations, and a non-exhaustive `GrantKind` match in `bcs-edge-permission-store`. The dependency script, enum and failing match files are byte-identical to HEAD. These gates were not weakened and unrelated failures were not modified.
- No deployed request replay, live MySQL execution or full product E2E was performed. SQL behavior is covered by SQLite conformance and recording-DB query/error assertions.
- Independent static review found no correctness or security regressions and confirmed the Session facade extraction preserves unrelated logic. Its informational index concern was checked against all current MySQL migrations: the required index prefix already exists in migrations 004/005, not just the initial schema.
- After the user requested a commit on a new branch from `dev`, the patch was transferred without conflicts onto `origin/dev` at `25e802715`. All five affected crate suites and the 65 HTTP route tests passed again; the updated base has 73 validated OpenAPI operations. The full architecture run and its recorded baseline failures above preceded this transfer; full architecture/product E2E was not repeated on the new branch.
