# Plan — Bot Task Modes (`task_claim_mode` / `task_dream_mode`)

> SDD phase 2 — HOW. Grounded in the BCS layered architecture and the backend
> task-module consumer. Files referenced with `path:line` from the current tree.

## 0. Module split & data flow

Two touch points; BCS is the owner, backend task module is the consumer.

```
Frontend ──PATCH /openapi/v1/collaboration/bots/{bot_id}──▶ BCS ─▶ bcs_bots (new cols)
                                                            │
                                                            └─ PhysicalBot exposes the toggles
Backend task core ── list_bots_by_task_modes ──▶ depends on BCS for bot data (transport TBD)
  (implemented in backend/.../task; NOT in the BCS module; no new BCS OpenAPI read endpoint)
```

- `POST /bots/query` is **not touched**. No new BCS read endpoint is exposed over OpenAPI.
- Storage: two top-level columns on `bcs_bots` (default 0/false), not in `bot_info` JSON.
- Read side lives in the **backend task module core** (depends on BCS), not in the BCS module — the
  BCS-internal `list_by_task_modes` was reverted. Transport for "backend core depends on BCS" is open
  (BcnService is currently write-only); see §6.

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

## 6. Roster read lives in backend/task core (depends on BCS); BCS read reverted

The task-mode roster read is **not** in the BCS module. It belongs in the **backend task module's core
layer**, which depends on BCS for bot data. Per the consumer's correction ("读不实现在 bcs 模块下，而是
实现在 backend 模块下的 task 子模块下，core 层代码要依赖"), BCS only:

- persists the toggles on `bcs_bots` (migration + patch — done);
- exposes them on `PhysicalBot` so existing BCS bot representations carry the fields (done);
- **does not** host a roster read.

The earlier BCS-internal `list_by_task_modes` (repo `list_control_plane_by_task_modes` +
`TaskModeMatch`/`BotTaskModesQuery` types + the SQL/memory impls + the read conformance test) was
**reverted** in this revision. The `persistent_control_plane_task_modes_patch_persists_and_reads_back`
write-side test (patch persists + independent toggle + read-back via `get_control_plane`) remains.

### OpenAPI contract — write side only (`bots.yaml` + `domain-models.yaml`)
- `UpdateBotRequest` (:555): add `task_claim_mode` (boolean), `task_dream_mode` (boolean).
- `domain-models.yaml` `PhysicalBot` (:117): add `task_claim_mode` (boolean, required),
  `task_dream_mode` (boolean, required).
- **No new read path** is added to `bots.yaml`; no endpoint-registration test changes.

### Open transport decision (blocks the backend read)
`BcnService` (backend's only BCS client, `core/bot_management/services/bcn_service.py`) is currently
**write-only** (onboard/register/switch/delete); backend has no existing read path to BCS bots. So
"backend task core depends on BCS" + "no new OpenAPI" + "bcs_bots is BCS-owned" need a concrete
transport, to be confirmed with the consumer/BCS owner:
- (a) reuse an existing BCS bot-read surface that already returns the toggles, filter locally in
  backend task core;
- (b) BCS syncs the toggles into a backend-readable store, backend reads locally;
- (c) another agreed channel.

---

## 7. Backend task-module roster read (pending transport decision)

Once §6's transport is chosen, implement in `backend/src/agentclaw/community/core/task/`:
- a core service/Protocol (e.g. `TaskBotRosterProtocol`) with a `list_bots_by_task_modes(...)` method
  that filters by the toggles (OR/AND), depending on BCS for the bot data per the chosen transport;
- a roster DTO (minimal: `bot_id, name, env, task_claim_mode, task_dream_mode`, optional descriptor);
- DI wiring injecting the BCS dependency into the task core; inject the roster into task discovery so it
  iterates enabled bots.
No backend HTTP route unless an external caller needs it. **Blocked on the transport decision in §6.**

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

---

## 11. Read-side contract (finalized 2026-08-20 — supersedes §6 and the earlier revert)

The earlier plan reverted a BCS-internal roster read and left the backend→BCS transport open
(§6). After alignment with the BCS owner, the read side is now finalized as an **internal
(non-OpenAPI) BCS provider HTTP route**, consumed by the backend task module over the existing
`BcsHttpAdapter`. This section is authoritative for the read side.

### Contract

- **Route**: `GET /providers/{provider_id}/bots/by-task-modes` — registered in
  `adapters/http/bcs-http/src/router.rs`, co-located with the `/providers/{provider_id}/bots`
  group (NOT under `openapi_v1`).
- **Auth**: `Authorization: Bearer <provider_admin_token>`, extracted via `bearer_token(&headers)`
  and validated server-side by `provider_management` (mirrors `list_provider_bots` /
  `switch_bot_delivery`). Missing/wrong token → `401`. NOT X-BCS-Service-Key, NOT X-ECB, NOT OpenAPI.
- **Scope**: provider-scoped — only bots bound to `{provider_id}` are returned (intersect of the
  provider's bot bindings with the task-mode matches).
- **Query params**: `task_claim_mode` / `task_dream_mode` (optional `true|false`; absent/empty =
  do not filter on that toggle); `match` (`any` default | `all`) — whether a bot must match the
  supplied toggles on ANY or ALL.
- **Response**: `{"items":[{"bot_id","name","env","task_claim_mode","task_dream_mode"}, ...]}` — same
  envelope shape as `list_provider_bots`.

### BCS implementation layers (done)

1. `types/bot_control_plane.rs`: `TaskModeMatch { Any, All }` + `BotTaskModesQuery { env,
   task_claim_mode: Option<bool>, task_dream_mode: Option<bool>, match_mode }` (re-added; was
   reverted earlier). Re-exported from `bcs_service_api` root.
2. `port/repo/bot_control_plane.rs`: `list_control_plane_by_task_modes(query)`; implemented by
   `PersistentBotRepo` (SQL Any/All/single/none clause, `env` + `is_deleted=0` +
   `actor_kind='bot'`, ordered) and `MemoryBotRepo` (filter loop).
3. `core/bot_control_plane.rs`: `BotControlPlaneCoreService::list_by_task_modes` (default
   `Ok(vec![])`); `BotControlPlaneCore` overrides to delegate + `hydrate`.
4. `application/provider.rs`: `ProviderManagementService::list_provider_bots_by_task_modes` +
   result type `ProviderBotRosterItem` + input `ProviderBotTaskModesFilter`. `ProviderManagement`
   gains `control_plane: Option<Arc<dyn BotControlPlaneCoreService>>` + `with_control_plane(..)`
   builder; the method validates the admin token via `list_provider_bots`, queries
   `list_by_task_modes(env = resolve_env_str())`, intersects by `bot_uuid`, projects to roster
   items. Composition root (`server.rs`) wires the control-plane core into all 3 provider-service
   builders; noop stub updated.
5. `routes/providers.rs` + `router.rs`: handler `list_provider_bots_by_task_modes` parses query
   params leniently (empty/absent = no filter) and the route registration.
6. Conformance test `provider_routes_contract.rs::list_provider_bots_by_task_modes_filters_and_scopes_to_provider`:
   any/all/single/none filters + provider-scoping (cross-provider bot excluded) + 401 on
   missing/wrong token.

### Backend consumption (see tasks.md §K / §I)

Backend reuses `BcsClientPort` / `BcsHttpAdapter` (async, already injected into task core as
`bcs: BcsClientPort`): new method `list_bots_by_task_modes(provider_id, claim, dream, match)`
sends `Authorization: Bearer {provider_admin_token}` (provider creds on the token provider) and
maps `{items}` to a **local** DTO (no reuse of existing backend domain objects). Task core
scopes task discovery/dispatch to the returned roster.
- Gateway `bcs_bots` mirror changes (not needed; gateway reads 6 columns for token resolution).