---
agent: tc-code-reviewer
status: completed
created: 2026-11-18
iteration: 1
---

# BaaS Caller status code review

## Scope and boundary

- Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/baas-caller-status-20261118`.
- Base: GitHub `dev` at `d24f0e1ed2d240a54fe475d7acb03465948dbada`; reviewed the rebased prerequisite status implementation and this task's uncommitted frozen runtime/test diff. The final published head did not exist at review time.
- New BaaS route: `GET /api/v1/expert-chats/app-caller-instance/status`, required `bot_id`, `owner_id`, `user_id`, verified `X-Avernet-Principal` service JWT.
- Existing Public GET and global public identity/admission/grant policies retain their behavior. No production or container work was performed.
- Reviewed the `002-code-report.md` in-progress snapshot and independently reconstructed scope from git status/diff, the complete spec and actual new test files. Full Backend execution was still running; its final result belongs to regression/PR reports.

## Findings and resolution

1. Authentication request/success/denied events initially omitted trusted tenant/environment. The shared helper now records both from server context; the JWT decoder result is never projected as a tenant or identity set.
2. Replaying prerequisite #3914 on current dev made `authorization_rules.py` 1003 lines. The new route's policy declaration was compacted without changing its value; the file is now 1000 lines. The existing BaasService is a pre-existing explicitly allowed oversized module, not a new exemption.
3. Added a same-token negative HTTP assertion: a valid BaaS service JWT still receives 401 on the original Public status route. Added `caller_token_expires_at` to the live ledger snapshot projection.

No unresolved blocking code findings remain.

## Review checks

| Dimension | Result | Evidence |
| --- | --- | --- |
| Correctness | PASS | Router invokes existing instance read once; source Bot resolution and real Owner mapping precede exact Caller ledger read; no ledger UUID means false/false; provider failure remains a non-success response. |
| Service authentication | PASS | Existing shared decoder checks signature, HS256, issuer=baas and required temporal claims. Missing/malformed/forged/wrong-key/wrong-issuer/expired/future credentials fail before service work. The operation selector stays an internal helper argument, absent from HTTP query schema. |
| Scope isolation | PASS | Ordinary non-public middleware fixes the default server tenant; JWT/query claims cannot replace it. Repository uses current server environment and resource locator. Public access checks remain unchanged. |
| Read-only and engine boundary | PASS | Both routes reuse the same read-only instance method; no provisioning, restart, upgrade, publication polling, token exchange or ledger/binding write is reachable. No Teclaw shared runtime fallback or stage mutation is added. |
| Boundary logs | PASS | Status-specific authentication and operation request/success/denied/failed events contain scope, route/method, correlation ID, status, duration and safe result fields. Decoder/handler failures log exception class, not sensitive exception text; provider query retains safe payload suppression. |
| Architecture | PASS | Flat endpoint in existing ordinary router, Service API injection and existing domain read; no child router, new DI provider, generic identity framework or schema/lifecycle expansion. |
| Static hygiene | PASS | Independent ruff F401/F841/E9 checks passed for new runtime/test files; final `git diff --check` clean. Runtime files are 734/137 lines; authorization policy 1000; inherited BaasService uses its existing allowlist. |
| Test framework and acceptance | PASS / remote pending | Real DI/SQLite happy/error registry cases cover private unrelated Caller, pending record, auth refusal and absent Bot. Live acceptance covers no registration, pending, provider 404 and exact ledger preservation. Their remote execution remains a PR gate. |

## Independently executed verification

From `apps/backend`, with `DEPLOY_PROFILE=test PYTHONPATH=src:.` and the existing public Python environment:

- New signed-service HTTP suite: **37 passed** at first inspection.
- New BaaS status + existing app-caller-connection + existing Public status + instance-domain suites: **113 passed**. Independent coverage JSON mapped this task's added executable lines in the two runtime adapters to **33/33 (100%)**. The selected three modules' overall **214/564 (37.94%)** is a narrow diagnostic measurement, not repository coverage and not an ACI pass.
- Final same-token Public refusal, all new auth cases, source-size and authorization inventory suites: **75 passed**, zero failures (25.61 seconds).
- Independent ruff checks and diff hygiene: PASS.
- The implementation agent additionally reported complete OpenAPI/framework **2804 passed / 2 skipped**; this was not rerun wholesale by this reviewer. Full Backend was in progress when this report was written.

## ACI evidence

- Final published base/head: **PENDING**; working-tree review is not a remote job.
- Remote case pass: **PENDING**; threshold 100%.
- Remote total line coverage: **PENDING**; require the actual repository gate and skill minimum.
- Remote changed line coverage: **PENDING**; require >=90% per workflow without exclusions/threshold changes.
- Remote Singlebox: **PENDING**; existing ExpertChat thresholds unchanged and the new route is in its denominator.

## Verdict

**Local Code Review: PASS. Remote ACI/CI: PENDING.**

Continue independent regression and final-head PR checks. Do not interpret this local verdict as remote CI completion or deployment approval.
