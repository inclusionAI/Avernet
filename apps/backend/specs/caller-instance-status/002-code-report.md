---
agent: tc-code
status: completed
created: 2026-11-12
iteration: 1
---

# Caller instance status implementation

## Boundary

Existing chain: public Bots delivery -> authenticated acting user/addressed application grant -> ExpertChat application service -> independent Caller ledger and BaaS observation.

Allowed additions: one flat GET route, explicit admission/authorization inventory, two existing service contracts/methods, one small response DTO, necessary safe BaaS read logging, tests and owning documentation. Forbidden: provisioning, restart/upgrade, publication polling, identity exchange, ledger/binding writes, shared-runtime fallback, OCB/gitlink changes.

## Worktree

- `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/caller-instance-status-20261112`
- `feat/caller-instance-status-20261112`, initialized from GitHub `dev` `d0cdd02345`.

## Implementation

- `GET /openapi/v1/bots/{bot_id}/caller-instance/status` returns standard Envelope data exactly `{exists, alive}`. UserIdDep prevents human/mixed-principal cross-user queries; OwnerIdDep supports entity_id/owner_id, and application-only calls require an addressed-Bot grant.
- ExpertChatService reuses existing owner/public/collaborator chat authorization before any Caller read. Its ServiceChecked declaration and exact inventory entry preserve public noncollaborator chat access without broadening the generic gate.
- ExpertChatInstanceService resolves the real owner, reads only the exact acting user's scoped ledger, and observes the registered UUID with health_check=True. Blank/malformed/no UUID means false/false without provider access. Registered released Caller remains exists=true; only a current ACTIVE device with health="true" yields alive=true. No shared Bot binding is substituted.
- Two owning Protocols document this read contract. No DI constructors or bindings change.
- BaaS get_bot adds keyword-only log_response=True; this query supplies False to reuse the existing response-log switch. This mode suppresses provider bodies and unsafe HTTP/transport exception text, while existing call defaults remain unchanged. Provider failure maps safely to HTTP 502 rather than false status.
- Owning ExpertChat README documents scope, exact boolean meanings, health observation and no-provision behavior.

## Verification

- TDD red: new absent-instance test failed with missing get_caller_instance_status AttributeError before implementation; 8 absent/malformed variants then passed.
- Affected service/HTTP/BaaS/authorization suites: **308 passed**, no failures or skips.
- API conformance, Protocol-only API and module boundary suites: **233 passed**.
- Measured runtime-module total line coverage: **790/902 = 87.58%** on the focused five modules, not the complete repository denominator. Incremental coverage is recorded below from the final JSON artifact.
- New/changed-line flake8 (`F401,F811,F821,F822,F823,E203,E211,E265`): **0 findings**. Eight pre-existing findings remain in legacy re-exports and slice spacing; no unrelated cleanup performed.
- `git diff --check`: PASS.
- New or extended ExpertChat/HTTP source file sizes remain below 1,000 lines (application service 968, instance service 874, Bots router 927). Existing BaaS service is already above 1,000 lines; the narrowly scoped compatible option adds 17 lines and introduces no new size waiver.

## Boundary logging evidence

Inbound events: `caller_instance_status.request`, `.success`, `.failed`; fields include backend/inbound, operation, GET/route, subject/Bot/address, tenant/env, request/trace ID, result/error type, elapsed time and the two boolean outputs.

Outbound events: `caller_instance_status.provider.request`, `.success`, `.failed`; fields include baas/outbound, UUID, Bot/real owner/subject, engine, health-check flag, tenant/env, interpreted provider status/device health and duration. Failure events record exception type without credentials. No generic logging infrastructure is added.

Tests assert success/failure events and no raw credential sentinel. Real BaaS+httpx.MockTransport tests cover healthy success, release/404, HTTP 503, API error, malformed JSON and transport error, proving the lower read helper does not log complete provider payloads or secret-bearing exceptions in this mode.

## Delivery scope

No production URL was invoked. Deployment and live container creation/restart are outside this task. Commit, latest-dev rebase, GitHub PR and remote-check reporting belong to the main workflow; local checks do not substitute for remote CI.

## Final changed-line measurement

- Focused executable changed lines: **60/61 = 98.36%**.
- Artifact: `/tmp/caller-instance-status-coverage.json`; intersection of added Git diff lines (including untracked new source files) with coverage executed/missing lines; Protocol/declarative-policy files are not in this runtime-module measurement.
- `apps/backend/src/agentclaw/community/adapters/http/openapi_v1/bots/router.py`: 25/26; uncovered added executable lines: [202]
- `apps/backend/src/agentclaw/community/adapters/http/openapi_v1/bots/schemas_caller_instance.py`: 4/4; uncovered added executable lines: []
- `apps/backend/src/agentclaw/community/core/expert_chat/services/expert_chat_instance_service.py`: 24/24; uncovered added executable lines: []
- `apps/backend/src/agentclaw/community/core/expert_chat/services/expert_chat_service.py`: 7/7; uncovered added executable lines: []
- `apps/backend/src/agentclaw/community/core/service_bot/services/baas_service.py`: 0/0; uncovered added executable lines: []

## PR CI inventory correction
- Actual remote Backend found the pinned public route-count contract: new Caller read adds one explicit-user operation (298 to 299) and one Bot-path operation (226 to 227). Updated only those exact pins with a route-specific note; complete explicit-user-id suite passed 23 tests. No runtime changes or relaxed checks.


## CI follow-up: live Singlebox observation story

The first head's remote Singlebox artifact reported Expert Chat Core 565/936
(60.36%), below the unchanged 61.18% module threshold. Existing acceptance
stories never called the added read-only methods, increasing their real Core
denominator without exercising them.

Added one real-HTTP story in the existing registered Expert Chat acceptance suite:
`test_caller_instance_status.py`. It seeds a source Bot without any container,
uses a real gateway-signed public principal, and verifies absent allocation,
pending ledger without UUID, and registered UUID missing from BaaS (real 404 to
RELEASED). Each step asserts the exact two booleans and compares the full Caller
ledger snapshot, including timestamps, to prove no creation or mutation. It does
not call caller-connection or provision an engine. The manifest now explicitly
includes the new GET in its Router API denominator; no threshold or exclusion
changes were made.

Validation: manifest checker PASS; acceptance story collects as one live test;
changed-file flake8 and diff checks PASS; manifest/report checker plus current
HTTP regression **36 passed**. Live Singlebox execution remains the remote CI's
responsibility for this follow-up: no existing local shared services were
started or stopped. Healthy-device and upstream-error behavior remain covered by
the focused runtime/MockTransport tests, while this live story preserves real
provider behavior rather than replacing it.

## Complete public-contract audit
- Registered the new GET in the existing owner-addressed/non-stage exact inventory and added CallerInstanceStatus model documentation. Targeted suites28 passed; full OpenAPI adapter suite2505 passed,2 skipped,zero failed (83.21s). No production control flow changed. Prior acceptance-fix head Singlebox job130967813274 actually succeeded; final pushed head remains independently checked.

## Full Backend verification
- Added four registered real-DI/SQLite endpoint cases to the global framework; no baseline exemptions/mocking. Endpoint runner4 passed, framework128 passed, public schema/path/namespace27 passed. Entire Backend community suite31081 passed,40 skipped,zero failed/errors plus6 subtests passed (414.76s), four workers, no fail-fast, coverage disabled. Remote head-specific coverage remains independently measured.
