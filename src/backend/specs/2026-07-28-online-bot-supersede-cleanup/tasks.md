# Tasks: Online Bot Supersede Cleanup

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked
>
> Paths are relative to the backend module root `src/backend/src/`.

## Task 1: `[x]` Cleanup primitive — `retire_superseded_bot`

- **Goal:** One idempotent "destroy a superseded bot" method that every seam can
  call. Failures **propagate** (never report a failed lifecycle write as
  success); never touches a live bot (callers gate on the decision/error-code).
- **Files:**
  `src/agentclaw/community/core/service_bot/services/bot_build_service.py`,
  `tests/community/core/service_bot/services/test_bot_build_service_teclaw_routing.py`
  (or a new `test_bot_build_service_retire.py`)
- **Done when:**
  - [x] `BotBuildService.retire_superseded_bot(self, bot_uuid: str) -> None`
        added: calls `self._baas_service.destroy_bot(bot_uuid)` with a
        `bot_uuid`-derived deterministic `request_id`; a `destroy_bot` failure
        **propagates** (AGENTS.md: never swallow a failed lifecycle write and
        report success) so the caller does not create a replacement while the old
        bot may still be live — the durable deploy retries and re-evaluates.
  - [x] Docstring states it is idempotent (BaaS `destroy` tolerates already-gone
        via the deterministic `request_id`), propagates failures, and must only
        be called for a bot the caller has decided is superseded/gone.
  - [x] Unit: `destroy_bot` called once with the uuid on the happy path.
  - [x] Unit: `destroy_bot` raising **propagates** (method re-raises).
  - [x] `pytest tests/community/core/service_bot/services/` green.
- **Depends on:** —

## Task 2: `[x]` Carry the gone-bot error code through `TargetBotGoneError`

- **Goal:** Preserve the specific gone code (`BOT_NOT_FOUND` vs
  `DEVICE_NOT_FOUND`) out of the deploy atom so the secondary fallback can branch.
- **Files:**
  `src/agentclaw/community/core/service_bot/services/publish_flow/operation_runner.py`,
  `tests/community/core/service_bot/services/test_operation_runner.py`
- **Done when:**
  - [x] `TargetBotGoneError.__init__(self, error_code: str = "BOT_NOT_FOUND")`
        stores `self.error_code`; docstring updated.
  - [x] `acquire_deploy_workflow` raises `TargetBotGoneError(result.get(
        "error_code"))` when `result["error_code"] in BOT_GONE_ERROR_CODES`.
        `BOT_GONE_ERROR_CODES` unchanged (`{BOT_NOT_FOUND, DEVICE_NOT_FOUND}`).
  - [x] Unit: a `BOT_NOT_FOUND` result raises `TargetBotGoneError` with
        `error_code == "BOT_NOT_FOUND"`; a `DEVICE_NOT_FOUND` result → `error_code
        == "DEVICE_NOT_FOUND"`.
  - [x] `pytest tests/community/core/service_bot/services/test_operation_runner.py`
        green.
- **Depends on:** —

## Task 3: `[x]` Provider-aware unified decision

- **Goal:** Replace `_should_upgrade_online` + `_ONLINE_UPGRADE_BLOCKING_BAAS_STATUSES`
  with one candidate resolver and one provider-aware decision matrix.
- **Files:**
  `src/agentclaw/community/core/service_bot/services/publish_flow/upgrade_resolution_mixin.py`,
  `src/agentclaw/community/core/service_bot/types.py` (decision enum),
  `tests/community/core/service_bot/services/test_publish_flow_service.py`
- **Done when:**
  - [x] `OnlineDeployDecision` enum `{UPGRADE, RETIRE_THEN_FIRST_RELEASE,
        FIRST_RELEASE}` defined (types.py).
  - [x] `_resolve_online_reuse_target(publish_record) -> tuple[str | None, int |
        None]`: this record's own `ext.binding.online` → binding → `device_id`
        first; else `last_pub_id`'s online binding; else `(None, None)`.
  - [x] `_decide_online_deploy(publish_record, bot) -> OnlineDeployDecision`
        implements the matrix: no candidate / `RELEASED` / `DESTROYING` /
        status-absent → `FIRST_RELEASE`; `ACTIVE` → `UPGRADE`;
        `FAILED`/`STOPPED`/`STOPPING` → `RETIRE_THEN_FIRST_RELEASE` iff
        `resolve_container_provider(bot) == TECLAW_DEVICE_PROVIDER` else
        `UPGRADE`; `PENDING`/unknown → `UPGRADE`. A `get_bot` failure
        **propagates** (a genuine 404 is already normalized to `RELEASED`, so a
        raised error is transient/non-404 — NOT proof the candidate is gone; the
        durable task retries the status read rather than replacing a live bot).
  - [x] `_ONLINE_UPGRADE_BLOCKING_BAAS_STATUSES` and `_should_upgrade_online`
        removed; `grep -rn "_ONLINE_UPGRADE_BLOCKING_BAAS_STATUSES\|_should_upgrade_online"
        src/agentclaw` returns nothing (call sites migrated in Task 4).
  - [x] Unit (table-driven) over `(provider ∈ {teclaw, baas}) × (status ∈
        {ACTIVE, FAILED, STOPPED, STOPPING, RELEASED, DESTROYING, PENDING, absent})`
        asserting the decision, incl. `get_bot`-raises → **propagates**.
  - [x] `pytest tests/community/core/service_bot/services/` green.
- **Depends on:** —

## Task 4: `[x]` Wire the decision into the online-release dispatch

- **Goal:** The online release consumes the 3-way decision; the upgrade target is
  resolved own-binding-first (so the failed-first-release **retry** reuses).
- **Files:**
  `src/agentclaw/community/core/service_bot/services/publish_flow_service.py`,
  `tests/community/core/service_bot/services/test_publish_flow_service.py`
- **Done when:**
  - [x] `_execute_online_release` switches on `_decide_online_deploy(publish_record,
        bot)`: `UPGRADE` → `_execute_upgrade_release`; `RETIRE_THEN_FIRST_RELEASE`
        → `self._build_service.retire_superseded_bot(candidate_bot_uuid)` then
        `_execute_first_release`; `FIRST_RELEASE` → `_execute_first_release`.
  - [x] `_execute_upgrade_release` resolves `bot_uuid`/`existing_binding_id` via
        `_resolve_online_reuse_target` (own binding first, then `last_pub_id`) —
        not `last_pub_id` only.
  - [x] Retry of a failed online first-release: `baas`/ARCA candidate `FAILED` →
        upgrade against the **same** `bot_uuid` (no new bot); `teclaw` candidate
        `FAILED` → `destroy(old)` then first_release (one live bot).
  - [x] Unit: retry-ARCA reuse (same uuid, no destroy, no new bot); retry-teclaw
        (destroy once + first_release); re-publish prev `STOPPED` (baas upgrade /
        teclaw destroy+recreate).
  - [x] `pytest tests/community/core/service_bot/services/` green.
- **Depends on:** Task 1, Task 3

## Task 5: `[x]` Secondary net — retire on the upgrade-fallback error code

- **Goal:** Keep the reactive path for the rare non-teclaw race: when an
  attempted `upgrade` reports a gone bot, clean up before first-release only when
  the record still lingers.
- **Files:**
  `src/agentclaw/community/core/service_bot/services/publish_flow/release_stage.py`,
  `tests/community/core/service_bot/services/test_publish_flow_service.py`
- **Done when:**
  - [x] `upgrade_release`'s `except TargetBotGoneError as e:` calls
        `self._build_service.retire_superseded_bot(bot_uuid)` **iff**
        `e.error_code == "DEVICE_NOT_FOUND"`, then `fallback(...)`; no destroy for
        `BOT_NOT_FOUND`.
  - [x] Unit: `DEVICE_NOT_FOUND` fallback → `destroy` called once then first
        release; `BOT_NOT_FOUND` fallback → no destroy, first release.
  - [x] `pytest tests/community/core/service_bot/services/` green.
- **Depends on:** Task 1, Task 2

## Task 6: `[x]` Apply the decision + cleanup to restart

- **Goal:** Restart uses the same provider-aware decision and cleanup, so a
  restart against a `FAILED`/`STOPPED`/gone target never orphans a bot.
- **Files:**
  `src/agentclaw/community/core/service_bot/services/publish_flow/restart_mixin.py`,
  `tests/community/core/service_bot/services/test_publish_flow_service.py`
  (+ `tests/community/e2e/publish_boundary/test_chain_and_restart_flows.py` if
  touched)
- **Done when:**
  - [x] `execute_restart` decides via `_decide_online_deploy` on the restart
        target: `UPGRADE` → existing upgrade path; every **non-`UPGRADE`** decision
        recreates *directly* (`RETIRE_THEN_FIRST_RELEASE` calls
        `retire_superseded_bot(bot_uuid)` first; both it and `FIRST_RELEASE` then
        open+abandon a fresh `RESTART` op — so `sync_restart_progress` does not
        read a stale earlier restart and instead falls back to the `ext.restart`
        handle the recreate writes — before `_recreate_restart_target`). A
        `FIRST_RELEASE`/`DESTROYING` target is NOT sent through `upgrade_async`
        (its UPDATE is rejected with an error the atom does not classify as
        `BOT_NOT_FOUND`, which would strand the restart).
  - [x] `except TargetBotGoneError as e:` applies the same code-gated
        `retire_superseded_bot` (DEVICE_NOT_FOUND only) before
        `_recreate_restart_target`.
  - [x] Unit: restart `teclaw`+`STOPPED` → destroy + recreate; restart
        `baas`+`FAILED` → upgrade (same uuid); restart `DEVICE_NOT_FOUND`
        fallback → destroy + recreate; `BOT_NOT_FOUND` fallback → recreate, no
        destroy.
  - [x] `pytest tests/community/core/service_bot/services/` green.
- **Depends on:** Task 1, Task 2, Task 3

## Task 7: `[x]` Regression, crash-safety, and E2E guard

- **Goal:** Existing tests reflect the new behavior; crash-safety and single-bot
  end-states are proven; the wider suites stay green.
- **Files:**
  `tests/community/core/service_bot/services/test_publish_flow_service.py`,
  `tests/community/core/service_bot/services/test_operation_runner.py`,
  `tests/community/core/service_bot/services/test_bot_build_service_teclaw_routing.py`,
  `tests/community/core/service_bot/services/test_publish_crash_windows.py`,
  `tests/community/e2e/publish_boundary/` (chain/restart, retry/failure flows)
- **Done when:**
  - [x] `dcce9a6` regression tests adapted: teclaw offline→re-publish yields a
        single live bot **with the old `STOPPED` bot destroyed** (not orphaned);
        non-teclaw `FAILED`/`STOPPED` now **reuses** (same uuid) rather than
        recreating. No test still asserts the old orphan-leaving behavior.
  - [x] Crash-safety: redelivery after `destroy(old)` but before first-release —
        candidate now reads `RELEASED` → decision `FIRST_RELEASE` → adopt the
        in-doubt new bot by query; assert **no double-destroy, exactly one live
        bot**.
  - [x] Invariant assertion helper/test: after each covered flow, exactly one
        live (`is_deleted=0`, non-`RELEASED`) online `bot_uuid` per record/stage.
  - [x] `pytest tests/community/core/service_bot/` green.
  - [x] `pytest tests/community/e2e/publish_boundary/` green.
- **Depends on:** Task 4, Task 5, Task 6

## Notes
- No schema/DDL/migration; no new BaaS endpoints, config, or feature flags.
- Reconciliation of already-orphaned production bots is **out of scope** (a
  separate one-off operational sweep — see `spec.md`).
