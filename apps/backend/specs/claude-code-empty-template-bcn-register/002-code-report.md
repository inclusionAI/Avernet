---
agent: tc-code
status: completed
created: 2026-09-04
iteration: 1
source: controller-tdd-after-subagent-timeout
---

# Coding Report: Claude Code Empty Template BCN Registration

## Worktree

- Path: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/fix-claude-code-empty-template-bcn-register`
- Branch: `fix/claude-code-empty-template-bcn-register`
- GitHub base at creation: `dev@6ecb42630227da0a2030a051659312ac88dea86c`

## Existing chain and boundary

- Existing lifecycle callers (`create_bot`, `start_bot`, and BaaS restart) remain
  wired to the centralized `_should_register_bcn_provider()` predicate.
- Declared template-factory capabilities still return before the legacy fallback.
- No BCN RPC payload, DRM, repository, schema, router, device-allocation, or
  exception behavior changed.

## Changed files

1. `src/agentclaw/community/core/bot_management/services/bot_service.py`
   - Added `is_claude_code_standard` accepting only `None`, `""`, and
     `"normalCC"` for `active_engine="claude_code"`.
   - Preserved all other eligibility branches.
   - Added a non-sensitive compatibility diagnostic event.
2. `tests/community/core/bot_management/services/test_bot_service_create_bcn_register.py`
   - Replaced the obsolete missing-template rejection assertion with parameterized
     `None` and empty-string registration coverage.
   - Asserted the diagnostic event and normalized template-state field.
3. `tests/community/core/bot_management/services/test_bot_service_stop_start.py`
   - Updated the obsolete `start_bot` missing-template assertion to require BCN
     registration.

## TDD evidence

### RED

Command:

```bash
uv run pytest -q \
  tests/community/core/bot_management/services/test_bot_service_create_bcn_register.py \
  -k 'missing_template_type'
```

Result before production change: `2 failed, 18 deselected`. Both `None` and `""`
failed because `register_provider_bot` was called zero times.

### GREEN

Same command after the minimum production change: `2 passed, 18 deselected`.

## Regression and quality checks

```bash
uv run pytest -q \
  tests/community/core/bot_management/services/test_bot_service_create_bcn_register.py \
  tests/community/core/bot_management/services/test_bot_service_stop_start.py \
  tests/community/core/bot_management/services/test_bot_service_restart_idempotency.py \
  tests/community/core/bot_management/services/test_bot_service_misc.py \
  -k 'bcn or BCN or should_register_bcn_provider'
```

Result: `50 passed, 158 deselected`.

```bash
uv run ruff check --ignore F401 \
  src/agentclaw/community/core/bot_management/services/bot_service.py \
  tests/community/core/bot_management/services/test_bot_service_create_bcn_register.py \
  tests/community/core/bot_management/services/test_bot_service_stop_start.py
```

Result: PASS. An unmodified GitHub `dev` copy of `bot_service.py` independently
reproduces one existing `F401` at line 57 for `McpSyncProtocol`; this task does
not remove unrelated baseline code.

`git diff --check`: PASS.

## Diagnostic logging

- Event: `legacy_claude_code_missing_template_type`
- Logger context: `bot_service._should_register_bcn_provider`
- Fields:
  - `active_engine=claude_code`
  - `template_type_state=none|empty`
  - `fallback_template_type=normalCC`
- Sensitive fields: none. No bot ID, user ID, token, headers, credentials,
  request payload, or raw template object is recorded.
