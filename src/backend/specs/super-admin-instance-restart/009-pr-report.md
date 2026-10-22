# PR report: super-admin-instance-restart

- Source repo: inclusionAI/Avernet (GitHub); origin explicitly verified.
- Worktree: isolated Avernet checkout, fix-super-admin-instance-restart-20261022
- Topic/base: fix/super-admin-instance-restart-20261022 / REL20261023.
- PR: NOT_CREATED. Reviews/checks: PENDING. Human comments: auto.
- Baseline: closest multi-instance and user-list E2E tests: 50 passed.
- OCB mirror/gitlink PR: waiting for source PR and mirror synchronization.

## Validation

- Red: new authorization expectations failed against the original implementation (19 failures, 54 passes).
- Green: affected HTTP, service, SQLite collaborator/revocation and correction tests: 110 passed.
- Ruff F rules and git diff --check: PASS. Modified Python files are below 1,000 lines.
- Full backend suite with coverage: running; no success claimed yet.
- Runtime source changes include safe request/result/error and authorization decision logs. Documentation-only edits need no runtime logs.
