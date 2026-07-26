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

## Task 6: Public-API tenant source (`resolve_avernet_tenant`)
- **Goal:** Add the single replaceable seam for the public API's tenant.
- **Files:** `src/agentclaw/community/adapters/http/openapi_v1/dependencies.py`.
- **Done when:**
  - [ ] Plain function `resolve_avernet_tenant(request) -> str` returns
        `DEFAULT_AVERNET_TENANT`, beside `require_principal`, same stub pattern,
        docstring marking it the drop-in point for the real verifier.
  - [ ] No Protocol, no DI binding, no `app.py` / `container.py` change.
- **Depends on:** Task 2

## Task 7: `AvernetTenantMiddleware`
- **Goal:** Establish each request's tenant for its whole lifetime, reset on the
  way out including on error.
- **Files:** `src/agentclaw/community/adapters/http/middleware.py`,
  `tests/community/...` (integration).
- **Done when:**
  - [ ] `AvernetTenantMiddleware.dispatch` picks `resolve_avernet_tenant(request)`
        for `/openapi/v1/*` paths, else `DEFAULT_AVERNET_TENANT`; enters
        `avernet_tenant_scope`; awaits `call_next`.
  - [ ] Added in `install_middleware` immediately after `UserContextMiddleware`
        so it is outside it (auth plugin DB reads run under the tenant); no new
        `install_middleware` parameter.
  - [ ] Integration test: two sequential requests through the ASGI app — the
        second sees `teamclaw`; repeated where the first handler raises 500, the
        tenant still does not leak.
- **Depends on:** Task 2, Task 6

## Task 8: Request-spawned work inherits the tenant
- **Goal:** In-request background threads observe the request's tenant.
- **Files:** `core/bot_management/services/bot_service.py` (3 sites),
  `core/service_bot/services/bot_publish_service.py`,
  `core/bot_collaborator/services/collaborator_service.py`,
  `tests/community/...`.
- **Done when:**
  - [ ] The five `threading.Thread` targets listed in the plan are wrapped with
        `bind_current_avernet_tenant` (or the tenant is otherwise captured and
        re-established inside the thread).
  - [ ] `asyncio.create_task` sites are left unchanged (verified they inherit
        context).
  - [ ] Test: a bot operation performed on a spawned thread runs under the
        spawning request's tenant.
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

## Task 10: Verification against spec acceptance criteria
- **Goal:** Prove every spec acceptance criterion holds.
- **Files:** — (runs suites; no production change).
- **Done when:**
  - [ ] The existing internal API test suite passes **unmodified**.
  - [ ] The cross-tenant isolation test (Task 4/5) is green; its earlier red run
        is on record.
  - [ ] Non-leakage across requests (incl. post-error) is green (Task 7).
  - [ ] Request-spawned work inherits the tenant (Task 8).
  - [ ] `to_dict()` key set unchanged (Task 3) — internal responses identical.
  - [ ] Backend unit tests, changed-line coverage, and singlebox coverage pass
        (`AGENTS.md:131`); pre-push hooks installed via
        `scripts/install_git_hooks.sh`.
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
