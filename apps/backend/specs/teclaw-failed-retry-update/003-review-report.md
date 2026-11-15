# Independent review: Teclaw FAILED retry UPDATE

## Decision

PASS. No blocking correctness or scope findings in the reviewed changes.

## Scope and boundaries

- Existing chain: online release/retry and online restart select a candidate through `_decide_online_deploy`; the upgrade runner reuses the Bot and binding and delegates to `BotBuildService.upgrade_async`, whose Teclaw branch invokes `update_teclaw_bot`.
- Allowed change: FAILED selects UPGRADE while preserving the existing delivery, operation-ledger, progress-polling and callback behavior. Matching domain knowledge and provider capability comments explain the new policy.
- Protected boundaries: no OCB/gitlink changes, API/schema changes, new external-call wrappers or provider identity resolution. STOPPED, gone candidates, STOPPING and ambiguous/failed status reads keep their previous decisions.

## Findings and verification

1. The only executable production change is the decision condition: `status == "FAILED" or behavior.upgrade_recovers_not_live_bot`. For existing ARCA behavior this preserves UPGRADE; for Teclaw it changes only FAILED, while STOPPED still selects retirement/recreation.
2. Candidate selection remains current-publication binding first, then previous-publication binding. The new regression asserts the previous-publication lookup is not used when the current failed candidate exists.
3. The release regression executes the actual online release orchestration and deploy runner with the existing real SQLite operation ledger. It asserts `upgrade_async` receives the original Bot UUID and artifact; retirement, binding release, CREATE and new binding insertion are not called.
4. The restart regression exercises the shared online decision and actual restart runner, asserting reuse of the original target and no retirement or creation. It tests submission, not provider terminal success.
5. Existing build-service routing coverage verifies the downstream Teclaw upgrade invokes `update_teclaw_bot` with the original target and frozen artifact. Existing BOT_NOT_FOUND/DEVICE_NOT_FOUND upgrade fallback remains unchanged; this task does not promise UPDATE can recover a truly missing instance.
6. The existing diagnostic logger retains candidate UUID, BaaS status, provider and selected decision. New assertions require FAILED/teclaw/upgrade and the candidate UUID. No credentials or new external request/response body logging is introduced.
7. Provider behavior changes are comments/docstrings only; the capability remains False for Teclaw STOPPED recovery. The domain README records the durable policy and final-success boundary.

## Independently executed checks

From `apps/backend/`:

```bash
.venv/bin/python -m pytest tests/community/core/service_bot/services/test_teclaw_failed_retry_update.py tests/community/core/service_bot/services/test_publish_flow_service.py tests/community/core/service_bot/services/test_bot_build_service_teclaw_routing.py -q
```

Result: **215 passed**, 17 pre-existing Pydantic deprecation warnings, 7.08 seconds. `git diff --check` passed. Modified production source files are below the 1,000-line limit.

## Limits

No live deployment or real provider UPDATE was executed; this review proves local control-flow behavior and preserved downstream routing. Remote PR/CI and coverage gates are independent and must be reported from the actual PR head, not inferred from these tests. The general `FAILED` policy intentionally attempts UPDATE without a new provider existence probe, as specified by the user and task acceptance criteria.


## Review revision: provider-owned FAILED policy (2026-11-14)

**Current decision: PASS.** This section supersedes the earlier production-condition and generic-FAILED conclusions above. The first review covered built-in ARCA/Teclaw behavior but did not protect a custom provider with recovery capability False; the user correctly identified that extension-boundary gap.

The revised common decision only dispatches to `behavior.should_upgrade_not_live_bot(status)` for FAILED/STOPPED. It does not force either status to upgrade. The new base method is concrete, not abstract, and returns the pre-existing `upgrade_recovers_not_live_bot` property. Existing subclasses therefore retain their capability without implementing another abstract member. Teclaw alone overrides FAILED to True; its STOPPED result remains False. The existing property remains available, preserving callers and subclass definitions.

A real custom behavior subclass with capability False is registered through the existing router in a regression. Both FAILED and STOPPED still select RETIRE_THEN_FIRST_RELEASE, demonstrating that the original cross-provider expansion is removed. Separate provider-policy tests verify Default/ARCA returns True for both statuses and Teclaw returns True/False for FAILED/STOPPED. The public orchestration code contains no Teclaw-specific status override.

Online retry and online restart regressions still exercise the real workflow runners and SQLite ledger, preserve target UUID/binding, and assert that retirement, replacement creation and binding creation are not called. Existing build-service tests independently prove the Teclaw upgrade route calls `update_teclaw_bot`. Explicit BOT_NOT_FOUND/DEVICE_NOT_FOUND fallback, STOPPING wait and unknown/missing-status behavior are unchanged. Diagnostic fields remain target UUID, BaaS status, provider and decision; no external credential logging was added.

Independently executed in `apps/backend/`:

```bash
.venv/bin/python -m pytest tests/community/core/service_bot/services/test_teclaw_failed_retry_update.py tests/community/core/service_bot/services/test_provider_behavior.py tests/community/core/service_bot/services/test_publish_flow_service.py tests/community/core/service_bot/services/test_bot_build_service_teclaw_routing.py -q
```

Result: **235 passed**, 17 existing Pydantic deprecation warnings, 8.25 seconds. `git diff --check` passed. No blocking correctness or compatibility findings in this revision. Scope remains Avernet only, with no OCB/gitlink edits. Real provider execution and remote CI are separate verification gates and are not inferred from this result.
