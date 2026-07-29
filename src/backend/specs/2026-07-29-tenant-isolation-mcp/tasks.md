# Tasks: Tenant Isolation — MCP Configuration (Stage 5)

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

## Task 1: [x] Spike — confirm the guard is inert for unrelated models
- **Goal:** Prove that appending a `with_loader_criteria` option for a model
  absent from a statement is a no-op, before the mechanism is built on that
  assumption.
- **Files:** `tests/community/plugins/test_avernet_tenant_guard.py` (new)
- **Done when:**
  - [x] A query touching only model X, executed while criteria for models X, Y
        and Z are all appended, returns exactly what it returns with only X's
        criteria appended — no extra join, no extra `WHERE` term, no error.
        **Confirmed:** the emitted SQL is
        `FROM spike_alpha WHERE spike_alpha.avernet_tenant = ?` — the other two
        models' criteria leave no trace.
  - [x] The assertion is left in the tree as a regression test, not deleted
        after the spike (mirrors how Stage 1 kept its `Query.update()` spike).
  - [x] If it does **not** hold: stop, record the finding, and revise `plan.md`
        before any further task — the one-listener design depends on it.
        **Not triggered** — the assumption held.
- **Depends on:** —

## Task 2: [x] Lift the tenant guards into a model-agnostic registrar
- **Goal:** Move the Stage 1 guards out of `BotModel` and behind
  `register_avernet_tenant_guard(model)`, with bot behavior byte-identical.
- **Files:** `src/agentclaw/community/utils/avernet_tenant_guard.py` (new),
  `src/agentclaw/community/plugin_api/models.py`,
  `src/agentclaw/community/plugin_api/README.md`
- **Done when:**
  - [x] `utils/avernet_tenant_guard.py` holds `CrossTenantInsertError`, the
        registry, the single `Session`-level `do_orm_execute` read listener that
        appends one criteria option per registered model, the per-model
        `before_insert` stamp, and `register_avernet_tenant_guard(model)`.
  - [x] Read guard keeps the `is_column_load` / `is_relationship_load` skips and
        the `skip_avernet_tenant_guard` execution option.
  - [x] Criteria are built as direct expressions per call, never lambdas — the
        lambda form is cached and would pin the first tenant.
  - [x] Registration is idempotent per model and the `Session` listener installs
        once, so a re-import cannot double-register.
        Covered by `test_registration_is_idempotent`.
  - [x] The insert-guard error message names the offending model rather than the
        hardcoded `"bot"`.
  - [x] `plugin_api/models.py` keeps `BotModel.avernet_tenant`, drops the guard
        bodies (`:101-186`), calls `register_avernet_tenant_guard(BotModel)`, and
        re-exports `CrossTenantInsertError`.
  - [x] `plugin_api/README.md` declares
        `agentclaw.community.utils.avernet_tenant_guard` in
        `internal_dependencies` — its own line, since the checker matches on
        `d` or `d + "."`.
  - [x] `tests/community/plugins/test_bot_tenant_guard.py`,
        `test_bot_tenant_isolation.py` and `test_bot_tenant_raw_sql_and_threads.py`
        pass **unmodified**. Any edit needed there is a defect in this task.
  - [x] `tests/community/architecture/` passes (the boundary guard failed CI
        twice in Stage 1 on undeclared imports). 108 passed.
  - **Also:** added `guarded_models()` for tests/diagnostics, and
    `register_avernet_tenant_guard` raises `TypeError` for a model that declares
    no tenant column — a guard against silently registering the wrong class.
- **Depends on:** Task 1

## Task 3: [x] Isolate `ac_user_mcp_config`
- **Goal:** Put MCP per-user configuration behind the tenant guard, including the
  unique-key change that lets two tenants hold the same `(user, server)`.
- **Files:** `src/agentclaw/community/core/models/mcp.py`,
  `tests/community/plugins/test_user_mcp_config_tenant_isolation.py` (new)
- **Done when:**
  - [x] `UserMCPConfig` gains
        `avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")`.
  - [x] Its `UniqueConstraint` becomes
        `("avernet_tenant", "user_id", "server_code", "env")`, named
        `uix_user_mcp_config_tenant`.
  - [x] `register_avernet_tenant_guard(UserMCPConfig)` after the class.
  - [x] `avernet_tenant` is **absent** from `to_dict()`; a test asserts the
        returned key set is unchanged.
  - [x] Cross-tenant reads return nothing, for each protocol method:
        `get_by_id`, `get_by_user_and_server_code`, `list_by_user`. (`list_by_user`
        has no production caller today — guard and test it anyway; the spec names
        it.)
  - [x] `update` and `delete` against another tenant's row return `None` /
        `False` — indistinguishable from a missing row — and leave the row
        untouched.
  - [x] `create` stamps the current tenant with no explicit stamp at the call
        site; an explicit conflicting tenant raises `CrossTenantInsertError`.
  - [x] **Two tenants each hold a config for the same `(user_id, server_code, env)`**
        and neither can see or displace the other's. This test fails without the
        unique-key change.
  - [x] `plugins/user_mcp_config_repository.py` is **unchanged** — the guards
        cover every method without a per-method filter.
  - [x] Each cross-tenant test's red run (before the change) is recorded.
        **Red run:** with the registration commented out and the old unique key
        restored — 6 failed, 2 errored, 3 passed. The two errors are
        `IntegrityError: UNIQUE constraint failed: ac_user_mcp_config.user_id,
        ac_user_mcp_config.server_code, ac_user_mcp_config.env`, which is exactly
        the failure the key change exists to prevent. Green after: 11 passed.
- **Depends on:** Task 2

## Task 4: [x] Isolate `ac_bot_mcp_call_config`
- **Goal:** Put a bot's per-server MCP call identity behind the same guard,
  covering the aggregate reads that never mention a bot record.
- **Files:** `src/agentclaw/community/core/caller_identity/models.py`,
  `tests/community/plugins/test_bot_mcp_call_config_tenant_isolation.py` (new)
- **Done when:**
  - [x] `BotMcpCallConfigModel` gains the same `avernet_tenant` column.
  - [x] `__table_args__` is **unchanged** — `bot_pk` is a global primary key, so
        the existing unique key already determines tenant and needs no reshape.
        The reason is recorded as a comment above `__table_args__`.
  - [x] `register_avernet_tenant_guard(BotMcpCallConfigModel)` after the class.
  - [x] Both aggregate reads — `list_draft_call_types`
        (`plugins/caller_identity_repository.py:279`) and the call-type rollup
        (`:302`) — return only the current tenant's rows.
  - [x] `replace_draft_call_type` stamps the tenant; a cross-tenant explicit
        insert raises `CrossTenantInsertError`.
  - [x] `plugins/caller_identity_repository.py` is **unchanged**.
  - [x] Each cross-tenant test's red run is recorded.
        **Red run:** with the registration commented out — 5 failed, 2 passed,
        including both aggregate reads and the cross-tenant delete. Green
        after: 7 passed.
- **Depends on:** Task 2

## Task 5: Prove the internal API is untouched
- **Goal:** Demonstrate that adding isolation is invisible to every existing
  caller.
- **Files:** `tests/community/api/mcp/routers/test_mcp.py` (extend) or a new
  sibling; no production file should need editing here.
- **Done when:**
  - [ ] `GET /mcp/user/config` and `POST /mcp/user/config` return
        byte-identical bodies to today under the default tenant, including the
        `api_key` masking and the `has_config` flag.
  - [ ] No response body anywhere carries `avernet_tenant`.
  - [ ] The existing internal API suite runs **unmodified**. Any edit needed
        there is a defect in Tasks 2–4, not a test to update.
  - [ ] Rows that predate the change resolve to the default tenant — asserted
        via `server_default`, not a Python default.
- **Depends on:** Tasks 3, 4

## Task 6: Update the handoff board and record the schema change
- **Goal:** Leave the board showing what is actually done and what is actually
  next, and put the DDL where whoever applies it will find it.
- **Files:** `src/backend/docs/openapi-v1/README.md`,
  `src/backend/docs/openapi-v1/README.zh-CN.md`
- **Done when:**
  - [ ] Track A Stage 5 shows ✅ DONE in both editions
        (`README.md:108`, `README.zh-CN.md:95`).
  - [ ] Channels show **deprioritized** rather than P2 in both editions, for
        **both** the Track A stage (`README.md:103`, `README.zh-CN.md:93`) and
        the Track B endpoint row (`README.md:119`, `README.zh-CN.md:109`), with
        the reason recorded. Rows keep their scope — deprioritized, not removed.
  - [ ] The three `ALTER TABLE` statements and **both** ordering constraints are
        recorded in the cross-cutting deferred-items section of both editions:
        the column adds must precede the code deploy; the unique-key swap must
        precede a second tenant writing, and is create-before-drop.
  - [ ] A dated changelog line is appended in both editions
        (`README.md:418`, `README.zh-CN.md:366`).
  - [ ] The English and Chinese editions say the same thing — no drift.
- **Depends on:** Tasks 3, 4

## Task 7: Tests & Verification
- **Goal:** Ensure the feature meets every spec acceptance criterion.
- **Files:** —
- **Done when:**
  - [ ] Every checkbox under **Isolation** in `spec.md` checks off, against the
        tests from Tasks 3, 4 and 5.
  - [ ] Every checkbox under **Handoff board** in `spec.md` checks off, against
        Task 6.
  - [ ] Cross-tenant isolation is demonstrated **for each isolated data set** by
        a test that failed before the change and passes after — both red runs
        recorded.
  - [ ] Backend SAST, unit tests, changed-line coverage and singlebox coverage
        are green. No coverage-manifest change should be needed: `mcp` sits in
        `pending_modules` with no thresholds, and `utils/`, `plugin_api/` and
        `core/models/` are outside the per-module Core denominators — if that
        turns out to be wrong, flag it rather than silently editing the manifest.
- **Depends on:** Tasks 5, 6

---

## Groups

> Groups bundle tasks into end-to-end units of work. `implement` executes
> one group at a time and runs code review on each group before moving on.

- **Group A — Mechanism:** Tasks 1, 2
  - Theme: Generalize the Stage 1 guard so it can cover models outside
    `plugin_api`, with bot behavior provably unchanged. Lands no new isolated
    data on its own.
- **Group B — Isolated data:** Tasks 3, 4
  - Theme: Put both MCP tables behind the guard, each with its own cross-tenant
    test that fails before and passes after. The unique-key change on
    `ac_user_mcp_config` is the load-bearing part.
- **Group C — Invisibility and handoff:** Tasks 5, 6
  - Theme: Prove nothing changed for existing callers, and leave the board and
    the DDL where the team will find them.
- **Group D — Verification:** Task 7
  - Theme: Final spec acceptance check.
