---
agent: tc-code
status: completed
created: 2026-10-29
iteration: 1
---

# Super-admin cross-owner call-type editing

## Boundary and result
Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/admin-cross-owner-call-type-rel20261029`.
Branch: `feat/admin-cross-owner-call-type-rel20261029`; implementation tested from release base `9e59870168` before parent-managed rebase/delivery.

Existing chain remains HTTP caller-identity adapters → CallerIdentityService → row-locked identity repository → Passport sync/conditional compensation. Only the two call-type mutation paths gain configured super-admin cross-owner permission. Ordinary non-owner, operator-only and collaborator users remain denied. Existing Owner behavior and the last Caller-to-Owner super-admin rule remain in place.

Actual actor, Owner and entity are separate: actor authorizes, owns the edit lock and receives modifier attribution; actual current Owner addresses resource discovery/MCP Principal/CLI Passport; immutable target entity supplies the lock key. Preliminary checks, writes and compensation all use `bot_id:entity_id`. No lock stealing or skipped epoch checks. No new HTTP flags, owner mutation, broad ACL/asset/read permission changes, publication changes, storage migrations or instance lifecycle changes.

## Necessary files
| File under apps/backend | Reason |
| --- | --- |
| `src/agentclaw/community/core/caller_identity/service.py` | A private edit-policy helper checks Owner or trusted configured super admin and audits the reason. Both mutation paths use actual Owner discovery/sync and immutable entity lock keys for checks, writes and compensation. Shared asset guards/read helpers are unchanged. |
| `src/agentclaw/community/core/caller_identity/caller_identity_service_protocol.py` | Document the existing mutation contracts' additional authorized actor and true-Owner addressing. Signatures remain unchanged. |
| `src/agentclaw/community/core/caller_identity/README.md` | Document actor/Owner/entity separation, scoped exception, canonical lock and immediate-effect semantics. |
| `tests/community/core/caller_identity/test_cross_owner_call_type.py` | New real SQLite service-chain tests for cross-owner success/denial, handoff, scope addressing, audit attribution, target locks, races and failure compensation; explicit no-bypass checks for shared asset guard, missing/ambiguous entities, profile and resources. |
| `tests/community/core/caller_identity/test_admin_owner_transition.py` | Update old owner-only-admin denial to ordinary non-owner denial, and old Owner lock fixtures to canonical entity keys. |
| `tests/community/core/caller_identity/test_service.py` | Update one exact CLI compensation lock expectation from Owner to immutable entity. |

## TDD evidence and checks
- Added cross-owner test suite first: **24 expected failures**. Configured admins hit `CALLER_IDENTITY_FORBIDDEN`; ordinary rejection cases lacked the required diagnostic event.
- First implementation run exposed three real Owner-addressing test failures in CLI discovery; fixed the discovery input to the actual Owner and retained the independent assertion.
- Final focused + conformance run: **394 passed**, 17 existing Pydantic/dependency warnings, no failures. From `apps/backend`:
  `.venv/bin/python -m pytest tests/community/core/caller_identity tests/community/repository/identity/test_caller_identity_repository.py tests/community/api/test_caller_identity_service.py tests/community/core/mcp/services/test_caller_identity_principal_sync.py tests/community/architecture/test_module_boundaries.py tests/community/architecture/test_service_api_conformance.py -q --disable-warnings --tb=short`
- Ruff F401/F821/F841/E9 over all changed Python files: **PASS**.
- `git diff --check`: **PASS**.
- Changed source sizes: service 824 lines, protocol 154 lines; new test file 253 lines. No source exceeds 1,000 lines.
- Independent environment: `apps/backend/.venv`, `uv sync --frozen --group dev`, Python 3.12.13. No shared environment edits.
- Full CI/report-tail/coverage/rebase/PR checks belong to the parent and independent regression agent; not claimed by this coding report.

## Audit and external boundaries
New `caller_call_type_edit_authorized` / `caller_call_type_edit_denied` events record `bot_id`, `actor_id`, `owner_id`, `entity_id`, `resource_type`, `resource_code`, and `reason` (`owner`, `super_admin`, `not_owner_or_super_admin`, `bot_not_found`, `inactive_or_non_service`). These explain permission outcomes without dumping objects or credentials. Existing mutation/Passport request, success, failure and compensation events remain in use; no external boundary client or HTTP schema was rewritten.

Real service/repository tests assert new permission logs, actual Owner sync arguments, preserved Owner/entity, admin modifier attribution, full CLI explicit Owner scope, target-lock compensation and no synthetic secret text in success/failure logs. Existing strict request/query schema tests reject privilege-bearing extra fields. The shared Owner-only guard still rejects an admin explicitly, demonstrating no unrelated asset permission expansion.

## Delivery state
Code/tests final and stable. No commit, push, PR creation, full backend CI or deployment performed by this agent. Parent owns those steps and the root project log.
