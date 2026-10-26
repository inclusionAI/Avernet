# PR report: super-admin-instance-restart

- Source repo: inclusionAI/Avernet (GitHub); origin explicitly verified.
- Worktree: isolated Avernet checkout, fix-super-admin-instance-restart-20261022
- Topic/base: fix/super-admin-instance-restart-20261022 / REL20261023.
- PR: https://github.com/inclusionAI/Avernet/pull/3028 (OPEN). Reviews/checks: PENDING. Human comments: auto.
- Baseline: closest multi-instance and user-list E2E tests: 50 passed.
- OCB mirror/gitlink PR: waiting for source PR and mirror synchronization.

## Validation

- Red: new authorization expectations failed against the original implementation (19 failures, 54 passes).
- Green: affected HTTP, service, SQLite collaborator/revocation and correction tests: 110 passed.
- Ruff F rules and git diff --check: PASS. Modified Python files are below 1,000 lines.
- Full backend suite with coverage: running; no success claimed yet.
- Runtime source changes include safe request/result/error and authorization decision logs. Documentation-only edits need no runtime logs.

## Review and gate iteration 1

- Independent authorization review: no actionable findings.
- Source branch rebased onto origin/REL20261023 at 81a51d5dc19627e97d7405a7315bfa4f3c3527e2; rebase reported up to date. Only the task commit is above that base.
- Native python_sast_local.sh and secret scanner: PASS. An initial alternate Ruff invocation used an unsupported rule; native SAST was subsequently run successfully.
- First full run in a reused corp environment: 24,734 passed / 16 failed / 40 skipped. Six failures exposed the endpoint runner fixture scope / no-monkeypatch contract. Fixed endpoint seeds to use the existing synthetic super admin 100000. The real user-list endpoint runner now passes all 15 cases; no authorization dependency is mocked there.
- Remaining initial failures involved missing subprocess PYTHONPATH, missing pymysql, corp-only imports and bare-thread instrumentation. A clean uv sync --frozen community environment is running the full suite with explicit source PYTHONPATH. Those initial results are not reported as passing.
- Remote first-head checks: title, BCS, Engine, BaaS, Gateway, Sandbox-proxy passed; Backend/Singlebox running. No review or inline comments at inspection time.
- OCB gitlink: initial source SHA was verified fetchable from the internal mirror. Final source head will be verified again before pinning. OCB base 6aeadd84b4a06adfb3ff95e958b952086bbe723a currently pins f3de5a61da83d5435cc4ee82dd6ff507a6bed744; source release base additionally contains upstream #3023 (81a51d5dc1).
