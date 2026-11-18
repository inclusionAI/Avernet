---
agent: tc-engine-regression
status: completed
created: 2026-11-18
---

# BaaS Caller status independent local regression

## Scope and environment

- Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/baas-caller-status-20261118`.
- This change adds a Backend HTTP adapter and service-authentication wrapper; no Engine or Relay behavior changes. Per task scope, no engine/container/runtime was started or stopped. Shared services were untouched.
- Read specification, implementation report (in-progress whole-suite section), independent review report and the actual JWT, registered endpoint and live acceptance sources.

## Independent execution

From `apps/backend`:

```bash
DEPLOY_PROFILE=test PYTHONPATH=src:. /Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/caller-cron-sessions-20261112/apps/backend/.venv/bin/python -m pytest tests/community/api/expert_chat/test_app_caller_status.py tests/community/endpoints/test_endpoint_runner.py -k 'app-caller-instance or test_app_caller_status' -q --disable-warnings
```

Result: **42 passed, 0 failed, 0 skipped**, 1,985 deselected, 18 warnings, 1.55 seconds. This is exactly the new 38-case signed HTTP suite plus four actual registered DI/SQLite endpoint calls; deselected cases were intentionally outside this independent focused rerun. The complete Backend suite is independently owned by the coding agent and was not duplicated here.

## Observed behavior and assertions

- Real shared-key HS256 verification accepts `iss=baas`, requires time claims, rejects malformed/tampered/wrong-key/algorithm/issuer/expired/future credentials and unconfigured verifier before service work.
- All three query parameters are required and nonempty. The internal diagnostic operation name is not exposed as a query parameter.
- Successful responses contain exactly `data={exists, alive}`. Signed private-Bot observations for an unrelated Caller work without application grants or chat membership.
- Actual DI/SQLite cases preserve no-allocation and pending-registration state, reject missing credentials with 401 and do not turn a missing source Bot into successful absence.
- JWT identity/tenant/app/audience claims do not replace trusted ordinary server tenant/environment; the same BaaS service JWT remains rejected by the original public status endpoint.
- Domain and generic failure responses are unsuccessful and sanitized; upstream failure is never a successful false health observation.
- The new Singlebox story is declared in the router denominator and exercises unauthenticated denial followed by absent, pending and real-provider-404 observations. It compares the ledger snapshot including `caller_token_expires_at`; no runtime was available for an independent live run here, so this remains remote verification, not a local PASS claim.

## Boundary diagnostics

Verified status-specific `authentication_request`, `authenticated`, `denied`, `request`, `success` and `failed` assertions. Events use system/direction/operation/method/route/request ID, trusted tenant/environment, Bot/Owner/Caller, status/error code, duration and non-sensitive result booleans. Tests assert signing key, raw JWT, decoder-controlled text, credential sentinels and exception details are absent. Authentication and ordinary public-path failures do not invoke the status service.

The reused instance service keeps its existing outbound health logs and safe provider-response projection; this adapter adds no outbound transport or lifecycle operation. Source/domain tests explicitly prohibit create/upsert/update/upgrade/publish/identity-exchange calls; registered HTTP cases corroborate unchanged real ledger state.

## Coverage and remaining gates

- This independent 42-case rerun did not collect coverage. Review's local changed-executable-line evidence is **33/33 (100%)** for this round's two runtime files; it is not repository-wide coverage.
- Full local Backend behavior suite: still in progress in the coding report at this independent report's creation; final numbers belong to that owner.
- Full local Singlebox: NOT RUN; the task explicitly avoids setting up or mutating shared runtime services, and this worktree does not have the complete isolated stack dependencies.
- Final-head remote Backend/Singlebox and ACI coverage: **PENDING**, to be read from actual PR jobs. No thresholds, exclusions, assertions or baselines were weakened.

## Conclusion

**Local focused regression PASS. Remote ACI/CI PENDING.**
