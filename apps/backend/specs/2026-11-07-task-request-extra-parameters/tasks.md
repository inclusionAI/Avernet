# Tasks

- [x] Confirm owner approval and exclude all BBS changes.
- [x] Add request-contract coverage and reproduce unknown-field rejection.
- [x] Adjust request-only DTO policies, leaving extensions and responses unchanged.
- [x] Update HTTP integration expectations and identity/generation tests.
- [x] Synchronize reviewed contract docs and complete local verification.

## Verification
- Test-first run reproduced unknown-key rejection before the DTO policy change.
- Final combined task HTTP (internal and OpenAPI), artifact, semantic integration
  and task service suites: **1086 passed, 2 skipped**, 18 existing deprecation warnings.
  Both skipped tests need live OpenAPI credentials and were not executed.
- BBS tests ran unchanged as part of the HTTP suites; no BBS file has a diff.
- Changed/new Python files passed Ruff. `git diff --check` passed.
- Modified production Python files remain below 1,000 lines (largest: 993).
- No live task execution, deployment, commit, push, Skill sync or CI trigger.
