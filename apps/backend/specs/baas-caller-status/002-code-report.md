---
agent: tc-code
status: completed
created: 2026-11-18
iteration: 1
---

# BaaS Caller status implementation

## Scope and existing chain

Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/baas-caller-status-20261118`.

The existing ordinary ExpertChat BaaS adapter verifies the shared service JWT. The new flat GET calls the existing instance Service API directly; the existing domain implementation resolves the exact source Bot/real owner, reads the Caller ledger and safely observes provider health. The original public status adapter and its user/grant/chat policy remain unchanged.

Allowed additions are the ordinary adapter/auth wrapper, owning contract documentation and meaningful tests/route inventory. No new lifecycle method, generic identity admission, DI provider, persistence field or production action was added. OCB, Relay, BaaS lifecycle and shared-runtime selection remain outside this change. Prerequisite PR #3914 is still open; its rebased commits supply the original status capability.

## Changes

| File | Purpose |
| --- | --- |
| `src/agentclaw/community/adapters/http/expert_chat/router.py` | Add authenticated GET `/api/v1/expert-chats/app-caller-instance/status`; require nonempty Bot/Owner/Caller; map ordinary errors safely |
| `src/agentclaw/community/adapters/http/org/dependencies.py` | Share existing service JWT verification behind internal helper and route-specific wrappers; preserve connection behavior |
| `src/agentclaw/community/adapters/http/openapi_v1/authorization_rules.py` | Compact only the prerequisite status row to retain the latest dev source-size ceiling of 1,000 lines; authorization semantics unchanged |
| `src/agentclaw/community/core/expert_chat/README.md` | Document BaaS-specific path, identity and query contract, tenant rule and public-path distinction |
| `tests/community/api/expert_chat/test_app_caller_status.py` | Real signed HTTP credential matrix, safe errors/logs, required parameters and public-path refusal |
| `tests/community/endpoints/test_baas_caller_status.py` | Four actual DI/SQLite registered endpoint cases, including private other-user reads without grants and unchanged pending ledger |
| `tests/community/acceptance/expert_chat/test_baas_caller_status.py` | Live BaaS credential/private-Bot story: 401, absent, pending and registered provider-404 observations without provisioning |
| `tests/community/acceptance/expert_chat/test_caller_instance_status.py` | Include latest schema's token-expiry column in prerequisite no-write ledger snapshot |
| `singlebox/ci/singlebox_coverage_modules.yaml` | Include the BaaS GET in the existing module's full router denominator; thresholds unchanged |

## Contract and safety

BaaS sends a valid HS256 service JWT in `X-Avernet-Principal`, with issuer `baas` and required time claims. Existing key/config and shared decoder remain authoritative. Missing/invalid credentials return HTTP 401. `bot_id`, `owner_id`, `user_id` are required nonempty query fields. Success uses ordinary `ApiResponse` and its `data` contains only `exists` and `alive`. Permission/domain failure retains ordinary code 403; upstream failure is a sanitized non-success 5999 response, never a successful false-health observation.

The service identity permits querying another user's private Bot without app grants/chat membership. JWT principals/tenant/audience/app claims are not projected into user/tenant scope. Trusted server tenant/environment continue to drive repositories. A real BaaS JWT remains rejected by the original public GET. No create/upgrade/restart/publish/exchange or instance/binding write was added.

Engine/stage behavior is unchanged: independent OpenClaw Caller observations use the precise existing Caller ledger across trusted environments; Teclaw shared runtimes are not substituted or allocated by this endpoint. No IAM/ASC refresh, expiry coordination or stage lifecycle path was changed. The live stories compare expiry alongside all other ledger fields.

## Verification

Python runtime reused from the existing caller-cron-sessions worktree. Commands run from `apps/backend` with `DEPLOY_PROFILE=test PYTHONPATH=src:.`.

- RED: the new signed HTTP success case returned HTTP 405 before the GET existed; genuine missing behavior.
- New status plus legacy BaaS connection boundary suites: **70 passed** before the added public-path negative test.
- Final new status suite, including BaaS JWT rejection on the public GET: **38 passed**.
- Real endpoint runner `tests/community/endpoints/test_endpoint_runner.py -k app-caller-instance`: **4 passed**; these are actual registered HTTP/SQLite invocations, not collection-only evidence.
- Full OpenAPI adapter + framework + new/old BaaS boundary + original status domain suite, `-n 4`: **2,804 passed, 2 skipped**, 0 failed, 132.79 seconds. JUnit: `/tmp/baas-caller-status-contracts.xml`.
- Source-size and full authorization inventory after prerequisite row formatting: **37 passed**.
- New live acceptance story collection: **1 collected**. No live stack was started locally; remote Singlebox remains a separate pending gate.
- Full Backend `tests/community -n 4 --dist loadfile`: **32,508 passed, 41 skipped, 6 subtests passed, 0 failed/0 errors**, 474.13 seconds. JUnit `/tmp/baas-caller-status-backend.xml`. Coverage was not enabled for this behavior run; remote coverage must be measured separately.
- Ruff F401/F821/F841 and `git diff --check`: passed. New runtime files are 734 and 137 lines; authorization inventory is exactly 1,000.

## External boundary logs

Authentication events use `expert_chat.app_caller_instance_status.authentication_request`, `.authenticated` and `.denied`. Query events use `.request`, `.success`, `.denied` or `.failed`. Fields include backend/inbound, operation, route/method, request ID, trusted tenant/environment, Bot/Owner/Caller, status/error code, safe two-boolean response and duration. Legacy connection diagnostics keep their original operation name.

Credential absence, malformed JWT, bad key/algorithm, tampering, wrong/malformed/missing issuer, required time claims, expiry and future time failures have real signed-HTTP coverage. Tests assert service JWT/key/untrusted JOSE text and provider/exception sentinel details do not enter logs. The reused provider observation retains request/success/failure events and `log_response=False`; full upstream payloads are not logged.

The independent review separately measured the two BaaS runtime files' changed execution lines as **33/33**, local only; this is not a claim that remote final-head coverage has passed.
