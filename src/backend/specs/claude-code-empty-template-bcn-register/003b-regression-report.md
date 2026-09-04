---
agent: tc-engine-regression
status: completed
created: 2026-09-04
execution: controller-fallback-after-regression-agent-timeout
---

# Local Regression Report

## Scope

- Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/fix-claude-code-empty-template-bcn-register`
- Base: `github/dev@6ecb42630227da0a2030a051659312ac88dea86c`
- Changed behavior: legacy `claude_code` BCN eligibility for missing
  `template_type` values (`None` and `""`).

## Test results

### Focused BCN behavior

```bash
uv run pytest -q \
  tests/community/core/bot_management/services/test_bot_service_create_bcn_register.py \
  tests/community/core/bot_management/services/test_bot_service_stop_start.py \
  tests/community/core/bot_management/services/test_bot_service_restart_idempotency.py \
  tests/community/core/bot_management/services/test_bot_service_misc.py \
  -k 'bcn or BCN or should_register_bcn_provider'
```

Result: `50 passed, 158 deselected, 0 failed`.

### Complete affected test files

```bash
uv run pytest -q \
  tests/community/core/bot_management/services/test_bot_service_create_bcn_register.py \
  tests/community/core/bot_management/services/test_bot_service_stop_start.py \
  tests/community/core/bot_management/services/test_bot_service_restart_idempotency.py \
  tests/community/core/bot_management/services/test_bot_service_misc.py
```

Result: `208 passed, 0 failed`; 17 existing Pydantic deprecation warnings.

## Static checks

- `git diff --check`: PASS.
- Ruff on changed files with the unrelated baseline `F401` excluded: PASS.
- Running Ruff on the exact unmodified `github/dev` `bot_service.py` reproduces
  `McpSyncProtocol` unused at line 57. This is a pre-existing base issue and no
  new import or unused variable was introduced by this change.

## Behavior matrix

| active_engine | template_type | expected | evidence |
|---|---|---:|---|
| claude_code | normalCC | register | existing create/start tests pass |
| claude_code | None | register | new create test and updated start test pass |
| claude_code | empty string | register | new parameterized create test passes |
| claude_code | applicationCoding | do not register | existing create/start tests pass |
| claude_code/aicoding | personalCoding | register | existing tests pass |
| declared capabilities | capability result | authoritative | early-return implementation unchanged and existing tests pass |

## Conclusion

**PASS** — the relevant behavior and complete affected test files pass. Formal
remote ACI/CI and coverage gates remain pending until the branch is pushed and a
PR exists.
