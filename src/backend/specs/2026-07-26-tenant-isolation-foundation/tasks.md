# Tasks: Tenant Isolation Foundation (Stage 1)

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

## Task 1: Spike — confirm listener covers ORM writes  `[x]`
- **Goal:** Empirically settle whether a `do_orm_execute` listener applying
  `with_loader_criteria(BotModel, ...)` also constrains `Query.update()` /
  `Query.delete()`, or whether the write methods need an explicit filter.
- **Files:** throwaway probe (scratchpad, not committed) — no production change.
- **Finding (SQLAlchemy 2.0.51): COVERED.** A prototype listener adding
  `with_loader_criteria(Bot, Bot.tenant == current, include_aliases=True)` in
  `do_orm_execute` constrains the exact write shape `bot_repository` uses —
  `db.query(Model).filter(...).update(values, synchronize_session=False)` — and
  `Query.delete()`. Probe results: a cross-tenant `Query.update()` returned
  rowcount 0 and left the row unchanged; a cross-tenant `Query.delete()`
  returned rowcount 0 and left the row present; a cross-tenant read returned
  nothing. **Task 5 needs no explicit `_avernet_tenant()` filter on the write
  methods — the single read listener covers reads, updates, and deletes.**
- **Done when:**
  - [x] Minimal reproduction run against different-tenant rows with a prototype
        listener installed.
  - [x] Finding recorded: **covered** — read path alone suffices for writes too.
- **Depends on:** —

## Task 2: Tenant context primitive  `[x]`
- **Goal:** Add the request-lifetime tenant `ContextVar` and its helpers.
- **Files:** `src/agentclaw/community/utils/avernet_tenant.py` (new),
  `tests/community/utils/test_avernet_tenant.py` (new).
- **Done when:**
  - [x] `DEFAULT_AVERNET_TENANT = "teamclaw"`, `get_current_avernet_tenant() -> str`
        (total, never `None`), `avernet_tenant_scope(tenant_id)` context manager,
        and `bind_current_avernet_tenant(fn)` are implemented.
  - [x] Module docstring names the unrelated poolab/baas `tenant` concept so a
        future reader does not conflate them.
  - [x] Tests: default outside a request; set/reset; nesting; reset still runs
        when the scoped body raises; a thread wrapped by
        `bind_current_avernet_tenant` observes the spawning tenant (and a bare
        thread does not). 7 passed.
- **Depends on:** —

## Task 3: `avernet_tenant` column on `BotModel`  `[x]`
- **Goal:** Give bot records the tenant axis, invisible in API responses. The
  column carries only a `server_default` for backfill; context-aware stamping is
  the `before_insert` guard's job (Task 5).
- **Files:** `src/agentclaw/community/plugin_api/models.py`,
  `tests/community/plugin_api/test_models.py`.
- **Done when:**
  - [x] `BotModel` gains `avernet_tenant = Column(String(64), nullable=False,
        server_default="teamclaw")` after `caller_config_revision` (matches the
        prod DDL `DEFAULT 'teamclaw'`, so `create_all` and the backfill agree).
  - [x] `avernet_tenant` is **not** added to `BotModel.to_dict()`.
  - [x] Test asserts `to_dict()`'s key set is unchanged (pins the full 26-key
        set; a seeded row carries `avernet_tenant == "teamclaw"` via the
        server_default). 5 model tests pass; existing 32 repo tests still green.
- **Depends on:** Task 2

## Task 4: Cross-tenant isolation test (red)  `[x]`
- **Goal:** Write the spec-required test that fails before the guards exist and
  passes after — and record its red run.
- **Files:** `tests/community/plugins/test_bot_tenant_isolation.py` (new).
- **Red run (recorded):** at this commit (column present, guards absent) —
  `6 failed, 1 passed`. The 6 cross-tenant reads (`get_by_id`,
  `get_by_id_and_owner`, `list_by_owner`, `count_by_owner`, `exists_by_bot_name`,
  `search_bots`) fail because reads are unfiltered so tenant B sees tenant A's
  bot; `test_own_tenant_still_visible` passes trivially (no filter yet). Task 5
  turns the 6 green.
- **Done when:**
  - [x] Test seeds a bot inside `avernet_tenant_scope("tenant-a")` and another
        inside `avernet_tenant_scope("tenant-b")` via `BotRepository.insert`, then
        asserts a read inside `tenant-b` does not return A's bot across all six
        read methods.
  - [x] Run at this task's commit the test **fails** (6 failed) and the red run
        is recorded above.
- **Depends on:** Task 2, Task 3

## Task 5: Tenant guards — read filter + insert stamp (green)  `[x]`
- **Goal:** Install both active guards that scope `BotModel` to the current
  tenant; turn Task 4 green. Reads/updates/deletes are filtered; inserts are
  stamped and validated.
- **Files:** `src/agentclaw/community/plugin_api/models.py`,
  `tests/community/plugins/test_bot_tenant_guard.py` (new).
- **Done when:**
  - [x] **Read guard:** `event.listen(Session, "do_orm_execute", ...)` at model
        import; applies `with_loader_criteria(BotModel, avernet_tenant ==
        get_current_avernet_tenant(), include_aliases=True)`; skips column /
        relationship loads (already-authorized reloads) and statements carrying
        `{"skip_avernet_tenant_guard": True}`.
  - [x] **Insert guard:** `event.listen(BotModel, "before_insert", ...)`; stamps
        `avernet_tenant` when unset, raises `CrossTenantInsertError` on an
        explicit conflicting tenant. Covers every insert path.
  - [x] Both registrations idempotent on `_AVERNET_TENANT_GUARDS_INSTALLED`.
  - [x] Per the Task 1 finding, **no** explicit write-method filter added — the
        read listener covers `Query.update()` / `Query.delete()`.
  - [x] Task 4's isolation test passes (7). Added guard tests (7): cross-tenant
        `update_by_owner` / `soft_delete_by_owner` are no-ops and leave the row
        untouched; a bare `session.query(BotModel).all()` is filtered; insert
        under a scope stamps that tenant (default outside a request); an explicit
        conflicting-tenant insert raises `CrossTenantInsertError`; the skip option
        sees all tenants. **1212 passed** across plugins / plugin_api /
        bot_dormant / bot_chat / cleanup — the global guard regresses nothing.
- **Depends on:** Task 1, Task 3, Task 4

## Task 6: Public-API tenant source (`resolve_avernet_tenant`)  `[x]`
- **Goal:** Add the single replaceable seam for the public API's tenant.
- **Files:** `src/agentclaw/community/adapters/http/openapi_v1/dependencies.py`.
- **Done when:**
  - [x] Plain function `resolve_avernet_tenant(request) -> str` returns
        `DEFAULT_AVERNET_TENANT`, beside `require_principal`, same stub pattern,
        docstring marking it the drop-in point for the real verifier.
  - [x] No Protocol, no DI binding, no `app.py` / `container.py` change.
- **Depends on:** Task 2

## Task 7: `AvernetTenantMiddleware`  `[x]`
- **Goal:** Establish each request's tenant for its whole lifetime, reset on the
  way out including on error.
- **Files:** `src/agentclaw/community/adapters/http/middleware.py`,
  `tests/community/adapters/http/test_avernet_tenant_middleware.py` (new).
- **Done when:**
  - [x] `AvernetTenantMiddleware` (a **pure ASGI** middleware, not
        `BaseHTTPMiddleware`, for `ContextVar`-propagation robustness) picks
        `resolve_avernet_tenant(request)` for `/openapi/v1/*` paths, else
        `DEFAULT_AVERNET_TENANT`; enters `avernet_tenant_scope`; awaits the
        downstream app.
  - [x] Added in `install_middleware` immediately after `UserContextMiddleware`
        so it is outside it (auth plugin DB reads run under the tenant); no new
        `install_middleware` parameter.
  - [x] Integration test (4): every path defaults to `teamclaw`; a public
        request uses the resolved tenant; the tenant does not leak between
        requests, nor after a request raises 500. Existing middleware-stack
        ordering tests (11) still pass.
- **Depends on:** Task 2, Task 6

## Task 8: Request-spawned work inherits the tenant  `[x]`
- **Goal:** In-request background threads observe the request's tenant.
- **Files:** `core/bot_management/services/bot_service.py` (3 sites),
  `core/service_bot/services/bot_publish_service.py`,
  `core/bot_collaborator/services/collaborator_service.py`,
  `tests/community/core/test_request_spawned_tenant_inheritance.py` (new).
- **Done when:**
  - [x] The five `threading.Thread` targets are wrapped with
        `bind_current_avernet_tenant` (do_allocate, _update_cron_workflow,
        _refresh_codefuse_token_on_device, _do_restart, collaborator _runner).
  - [x] `asyncio.create_task` sites left unchanged — task creation copies the
        context (noted in the plan; no code change).
  - [x] Test (4): the three spawn shapes I touched (target-only, target+kwargs,
        run-and-join) inherit the tenant; kwargs still pass; outside a request
        the default is captured. 1487 tests pass across the three touched
        modules — no regression.
- **Depends on:** Task 2

## Task 9: Reference DDL artifact — decision  `[x]`
- **Goal:** Resolve the spec-vs-convention tension the plan flagged.
- **Decision (user, 2026-07-26):** **No DDL file is checked in.** The schema
  change is always applied out-of-band on the platform; the `ALTER TABLE` in
  `plan.md` remains the authoritative record. No file to add — this task is
  closed with no production change.
- **Done when:**
  - [x] Decision recorded; no `.sql` committed.
- **Depends on:** —

## Task 10: Verification against spec acceptance criteria  `[x]`
- **Goal:** Prove every spec acceptance criterion holds.
- **Files:** README context-boundary declarations only (arch-guard follow-up);
  no runtime change.
- **Spec acceptance criteria → evidence:**
  - [x] Every request carries a tenant; default when none — `AvernetTenantMiddleware`
        + `DEFAULT_AVERNET_TENANT` (test_avernet_tenant_middleware).
  - [x] A bot created during a request belongs to that request's tenant —
        `before_insert` guard (test_bot_tenant_guard::insert stamp).
  - [x] Reads (fetch/list/count/name-existence/search) tenant-scoped — read guard
        across all six methods (test_bot_tenant_isolation).
  - [x] Updates/deletes tenant-scoped; cross-tenant = as if missing — read guard
        covers `Query.update()/delete()` per Task 1 spike (test_bot_tenant_guard).
  - [x] Pre-existing rows = default tenant; internal responses unchanged —
        `server_default="teamclaw"` backfill; `to_dict()` key set pinned (Task 3).
  - [x] Existing internal API suite passes **unmodified** — full run **8998
        passed, 3 skipped**; no existing test file's logic changed (only new
        tests added and two README boundary declarations).
  - [x] Tenant never leaks across requests, incl. after error — scope reset in
        `finally` (middleware tests + 200-way concurrency probe, 0 mismatches).
  - [x] Tenant readable by request-handling code — `get_current_avernet_tenant()`.
  - [x] Work started during a request inherits the tenant — `bind_current_avernet_tenant`
        on the five thread sites (Task 8).
  - [x] Public-API tenant source is a single replaceable seam — `resolve_avernet_tenant`.
  - [x] Isolation demonstrated by a test red without / green with — Task 4 (6
        failed) → Task 5 (green).
- **Gates:** Backend unit suite green locally (8998). Changed-line coverage and
  singlebox coverage run on push (pre-push hook) and in PR CI; singlebox needs a
  product stack not available in this sandbox, so it is validated by remote CI.
- **Depends on:** Tasks 1–8 (and Task 9's decision)

---

## Groups

- **Group A — Mechanism groundwork:** Tasks 1, 2
  - Theme: confirm the write-path approach and land the tenant `ContextVar`
    primitive; pure utility + investigation, no bot-data behavior change yet.
- **Group B — ORM enforcement:** Tasks 3, 4, 5
  - Theme: bot records carry a tenant, every read/update/delete is filtered and
    every insert is stamped+validated at the ORM layer (two active guards); the
    spec's red→green cross-tenant isolation test passes.
- **Group C — Request wiring & inheritance:** Tasks 6, 7, 8
  - Theme: each request establishes its tenant (reset even on error),
    request-spawned work inherits it, and the public-API seam exists.
- **Group D — Finalize & verify:** Tasks 9, 10
  - Theme: resolve the checked-in-DDL decision and prove all spec acceptance
    criteria; green gates.

---

## Follow-ups (post-review)

### F1: Extend request-spawned tenant inheritance beyond the first 5 sites  `[x]`
- **Trigger:** review question — was the Task 8 enumeration comprehensive?
- **Audit:** `asyncio.to_thread` and `asyncio.create_task` **copy the current
  context**, so every such offload (the large majority in the codebase) inherits
  the tenant automatically — no wrapping needed. Only raw `threading.Thread` and
  bare `ThreadPoolExecutor`/`run_in_executor` don't.
- **Wrapped now** (in-request threads whose body touches `BotModel`):
  - `bot_dormant/activate_service.py` `_reactivate_async` (reactivate flow).
  - `bot_management/services/bot_service.py` `create_bot_instances` — the
    device-allocation `ThreadPoolExecutor` (bind the submitted callable).
  - `desktop_bot/services/desktop_bot_service.py` `_poll_publish_progress`
    (calls `_bot_repo.get_by_id_and_owner` / `update_by_owner`).
- **CORRECTION (post-merge review, FreddieSun on PR #456):** the "deferred —
  non-bot tables" classification below was **wrong**. Re-traced, these sites DO
  read/write `BotModel` on bare `Thread`/`ThreadPoolExecutor`, so they were fixed
  in the follow-up PR (F3):
  - `bot_public_service.py:176` `_do_sync` → `get_by_id_and_owner`; `:1119`
    `ThreadPoolExecutor` → `get_by_id_and_owner`.
  - `device_service.py:729` `start_service_async` → `_mark_service_start_failed`
    → `_bot_query.get_by_binding_id`; `:1545` `report_device_alive` `_run` →
    `_bot_query.get_by_binding_id`.
  - `baas_publish_poller.py:55` `_poll` → updates `BotModel` on publish completion.
- **Recommended (not done):** an arch guard that flags new raw
  `threading.Thread` / `ThreadPoolExecutor` in core so future in-request spawns
  can't silently drop the tenant. Tracked for follow-up.
- **Verification:** 1005 tests pass across bot_dormant / desktop_bot /
  bot_management (original 3 sites); the 5 corrected sites are covered in F3.

### F3: Fix post-merge review findings (raw SQL + thread mis-classification)  `[x]`
- **Trigger:** two P1 review comments on merged PR #456 (FreddieSun).
- **Raw SQL bypass:** `bot_discover_service._batch_get_public_bots` read `ac_bots`
  via a raw `cursor.execute` — never triggers `do_orm_execute`, so unguarded.
  Migrated to `BotRepository.list_public_bots_by_owner_bot_pairs` (ORM →
  guard-covered). Regression test proves it is tenant-scoped.
- **Threads:** wrapped the 5 mis-classified sites above with
  `bind_current_avernet_tenant`. Regression test proves a repo read inside a
  bound thread stays tenant-scoped (and a bare thread drops to the default).
- **Threads (2nd pass, Codex review note on #478):** two more in-request bare
  threads that touch `BotModel` were wrapped for completeness —
  `device_service.py` `_trigger_data_init_on_device_ready` `_run_init` (data-init
  → `trigger_init` updates `bot.ext.data_init_status`) and its local variant
  `local_device_service.py` `start_service_async` (→ `_mark_service_start_failed`
  → `_bot_query.get_by_binding_id`). The `bot_public_service.py:300`
  auth-relationship-rebuild executor is left alone — it touches auth-relationship
  data, not `ac_bots` (a later category's stage).
- **Both are latent** (single-tenant-safe today); they bite once a 2nd tenant /
  the real resolver exists. Delivered as a new PR off `dev` (the merged PR can't
  be reopened).

### F2: Tenant-leading indexes — MANDATORY policy, deferred  `[ ]`
- Tenant-leading indexes on a tenant-columned table are a **mandatory corp
  policy** (confirmed by user 2026-07-27). Consciously deferred to the dedicated
  index-adding work — **not** an exemption; must be completed before multi-tenant.
- No index in Stage 1: cardinality-1 column, existing indexes stay effective, so
  it buys nothing yet (see plan Data Model Changes for the full rationale).
- When done: prepend `avernet_tenant` to the query-backing composites
  (`idx_owner`, `idx_bot_id_entity_id`, `idx_entity`, search index) via
  create-new-then-drop-old (naming convention ties index name to columns;
  create before drop so no index-less window). Leave low-cardinality
  (`idx_status`, `idx_is_delete`) and unique-lookup (`idx_binding_id`) indexes.
