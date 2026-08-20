# Tasks — Bot Task Modes (`task_claim_mode` / `task_dream_mode`)

> SDD phase 3 — numbered, testable checklist. Check off as you go.
> Order respects dependencies (migration → types → store → app/core → delivery →
> contract → tests → gates). Items within the same section can
> be parallelized. `path:line` refs from the current tree.

## A. Schema migration (do first)

- [x] **1.** Create `src/bcs/migrations/mysql/009_add_bot_task_modes.sql`:
  `ALTER TABLE bcs_bots ADD COLUMN IF NOT EXISTS task_claim_mode tinyint(4) NOT NULL DEFAULT '0' ...,
  ADD COLUMN IF NOT EXISTS task_dream_mode tinyint(4) NOT NULL DEFAULT '0' ...` (mirror `002_add_owner_bot_id.sql`).
  [verify] file applies idempotently on a `bcs_bots` table (`ADD COLUMN IF NOT EXISTS`).
- [x] **2.** SQLite in `src/bcs/crates/bootstrap/bcs/src/migrations.rs`: add the two columns to the
  `bcs_bots` CREATE TABLE in `SQLITE_DDL_STATEMENTS` (:28-49); append
  `SqliteMigration { version: 9, name: "add_bot_task_modes" }` to `SQLITE_VERSIONED_MIGRATIONS` (:693-726);
  add `ensure_sqlite_bot_task_modes` (PRAGMA table_info + `ALTER TABLE … ADD COLUMN …` idempotent, mirror
  :830-853); wire it in `apply_sqlite_migration_body` (:971-994) for version 9.
  [verify] fresh in-memory SQLite has both columns; re-running migrations is a no-op; checksum unchanged.
- [x] **3.** Migration tests in `migrations.rs` test module (:1175-1496): assert fresh DB has the columns;
  version-9 migration idempotent; legacy-row repair path.
  [verify] `cargo test -p bcs --bootstrap migrations` (or the migration test target) passes.

## B. BCS contract types (`bcs-service-api`)

- [x] **4.** `types/bot_control_plane.rs`: add `pub task_claim_mode: bool, pub task_dream_mode: bool` to
  `BotControlPlaneRecord` (:16); ensure `Default` = `false`. Add `task_claim_mode: Option<bool>,
  task_dream_mode: Option<bool>` to `BotControlPlanePatch` (:71) and update its `is_empty()`.
  [verify] crate compiles; `Default::default()` record has both `false`; empty patch `is_empty()` true.
- [x] **5.** `application/v1/bot.rs`: add `task_claim_mode: Option<bool>, task_dream_mode: Option<bool>`
  to `BotPatch` (:207) + update `is_empty()` (:215); add `pub task_claim_mode: bool, pub task_dream_mode: bool`
  (required) to `PhysicalBot` (:67). Leave `HumanBot` unchanged.
  [verify] compiles; `BotPatch::default().is_empty()` true; `PhysicalBot` serializes with both fields.

## C. BCS application impl (`bcs-app-bot`)

- [x] **6.** `bcs-app-bot/src/lib.rs` `update` (:375): carry `patch.task_claim_mode`/`task_dream_mode` into
  the `BotControlPlanePatch` built there; enforce bot-only — if `record.kind == ActorKind::Human` and either
  toggle is `Some` → `ApplicationError::invalid("invalid_bot_kind", ...)` (mirror descriptor-human rejection ~:408).
  [verify] unit test: toggles flow into the core patch; Human-row toggle PATCH → `invalid_bot_kind`.
- [x] **7.** `project_records` (:154 Phys.fieldBot construction): set
  `task_claim_mode: record.task_claim_mode, task_dream_mode: record.task_dream_mode`.
  [verify] a Physical bot read after patch echoes the toggles; Human bot output has no such fields.

## D. BCS store / SQL (`bcs-bot-store`)

- [x] **8.** `lib.rs` SELECTs: add `task_claim_mode, task_dream_mode` to the control-plane column lists in
  `try_load_from_db` (:450-577) and `list_bots_by_creator_from_db` (:764) (+ any other `BotControlPlaneRecord`
  SELECT). Read defensively: `db_get_column_opt(row, "task_claim_mode")…map(|v| v != 0).unwrap_or(false)`
  and same for dream.
  [verify] store round-trips the toggles on read; a NULL/legacy row reads `false`.
- [x] **9.** `lib.rs` `patch_control_plane` (:2805-2931): when `patch.task_claim_mode` is `Some(v)` push
  `"task_claim_mode = ?"` + `Value::from(if v {1} else {0})`; same for dream (same branch as
  `visibility`/`status`, **not** the `bot_info` merge branch).
  [verify] patching one toggle leaves the other and `bot_info` untouched.
- [x] **10.** `lib.rs` INSERTs (:414 onboard, :1900 `ensure_human_actor`): leave the two columns **out** so
  DB `DEFAULT 0` applies. [verify] new bots persist with both toggles `false` without listing the columns.
- [-] **11.** ~~`types/bot_control_plane.rs`: add `TaskModeMatch` + `BotTaskModesQuery`; repo port
  `list_control_plane_by_task_modes`.~~ **Reverted** — the roster read does not belong in the BCS module
  (see §E/§I). BCS keeps only the write-side types from task 4.
- [-] **12.** ~~`PersistentBotRepo::list_control_plane_by_task_modes` SQL impl.~~ **Reverted** (read moved
  to backend/task).
- [-] **13.** ~~`memory.rs` in-memory `list_control_plane_by_task_modes`.~~ **Reverted** (read moved to
  backend/task).

## E. BCS core read — reverted (read lives in backend/task, not BCS)

- [-] **14.** ~~Core trait `list_by_task_modes` + core override.~~ **Reverted** — BCS hosts no roster read.
  The read is implemented in the backend task module core (depends on BCS); see §I.
- [~] **15.** **No BCS application/command layer** for the read — confirmed NA (read is not in BCS at all).

## F. BCS delivery adapter (`bcs-api-http`) — write side only

- [x] **16.** `dto/bot.rs`: add `#[serde(default)] task_claim_mode: Option<bool>, task_dream_mode: Option<bool>`
  to `UpdateBotRequest` (:82); map both in `From<UpdateBotRequest> for BotPatch` (:93).
  [verify] PATCH body with toggles deserializes; unknown fields still rejected (`denyunknown_fields`).
- [~] **17.** **No read route.** `routes/bot.rs` gains no `/bots/by-task-modes` handler — the roster read
  is in backend/task, not a BCS HTTP endpoint. (done-as-NA)

## G. OpenAPI contract — write side only

- [x] **18.** `api-contracts/v1/openapi/bots.yaml`: add `task_claim_mode`/`task_dream_mode` (boolean) to
  `UpdateBotRequest` (:555). **No** `BotsByTaskModesPath` GET operation (no read endpoint).
- [x] **19.** `api-contracts/v1/domain-models.yaml`: add `task_claim_mode`/`task_dream_mode` (boolean,
  required) to `PhysicalBot` (:117).
  [verify] `bots.yaml` + `domain-models.yaml` validate; PhysicalBot now requires both booleans; no new
  path registered.

## H. BCS tests

- [~] **20.** Route tests `tests/bot_routes.rs`: the existing contract-input test already carries the new
  `UpdateBotRequest` fields through compilation; Human-row toggle PATCH → `invalid_bot_kind` is covered at
  the app layer (task 6). A dedicated route-level toggle-echo assertion is a nice-to-have follow-up, not
  required for correctness.
- [x] **21.** Store conformance `tests/conformance_bot_control_plane_repo.rs`: test `CREATE TABLE bcs_bots`
  fixtures include both columns; added `persistent_control_plane_task_modes_patch_persists_and_reads_back`
  (default-false read, independent toggle patch, untouched-other + bot_info intact, read-back via
  `get_control_plane`). No `list_control_plane_by_task_modes` test (read reverted).
  [verify] `cargo test -p bcs-bot-store --test conformance_bot_control_plane_repo` passes (7 tests).
- [~] **22.** Application/core contract: patch-carries-toggles + projection are exercised by existing
  app-bot tests (task 6) + the conformance write-persistence test (task 21). No BCS core read to test.
- [x] **23.** OpenAPI conformance `src/bcs/tests/openapi/test_bot_v1_contract.py` + `test_contract.py`:
  assertions updated to require the new fields in `UpdateBotRequest` + `PhysicalBot` (required + physical-only).
  No new path to assert; no gateway forwarding-test changes.
  [verify] `pytest tests/openapi/test_bot_v1_contract.py test_internal_contract.py test_contract.py` passes.
- [~] **24.** BCS build + gates: `cargo test -p bcs-bot-store -p bcs-app-bot -p bcs-api-http -p bcs-bot`
  passes. No new HTTP endpoint, so the singlebox 100%-HTTP-endpoint coverage gate is unaffected; still
  cover the toggle PATCH in an E2E story if the singlebox suite exercises PATCH /bots/{id}.

## I. Backend task-module roster read (transport decision blocks implementation)

- [ ] **25.** **Decide the transport** for "backend task core depends on BCS" given: `BcnService`
  (`core/bot_management/services/bcn_service.py`) is currently **write-only** (onboard/register/switch/
  delete); backend has no existing read path to BCS bots; `bcs_bots` is BCS-owned; no new BCS OpenAPI read
  endpoint. Options: (a) reuse an existing BCS bot-read surface that returns the toggles + filter locally
  in backend task core; (b) BCS syncs toggles into a backend-readable store + backend reads locally;
  (c) another agreed channel. Block on the consumer/BCS owner.
- [ ] **26.** Backend task core: add a roster Protocol/service (e.g. `TaskBotRosterProtocol`) with
  `list_bots_by_task_modes(claim, dream, match)`, depending on BCS per task 25's transport; roster DTO
  (`bot_id, name, env, task_claim_mode, task_dream_mode`, optional descriptor).
- [ ] **27.** DI wiring: inject the BCS dependency into the task core; inject the roster into task
  discovery so it iterates enabled bots. No backend HTTP route unless an external caller needs it.
- [~] **28–29.** (reserved) backend HTTP route for the roster — deferred unless an external caller needs it.

## J. Gates & docs

- [ ] **30.** Pre-push: `src/bcs` → BCS unit tests + singlebox E2E; no `src/backend` changes in this plan.
  State exactly what could not be run and why.
- [ ] **31.** Docs: bot API docs reflect the new PATCH fields; `api-frontend-patch.md` covers the frontend
  PATCH contract. No new read endpoint to document; no served-path registries changed.
- [ ] **32.** Final review pass: confirm `deny_unknown_fields`/`minProperties:1` still hold on
  `UpdateBotRequest`; confirm no `bot_info` JSON changes leaked in; confirm DB write failures propagate
  (no silent success); confirm Human rows never expose/accept the toggles; confirm no read OpenAPI path
  leaked into `bots.yaml`.