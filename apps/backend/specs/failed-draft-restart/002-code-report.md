---
agent: tc-code
status: completed
created: 2026-11-04T22:16:55+08:00
iteration: 1
---

# Coding report: failed-draft-restart

## Existing chain and boundary

`BotService.restart_bot` resolves the historical Provider before entering the existing durable restart lock and allocation pipeline. This change classifies service runtime scope within that historical read only. It preserves personal-Bot behavior, bound-Bot restarts, Provider override policy, lock semantics, callbacks, allocation SDKs, successful new-binding writeback, and failure cleanup.

Allowed additions: one small pure classifier, minimal service-only historical resolver wiring, focused tests and task documents. Forbidden/unmodified: Router, Expert Chat lifecycle, Caller provisioning, release selection, external SDK calls, repository interfaces/schema, OCB source or gitlinks, production data and deployment.

## Worktree

- Repository: `inclusionAI/Avernet`.
- Branch: `fix/failed-draft-restart-rel20261105`.
- Base at implementation: `b2ff992a5d6450783af118529f94570b5e7440fd` (`github/REL20261105`).
- Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/fix-failed-draft-restart-rel20261105`.

## Files and behavior

| File | Necessary change |
|---|---|
| `apps/backend/src/agentclaw/community/core/bot_management/services/restart_binding_scope.py` | Pure service history classifier: exact immutable entity/environment/Bot identity; Caller/evaluation/non-draft exclusions; positive DeviceService callback/resource identity; unknown scope refusal. BaaS draft `publish_id` remains compatible. |
| `apps/backend/src/agentclaw/community/core/bot_management/services/bot_service.py` | Pass Bot type to historical recovery; skip known foreign scopes before lifecycle checks and refuse unknown scope; retain the existing terminal/live draft and Provider checks. |
| `apps/backend/tests/community/core/bot_management/services/test_service_draft_restart_recovery.py` | Thirty cases covering the actual defect, foreign scopes, positive draft identity, terminal/live behavior, pagination, exact IDs, missing Provider, lock suppression, diagnostic logs, and real background allocator failure followed by successful retry with a fresh binding. |
| `apps/backend/specs/failed-draft-restart/implementation-plan.md` | Verifiable implementation steps. |

Historical bindings are never mutated or reattached. The end-to-end retry test executes the real `start_bot` and `_allocate_device_async` with a synchronous Thread double: first allocation raises, leaves FAILED and no binding, releases the lock; second retry allocates a fresh binding and reaches ACTIVE. The original history record remains RELEASED.

## TDD and validation

- Baseline: existing restart idempotency suite **79/79 PASS** before edits.
- Red: two initial new tests failed on unmodified source: newer pending Caller wrongly prevented draft recovery; unknown service history was wrongly accepted.
- Green: focused new and existing suites **109/109 PASS**, zero skips/failures, 17 existing dependency deprecation warnings.
- Targeted Ruff `F,E203,E211,E265` with preview: all three changed/new Python files **PASS**.
- `git diff --check`: **PASS**.
- Interpreter: final checks used the isolated worktree's `apps/backend/.venv` installed by the independent regression agent. Baseline used an existing interpreter with this worktree's `src` first on `PYTHONPATH`; traceback/warning module paths confirmed local source.

Final focused command (from `apps/backend`):

```bash
DEPLOY_PROFILE=test PYTHONPATH=src COVERAGE_FILE=/tmp/failed-draft-focused.coverage .venv/bin/python -m pytest tests/community/core/bot_management/services/test_service_draft_restart_recovery.py tests/community/core/bot_management/services/test_bot_service_restart_idempotency.py -q --disable-warnings --cov=agentclaw.community.core.bot_management.services.restart_binding_scope --cov=agentclaw.community.core.bot_management.services.bot_service --cov-report=json:/tmp/failed-draft-focused-cov.json --junitxml=/tmp/failed-draft-focused-junit.xml
```

Coverage from that final run:

| Scope | Covered / executable lines | Result |
|---|---:|---:|
| New scope classifier | 20 / 20 | 100% |
| Changed executable BotService lines | 8 / 8 | 100% |
| Combined new/changed executable production lines | 28 / 28 | 100% |

These are focused measurements, not full-module or remote CI claims. Independent full CI, Review, rebase and PR checks are handled by the main workflow.

## Diagnostics and secrets

New existing-logger diagnostic: `[bot_service.restart_bot] historical service binding scope`. Fields: exact `bot_id`, `entity_id`, `entity_type`, `env`, historical `binding_id`, classified `scope`, and `binding_status`. Existing recovery/refusal logs still provide selected Provider or unsafe lifecycle status. The classifier never logs or returns `callback_token` or entire binding props. Tests assert Caller/unknown decisions and identifiers and prove a synthetic callback credential is absent from emitted logs.

No external inbound/outbound boundary or payload is added or modified, so no SDK/HTTP logging changes or recursive masking facility are introduced.

## Size and remaining gates

- New helper: 55 lines; new focused test: 240 lines.
- Existing `bot_service.py`: 6,358 lines at base, 6,379 after minimal 21-line wiring. New policy is isolated in its own small helper; no unrelated split, allowlist, threshold or lint configuration change was made.
- No commit/push, PR, deployment or production mutation was performed by the coding agent.
