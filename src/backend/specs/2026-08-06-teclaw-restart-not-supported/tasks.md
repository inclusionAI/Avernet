# Tasks: Teclaw Restart Is Not Supported

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

## Task 1: `[ ]` Reject teclaw restart in `BotService.restart_bot`

- **Goal:** Close the destructive path at its source. A teclaw restart request
  raises `BotOperationNotAllowedError` before any state is read or written.
- **Files:**
  `src/agentclaw/community/core/bot_management/services/bot_service.py`
- **Done when:**
  - [ ] Guard added immediately after the desktop guard (`:3870-3877`), before
        the `bot_status` read at `:3879`:
        `if self.is_teclaw_bot(bot.get("active_engine")): raise
        BotOperationNotAllowedError("teclaw 类型的 Bot 不支持重启")`
  - [ ] Message string matches the existing refusal in
        `create_bot_for_others_service.py` so users see one consistent wording.
  - [ ] No new imports required (`is_teclaw_bot` at `:3689`,
        `BotOperationNotAllowedError` at `:149` both already in module).
  - [ ] Docstring of `restart_bot` updated: notes teclaw is refused outright and
        why (no teclaw restart primitive; the generic path would destroy the
        container), referencing #869 for the recovery-path question.
  - [ ] Nothing else in `restart_bot` changed — the BaaS branch at `:4047` and
        the `stop_bot` + `start_bot` fall-through are untouched.
- **Depends on:** —

## Task 2: `[ ]` Prove the guard is inert, and that BaaS/arca are untouched

- **Goal:** Pin the two properties that matter: a teclaw restart mutates
  **nothing**, and no other provider's restart behavior moved.
- **Files:**
  `tests/community/core/bot_management/services/test_bot_service_restart_teclaw.py` (new)
- **Done when:**
  - [ ] Guard fires for a teclaw bot in each restart-eligible lifecycle state
        (`ACTIVE`, `FAILED`, `PENDING`), asserting
        `BotOperationNotAllowedError` and its message.
  - [ ] Guard fires regardless of binding state: live binding, stale binding
        (points at a destroyed container), and **no binding at all** — the case
        an engine-keyed guard handles and a provider-keyed one could not.
  - [ ] **Inertness assertions** (the core of this task): with mocked
        collaborators, assert zero calls to the device service
        (`release_device` / `apply_device` / `get_device`), the binding
        repository, the bot repository, the restart-lock repository, and the
        task queue.
  - [ ] **Regression pin** named for the original defect: a teclaw restart never
        reaches `stop_bot` — patch `stop_bot` and assert not called. This is the
        single behavior whose reintroduction re-destroys containers.
  - [ ] Non-regression: a BaaS-provider bot still takes `_restart_bot_baas`, and
        an arca-provider bot still takes `stop_bot` + `start_bot`.
  - [ ] `pytest tests/community/core/bot_management/services/` green — including
        `test_bot_service_restart_idempotency.py`,
        `test_bot_service_restart_baas_envs.py`,
        `test_bot_service_aix_extra_envs_restart.py`,
        `test_bot_service_stop_start.py` unchanged.
- **Depends on:** Task 1

## Task 3: `[ ]` Pin the client-error mapping on all four restart surfaces

- **Goal:** Every restart entry point reports the refusal as a client error, not
  a 500. No production code should need to change — this task proves it.
- **Files:**
  `tests/community/api/bot_management/test_router.py`,
  `tests/community/adapters/http/openapi_v1/test_bots_endpoints.py`
- **Done when:**
  - [ ] `POST /api/bots/{bot_id}/restart` → `error_code 400` for a teclaw bot
        (handler at `router.py:2685`).
  - [ ] `POST /api/bots/{bot_id}/restart-for-others` → `error_code 400`
        (handler at `router.py:414`).
  - [ ] `POST /api/bots/restart-scheduler` → `error_code 400`
        (handler at `router.py:501`).
  - [ ] `POST /v1/bots/{bot_id}/restart` → **409** via
        `openapi_v1/responses.py:177`. Assert 409, not 400 — the asymmetry with
        the legacy surfaces is pre-existing and intentionally not changed here.
  - [ ] Each asserts the response carries the teclaw message, so the user learns
        why rather than seeing a bare status code.
  - [ ] If any surface turns out **not** to map the error to a client status,
        stop and report before changing router code — the plan asserts all four
        already do, and a miss means the audit was wrong.
- **Depends on:** Task 1

## Task 4: `[ ]` Verify the `CreateBotForOthersService` caller

- **Goal:** The one internal caller that can actually reach the guard degrades
  to a client error rather than an unhandled exception.
- **Files:**
  `tests/community/core/bot_management/services/test_create_bot_for_others_service.py`,
  `src/agentclaw/community/core/bot_management/services/create_bot_for_others_service.py` (only if the test proves a gap)
- **Done when:**
  - [ ] Confirmed by test: a teclaw bot that is not `ACTIVE` and has no
        restart-wait reaches `_bot_service.restart_bot` (`:307`) and the raised
        `BotOperationNotAllowedError` surfaces as a client error, not a 500.
  - [ ] If it does not, convert it at the call site to `CreateBotForOthersError`
        with `error_code=400` and the same teclaw message — matching the
        existing refusal on the restart-wait branch. Change nothing else.
  - [ ] The existing teclaw refusal on the restart-wait branch still passes
        unchanged.
  - [ ] `BotPublishService.upgrade_bot_to_service` confirmed unaffected: its
        `is_teclaw_bot` branch at `:1250` skips the restart, so the guard is
        never reached from there. Assert the skip still holds.
- **Depends on:** Task 1

## Task 5: `[ ]` Full-suite verification and spec acceptance

- **Goal:** Every acceptance criterion in `spec.md` demonstrably holds, and the
  module gates are green against the release branch.
- **Files:** —
- **Done when:**
  - [ ] Each acceptance criterion in `spec.md` walked and checked off, with the
        test or code reference that satisfies it.
  - [ ] Backend module gates green:
        `OCB_PRE_PUSH_RUN_CI=1` with
        `AVERNET_PRE_PUSH_MERGE_TARGET=origin/REL20260806` per `AGENTS.md`.
  - [ ] `grep` confirms no other call path reaches `stop_bot` for a teclaw bot.
  - [ ] PR opened as draft against `REL20260806`, titled
        `fix(backend): reject restart for teclaw bots instead of destroying them`,
        body following `.github/pull_request_template.md`
        (Problem / Solution / Validation) and linking #869.
- **Depends on:** Tasks 2, 3, 4

## Groups

> Groups bundle tasks into end-to-end units. `implement` executes one group at
> a time and runs code review on each group before moving on.

- **Group A — The guard:** Tasks 1, 2
  - Theme: the destructive path is closed at the source, and proven inert. This
    group alone fixes the reported bug.
- **Group B — Surfaces and callers:** Tasks 3, 4
  - Theme: every entry point reports the refusal as a client error, and the one
    internal caller that reaches it degrades cleanly. Expected to be
    test-only — any production change here is a signal the audit was wrong.
- **Group C — Verification:** Task 5
  - Theme: spec acceptance walk, module gates, draft PR.
