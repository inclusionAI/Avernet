---
agent: tc-code-reviewer
status: completed
created: 2026-11-12
iteration: 2
---

# Public OpenAPI independent code review

Code review verdict: **PASS**. Remote ACI/CI evidence remains **PENDING** until the orchestrator records actual PR/head/job results.

## Scope

Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/asc-caller-refresh-openapi-20261112`.
Baseline: GitHub `inclusionAI/Avernet`, `REL20261112` at `57bfa8c8f736e0a354ebdee0d6ee9890733dcdff` before the final fetch/rebase.
Reviewed working-tree additions to the existing Caller OpenAPI router/schema, explicit OWNER authorization and addressed application admission rows, the existing scheduled refresh Service API extension and public Noop implementation, endpoint framework cases and identity-count inventory updates. Corporate credentials, signing, physical target selection and installation stay in the separate OCB implementation.

## Review findings and disposition

- Fixed: initial new route lacked registered happy/error framework cases. The existing real endpoint runner now drives both successful owner dispatch and missing-Bot rejection.
- Fixed: explicit-user inventory counts updated by the exact new operation delta (298 to 299 identity consumers, Bot path count 226 to 227); no threshold or baseline exclusions were introduced.
- Fixed: initial endpoint fixture imported `unittest.mock.Mock`, failing the framework no-mock gate. It now binds an explicit recording implementation of the host-provided Service API through the existing DI seam. The fixture documents that it verifies the public adapter, authorization and replaceable port; actual ASC exchange/installation is independently tested in OCB. No gate exceptions or ignore changes.
- No remaining must-fix public source findings in the reviewed revision.

## Review dimensions

| Dimension | Verdict | Evidence |
| --- | --- | --- |
| API and authorization | PASS | New operation is flat in the existing Caller router. PublicAPIRoute reads Check(OWNER); ADMISSION uses GRANT_CHECKED_ADDRESSED_BOT and existing group grant dependencies. UserIdDep supplies authenticated/delegated actor; OwnerIdDep locator is resolved to the stable entity. No caller/env/owner/sandbox override body. |
| Business boundary | PASS | Adapter dispatches only the existing ScheduledCallerRefreshProtocol, with no corporate implementation import. The host service owns actual owner/handoff/target checks. |
| Unsupported profile | PASS | Noop implements the new abstract method and returns the stable provider-unavailable error; HTTP maps it to 501 instead of claiming successful installation. Existing Noop owner callback remains unchanged. Repository search found no other public concrete subclass requiring the new method. |
| Results and failures | PASS | Credential-free typed outcomes include counts, overall status and per-binding results. Partial failures remain explicit in data and operation logs. Unknown exceptions produce fixed safe 502 responses, known identity errors retain existing envelope handling. |
| Logging | PASS | New request/success/failure events contain operation, route, identity/trace fields, duration and projected results. Handler tests assert all error branches and sentinel exclusion. No raw SDK response/token is returned. |
| Static checks | PASS | Ruff on changed source/tests and git diff --check pass. |
| Architecture | PASS | Authorization/admission inventories passed in the independent combined architecture run; four initial cold-import failures were the reused interpreter lacking absolute PYTHONPATH in child processes, and all 18 tests in the affected three files passed after correcting the command. No source workaround. |
| Remote ACI | PENDING | Actual final PR/head/jobs must be reported separately; local narrow coverage is not repository ACI. |

## Independent executable evidence

All commands used the existing Python 3.12 interpreter at `/Users/helloworld/Desktop/codes/teamclaw/ocb/apps/backend/.venv/bin/python`, with `PYTHONPATH="$PWD/src:$PWD" DEPLOY_PROFILE=test` from the public `apps/backend` directory when subprocess imports matter.

- Final public HTTP fast gates plus Caller handler and Noop DI: **70 passed**, zero failures/skips, 22 warnings. Files: `framework/test_coverage_gate.py`, `adapters/http/openapi_v1/test_explicit_user_id.py`, `adapters/http/openapi_v1/test_schema_docs.py`, `core/bot_config_surface/test_no_handler_only_checks.py`, `adapters/http/openapi_v1/test_caller_identity_endpoints.py`, `di/test_scheduled_caller_refresh.py`.
- Real endpoint framework runner filtered to `caller-token and refresh`: **2 passed**, 1871 unrelated cases deselected. This invokes the real route and OWNER gate with a controlled host port implementation; merely importing endpoint registration files is not counted as execution.
- `framework/test_no_mock_in_endpoint_tests.py` and `framework/test_no_mock_on_world_get.py`: **23 passed** after fixture repair.
- Earlier authorization/admission + architecture combined run: **472 passed, 4 environment import failures**; corrected command reran all tests in the three affected files: **18 passed**. These outcomes are recorded distinctly rather than claiming a single final all-green full-suite run.
- Independent narrow coverage of the existing Caller router/schema and scheduled refresh contract: **137/140 executable lines = 97.86%**. All **49/49 measured added executable lines = 100%** were exercised. Three missing lines are pre-existing runtime-stage guards. Two new registry declaration lines are outside this narrow measurement, with their semantics verified by inventory tests.
- Evidence: `/tmp/asc-refresh-review-public.xml`, `/tmp/asc-refresh-review-public-junit.xml`.

No per-route application grant ASGI test was added by this review; grant enforcement was inspected against the real centralized dependencies and covered by the existing authorization/admission inventory suites. This report does not claim deployed integration, live AgentPass/BaaS acceptance or automatic scheduling. OCB's unchanged public gitlink requires later consumption of this companion public revision before this OpenAPI address is available there.
