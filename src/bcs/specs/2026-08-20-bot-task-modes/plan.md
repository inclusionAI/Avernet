# Plan — Bot Task Modes (`task_claim_mode` / `task_dream_mode`)

> SDD phase 2 — HOW. Grounded in the BCS layered architecture and the backend
> task-module consumer. Files referenced with `path:line` from the current tree.

## 0. Module split & data flow

Two touch points; BCS is the owner, backend task module is the consumer.

```
Frontend ──PATCH /openapi/v1/collaboration/bots/{bot_id}──▶ BCS ─▶ bcs_bots (new cols)

BCS core service (internal) ── list_by_task_modes ──▶ bcs_bots (read by toggles)
  (repo + core only; NOT exposed over OpenAPI, NOT a backend client)
```

- `POST /bots/query` is **not touched**. No new BCS read endpoint is exposed over OpenAPI.
- Storage: two top-level columns on `bcs_bots` (default 0/false), not in `bot_info` JSON.
- Read side is a BCS **internal core-service capability**
  (`BotControlPlaneCoreService::list_by_task_modes` +
  `BotControlPlaneRepoPort::list_control_plane_by_task_modes`) only — no HTTP route, no OpenAPI read
  contract, no backend Python client. Consumer-boundary assumption to confirm: see §6.

Layering to mirror (BCS): `routes/bot.rs` (DTO) → `application/v1/bot.rs` →
`bcs-app-bot/src/lib.rs` → `core/bot_control_plane.rs` + `types/bot_control_plane.rs`
→ `port/repo/bot_control_plane.rs` → `bcs-bot-store/src/lib.rs` (SQL). Plus the
in-memory impl `bcs-bot-store/src/memory.rs` for tests.

---

## 1. Schema migration

### MySQL
New file `src/bcs/migrations/mysql/009_add_bot_task_modes.sql` (mirror `002_add_owner_bot_id.sql`):
```sql
ALTER TABLE bcs_bots
    ADD COLUMN IF NOT EXISTS task_claim_mode tinyint(4) NOT NULL DEFAULT '0' COMMENT 'task claim opt-in flag',
    ADD COLUMN IF NOT EXISTS task_dream_mode tinyint(4) NOT NULL DEFAULT '0' COMMENT 'task dream opt-in flag';
```

### SQLite (`src/bcs/crates/bootstrap/bcs/src/migrations.rs`)
- Add to the `bcs_bots` CREATE TABLE in `SQLITE_DDL_STATEMENTS` (:28-49):
  `task_claim_mode INTEGER NOT NULL DEFAULT 0, task_dream_mode INTEGER NOT NULL DEFAULT 0`.
- Append `SqliteMigration { version: 9, name: "add_bot_task_modes" }` to `SQLITE_VERSIONED_MIGRATIONS` (:693-726).
- Add `ensure_sqlite_bot_task_modes` helper (mirror `ensure_sqlite_message_owner_bot_id` :830-853): `PRAGMA table_info(bcs_bots)` → if absent, `ALTER TABLE bcs_bots ADD COLUMN task_claim_mode INTEGER NOT NULL DEFAULT 0;` and same for `task_dream_mode`. Wire it into `apply_sqlite_migration_body` (:971-994) for version 9.
- Migration tests (:1175-1496): fresh DB has both columns; versioned migration is idempotent; checksum unchanged.

### Rollout ordering
Code that `SELECT`s the new columns requires the columns to exist. Deploy order:
**migrate DB first (or in the same release), then BCS code.** The store still reads
defensively (`db_get_column_opt(...).unwrap_or(false)`) so NULL/edge rows collapse to
false; this does **not** protect an unmigrated DB (SELECT would error), hence the ordering.

---

## 2. BCS contract types (`bcs-service-api`)

`src/bcs/crates/service-api/bcs-service-api/src/types/bot_control_plane.rs`:
- `BotControlPlaneRecord` (:16): add `pub task_claim_mode: bool, pub task_dream_mode: bool`.
  Ensure `Default` yields `false` (the struct likely already derives `Default`; verify).
- `BotControlPlanePatch` (:71): add `pub task_claim_mode: Option<bool>, pub task_dream_mode: Option<bool>`;
  update its `is_empty()` to include both.

`BotControlPlaneView` (`core/bot_control_plane.rs:17`) wraps `{ record, provider }` — no new fields.

---

## 3. BCS application layer

`src/bcs/crates/service-api/bcs-service-api/src/application/v1/bot.rs`:
- `BotPatch` (:207): add `task_claim_mode: Option<bool>, task_dream_mode: Option<bool>`;
  update `is_empty()` (:215).
- `PhysicalBot` (:67): add `pub task_claim_mode: bool, pub task_dream_mode: bool` (required, always present).
- `HumanBot` (:87): unchanged (no toggles).
- `UpdateBot`/`QueryBots`: unchanged.

`src/bcs/crates/application/v1/bcs-app-bot/src/lib.rs`:
- `BotServiceImpl::update` (:375): carry `patch.task_claim_mode` / `patch.task_dream_mode` into the
  constructed `BotControlPlanePatch`. Enforce bot-only: if `record.kind == ActorKind::Human` and either
  toggle is `Some` → `ApplicationError::invalid("invalid_bot_kind", ...)` (mirror the descriptor-human
  rejection ~:408). (Setting toggles on a Human row is rejected.)
- `project_records` (:154 PhysicalBot construction): set
  `task_claim_mode: record.task_claim_mode, task_dream_mode: record.task_dream_mode`.

---

## 4. BCS delivery adapter (`bcs-api-http`)

`src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/bot.rs`:
- `UpdateBotRequest` (:82): add `#[serde(default)] pub task_claim_mode: Option<bool>, pub task_dream_mode: Option<bool>`.
- `From<UpdateBotRequest> for BotPatch` (:93): map both through.

`routes/bot.rs`:
- `update_bot` handler (:109) unchanged (already forwards `body.into()`).
- **No new read route.** The task-mode roster is an internal core-service read (see §6), not an HTTP endpoint.

---

## 5. BCS store / SQL (`bcs-bot-store/src/lib.rs`, `PersistentBotRepo`)

- **SELECTs**: add `task_claim_mode, task_dream_mode` to the control-plane column lists in
  `try_load_from_db` (:450-577) and `list_bots_by_creator_from_db` (:764) (and any other SELECT that
  hydrates `BotControlPlaneRecord`). Read defensively:
  `db_get_column_opt(row, "task_claim_mode").ok().flatten().map(|v| v != 0).unwrap_or(false)`
  (and same for dream).
- **patch_control_plane** (:2805-2931): when `patch.task_claim_mode` is `Some(v)` push
  `"task_claim_mode = ?"` + `Value::from(if v {1} else {0})`; same for dream. (Same branch as
  `visibility`/`status`, not the `bot_info` JSON-merge branch.)
- **INSERTs** (:414 onboard, :1900 `ensure_human_actor`): **omit** the two columns so DB `DEFAULT 0`
  applies (keeps INSERTs valid on not-yet-migrated DBs and avoids churn).
- **New repo method** `list_control_plane_by_task_modes(query: BotTaskModesQuery) -> Vec<BotControlPlaneRecord>`
  (`BotTaskModesQuery { env, task_claim_mode: Option<bool>, task_dream_mode: Option<bool>, match_mode: TaskModeMatch }`,
  `TaskModeMatch::{Any,All}`). Unpaginated roster read — returns the full matching set (the consumer
  iterates it internally); no `COUNT(*)`/total.
  ```sql
  SELECT <cols incl task_claim_mode, task_dream_mode> FROM bcs_bots
  WHERE env = ? AND COALESCE(is_deleted,0) = 0 AND COALESCE(actor_kind,'bot') = 'bot'
    [AND task_claim_mode = ?                -- only claim provided
     AND task_dream_mode = ?               -- only dream provided
     AND (task_claim_mode = ? OR task_dream_mode = ?)   -- match=Any, both provided
     AND task_claim_mode = ? AND task_dream_mode = ?]   -- match=All, both provided
  ORDER BY gmt_create DESC, bot_uuid ASC
  ```
  Build the mode clause conditionally; if neither filter provided, return all physical bots in env.
- Add `BotTaskModesQuery` + `TaskModeMatch` to `types/bot_control_plane.rs` and the crate re-exports
  (`bcs-service-api/src/lib.rs`); add the method to the repo port trait
  (`port/repo/bot_control_plane.rs`), the core trait (`core/bot_control_plane.rs` — with a default
  `Ok(Vec::new())` so stubs like `AuthorizationProbeCore` keep compiling), the core impl
  (`bcs-bot/src/core/bot_control_plane_core.rs` — delegate + `hydrate`), and the in-memory impl
  (`memory.rs`) for contract tests.

---

## 6. BCS internal read capability (core + repo only — not exposed)

The task-mode roster is a BCS-internal read, **not** an HTTP/OpenAPI endpoint and **not** a backend
client. Per the consumer's decision ("读侧不暴露openapi，只是task内部实现用" / "只是core service调用"),
only the repo port + core service gain a read method.

- `BotControlPlaneRepoPort::list_control_plane_by_task_modes(query: BotTaskModesQuery)
  -> ServiceResult<Vec<BotControlPlaneRecord>>` (SQL in `PersistentBotRepo`, filter loop in
  `MemoryBotRepo`).
- `BotControlPlaneCoreService::list_by_task_modes(query: BotTaskModesQuery)
  -> ServiceResult<Vec<BotControlPlaneView>>` — declared with a **default** `Ok(Vec::new())` on the
  trait (so test stubs keep compiling) and overridden in `BotControlPlaneCore` to delegate to the repo
  port and `hydrate` providers.
- No `application/v1/bot.rs` command, no `bcs-api-http` route/DTO, no OpenAPI read path, no backend
  Python client. The consumer calls the BCS core service directly (in-process, BCS-internal).

### OpenAPI contract — write side only (`bots.yaml` + `domain-models.yaml`)
- `UpdateBotRequest` (:555): add `task_claim_mode` (boolean), `task_dream_mode` (boolean).
- `domain-models.yaml` `PhysicalBot` (:117): add `task_claim_mode` (boolean, required),
  `task_dream_mode` (boolean, required).
- **No new read path** is added to `bots.yaml`; no endpoint-registration test changes for a read path.

### Consumer-boundary confirmation (open)
The toggles live on `bcs_bots` (BCS-owned). The task consumer is BCS-internal per the latest decision,
so the read stays at the core layer. **If** the consumer is actually the backend Python task module
(which has no `bcs_bots` access and is a separate process), an in-process core call is not reachable and
a transport must be chosen — that would reintroduce an OpenAPI/IPC surface and conflict with "no
OpenAPI". Confirm with the consumer owner before closeout. This plan implements exactly what the user
asked for (repo + core, no OpenAPI); the confirmation only governs whether a follow-up transport task is
needed.

---

## 7. No backend task-module client

Dropped per the read-side revision. No `bcs_task_mode_client.py`, no `TaskBotRosterProtocol`, no DI
wiring, no backend HTTP route. If §6's consumer-boundary confirmation lands on "backend needs the
roster", this section is re-opened as a separate task with its own transport decision (and would require
re-introducing an OpenAPI/IPC read surface, explicitly outside this plan's scope).

---

## 8. Tests

### BCS
- Migration (`migrations.rs` tests): columns present on fresh DB; versioned migration idempotent.
- Store conformance (`tests/conformance_bot_control_plane_repo.rs`): update the test `CREATE TABLE bcs_bots`
  fixtures to include the two columns; add cases — patch sets each toggle independently; patch leaves the
  other untouched; PATCH/setting toggles on a Human row rejected (`invalid_bot_kind`);
  `list_control_plane_by_task_modes` returns correct sets for `any`/`all`/single-filter/none; defensive
  read of legacy NULL → false.
- Route tests (`tests/bot_routes.rs`): PATCH with toggles recorded in the `UpdateBot` command and echoed
  in the response `PhysicalBot`; Human-row toggle PATCH → 400 `invalid_bot_kind`. (No
  `GET /bots/by-task-modes` route — read is internal.)
- Application/core contract (`bot_use_cases_contract.rs`, `bcs-bot/tests/bot_use_cases.rs`): patch
  carries toggles; projection includes them; `list_bots_by_task_modes` OR/ALL semantics.
- OpenAPI contract (`src/bcs/tests/openapi/test_bot_v1_contract.py`, `test_contract.py`): assert new
  fields in `UpdateBotRequest` + `PhysicalBot` (required for Physical). No new read path to assert; no
  gateway forwarding-test changes.

### Backend
- None — no backend client in this plan (see §6/§7).

### Backend
- Unit test the `bcs_task_mode_client` (httpx mock / `respx`): parses `BotPageEnvelope`, maps DTOs,
  handles error envelope + timeout.
- Unit test the roster service: filter-param mapping (`any`/`all`) and delegation.

---

## 9. Rollout, compatibility, risks

- **PATCH contract change is additive** (new optional fields) — existing callers unaffected. No new
  endpoint is exposed (read is internal). Versioned + conformance-tested (arch rule).
- **Migration ordering**: DB migrated before BCS code is live (§1).
- **Gateway**: no new path served (read is internal); no whitelist change needed.
- **Backend → BCS auth**: N/A — no backend BCS call in this plan (see §6 open confirmation).
- **DB write failures propagate** as errors (OCB rule); PATCH must not silently succeed.
- **Style**: BCS `CLAUDE.md` — no `cargo fmt`, minimal edits, UTF-8-safe slicing (not relevant here).
- **Pre-push gates**: `src/bcs/` → BCS unit tests + singlebox E2E (40% line / 36% method coverage +
  100% endpoint coverage); the PATCH extension touches an existing endpoint (no new route), so the
  endpoint-coverage gate is unaffected — still cover the toggle PATCH in a story. `src/backend/` → no
  changes in this plan.

## 10. Deferred / out of scope
- Task claim/dream execution, scheduling, dispatch logic.
- Frontend implementation.
- A backend HTTP route exposing the roster (only if an external caller needs it).
- Gateway `bcs_bots` mirror changes (not needed; gateway reads 6 columns for token resolution).