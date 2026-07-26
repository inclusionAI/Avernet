# Tasks: Tenant Isolation Foundation (Stage 1)

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

## Task 1: Spike — confirm listener covers ORM writes
- **Goal:** Empirically settle whether a `do_orm_execute` listener applying
  `with_loader_criteria(BotModel, ...)` also constrains `Query.update()` /
  `Query.delete()`, or whether the write methods need an explicit filter.
- **Files:** throwaway test under
  `tests/community/plugins/` (not committed, or committed as an xfail probe then
  removed) — no production change in this task.
- **Done when:**
  - [ ] A minimal reproduction inserts two bots under different tenants and runs
        `update_by_owner` / `soft_delete_by_owner` against the other tenant's bot
        with a prototype listener installed.
  - [ ] The finding is recorded in `tasks.md` under this task: **covered** (read
        path alone suffices) or **not covered** (write methods need an explicit
        `_avernet_tenant()` filter — Task 5/6 adopt it).
- **Depends on:** —

## Task 2: Tenant context primitive
- **Goal:** Add the request-lifetime tenant `ContextVar` and its helpers.
- **Files:** `src/agentclaw/community/utils/avernet_tenant.py` (new),
  `tests/community/...` (new unit test).
- **Done when:**
  - [ ] `DEFAULT_AVERNET_TENANT = "teamclaw"`, `get_current_avernet_tenant() -> str`
        (total, never `None`), `avernet_tenant_scope(tenant_id)` context manager,
        and `bind_current_avernet_tenant(fn)` are implemented.
  - [ ] Module docstring names the unrelated poolab/baas `tenant` concept so a
        future reader does not conflate them.
  - [ ] Tests: default outside a request; set/reset; nesting; reset still runs
        when the scoped body raises; a thread wrapped by
        `bind_current_avernet_tenant` observes the spawning tenant.
- **Depends on:** —

## Task 3: `avernet_tenant` column on `BotModel`
- **Goal:** Give bot records the tenant axis, stamped by default, invisible in
  API responses.
- **Files:** `src/agentclaw/community/plugin_api/models.py`,
  `tests/community/...` (to_dict test).
- **Done when:**
  - [ ] `BotModel` gains `avernet_tenant = Column(String(64),
        default=get_current_avernet_tenant, nullable=False)` after
        `caller_config_revision`.
  - [ ] `avernet_tenant` is **not** added to `BotModel.to_dict()`.
  - [ ] Test asserts `to_dict()`'s key set is unchanged from before this change.
  - [ ] `create_all` on local SQLite builds the column (a fresh insert carries a
        tenant without any caller passing one).
- **Depends on:** Task 2

## Task 4: Cross-tenant isolation test (red)
- **Goal:** Write the spec-required test that fails before the guard exists and
  will pass after — and record its red run.
- **Files:** `tests/community/plugins/...` (new).
- **Done when:**
  - [ ] Test inserts a bot under tenant A and asserts a read under tenant B does
        not return it, across `get_by_id`, `get_by_id_and_owner`, `list_by_owner`,
        `count_by_owner`, `exists_by_bot_name`, `search_bots`.
  - [ ] With no listener yet, the test **fails**, and the red run is recorded in
        the commit message / task notes.
- **Depends on:** Task 2, Task 3

## Task 5: `do_orm_execute` tenant guard (green)
- **Goal:** Install the single listener that scopes every `BotModel` statement
  to the current tenant; turn Task 4 green.
- **Files:** `src/agentclaw/community/plugin_api/models.py`,
  `tests/community/plugins/...`.
- **Done when:**
  - [ ] Listener registered via `event.listens_for(Session, "do_orm_execute")`
        at model import; registration idempotent on a module-level flag.
  - [ ] Applies `with_loader_criteria(BotModel, avernet_tenant ==
        get_current_avernet_tenant())`; honors `include_aliases` for joins;
        skips statements carrying `{"skip_avernet_tenant_guard": True}`.
  - [ ] Write coverage matches the Task 1 finding: if writes are not covered by
        the listener, `update_by_owner` / `soft_delete_by_owner` get an explicit
        `_avernet_tenant()` filter.
  - [ ] Task 4's test passes. Added tests: cross-tenant `update_by_owner` /
        `soft_delete_by_owner` return `None` / `False` and leave the row
        untouched (indistinguishable from missing); a bare
        `session.query(BotModel).all()` is filtered (proves non-repository
        query sites are covered).
- **Depends on:** Task 1, Task 3, Task 4

## Task 6: Stamp `avernet_tenant` in `BotRepository.insert`
- **Goal:** Make bot creation set the tenant explicitly, parity with `env`.
- **Files:** `src/agentclaw/community/plugins/bot_repository.py`,
  `tests/community/plugins/...`.
- **Done when:**
  - [ ] `insert` passes `avernet_tenant=get_current_avernet_tenant()` beside
        `env=get_current_env()`.
  - [ ] Test: a bot inserted inside `avernet_tenant_scope("t1")` has
        `avernet_tenant == "t1"`.
- **Depends on:** Task 3

## Task 7: Public-API tenant source (`resolve_avernet_tenant`)
- **Goal:** Add the single replaceable seam for the public API's tenant.
- **Files:** `src/agentclaw/community/adapters/http/openapi_v1/dependencies.py`.
- **Done when:**
  - [ ] Plain function `resolve_avernet_tenant(request) -> str` returns
        `DEFAULT_AVERNET_TENANT`, beside `require_principal`, same stub pattern,
        docstring marking it the drop-in point for the real verifier.
  - [ ] No Protocol, no DI binding, no `app.py` / `container.py` change.
- **Depends on:** Task 2

## Task 8: `AvernetTenantMiddleware`
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
- **Depends on:** Task 2, Task 7

## Task 9: Request-spawned work inherits the tenant
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

## Task 10: Reference DDL artifact — decision + optional file  `[!]`
- **Goal:** Resolve the spec-vs-convention tension the plan flagged: the spec
  says no migration file is checked in, but repo convention checks a reference
  `.sql` into `core/<module>/sql/` (e.g. `caller_identity`).
- **Files:** possibly `src/agentclaw/community/core/.../sql/2026_07_26_avernet_tenant.sql` (new).
- **Done when:**
  - [ ] User confirms: keep DDL in `plan.md` only (spec's stance) **or** also
        check in a reference `.sql` (convention). Pending that decision.
  - [ ] If "check in": the file carries exactly the plan's `ALTER TABLE` and the
        deploy-ordering note; if "plan only": this task is closed with no file.
- **Depends on:** — (decision), Task 3 (if a file is added)

## Task 11: Verification against spec acceptance criteria
- **Goal:** Prove every spec acceptance criterion holds.
- **Files:** — (runs suites; no production change).
- **Done when:**
  - [ ] The existing internal API test suite passes **unmodified**.
  - [ ] The cross-tenant isolation test (Task 4/5) is green; its earlier red run
        is on record.
  - [ ] Non-leakage across requests (incl. post-error) is green (Task 8).
  - [ ] Request-spawned work inherits the tenant (Task 9).
  - [ ] `to_dict()` key set unchanged (Task 3) — internal responses identical.
  - [ ] Backend unit tests, changed-line coverage, and singlebox coverage pass
        (`AGENTS.md:131`); pre-push hooks installed via
        `scripts/install_git_hooks.sh`.
- **Depends on:** Tasks 1–9 (and Task 10's decision)

---

## Groups

- **Group A — Mechanism groundwork:** Tasks 1, 2
  - Theme: confirm the write-path approach and land the tenant `ContextVar`
    primitive; pure utility + investigation, no bot-data behavior change yet.
- **Group B — ORM enforcement:** Tasks 3, 4, 5, 6
  - Theme: bot records carry a tenant and every read/write is scoped at the ORM
    layer; the spec's red→green cross-tenant isolation test passes.
- **Group C — Request wiring & inheritance:** Tasks 7, 8, 9
  - Theme: each request establishes its tenant (reset even on error),
    request-spawned work inherits it, and the public-API seam exists.
- **Group D — Finalize & verify:** Tasks 10, 11
  - Theme: resolve the checked-in-DDL decision and prove all spec acceptance
    criteria; green gates.
