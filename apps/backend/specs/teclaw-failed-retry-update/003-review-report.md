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
