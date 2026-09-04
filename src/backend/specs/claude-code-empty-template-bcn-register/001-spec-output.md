---
agent: tc-review
status: approved
created: 2026-09-04
task: claude-code-empty-template-bcn-register
source: user-approved-bounded-design
---

# Claude Code Empty Template BCN Registration Compatibility

## 1. Requirement

`BotService._should_register_bcn_provider()` currently registers legacy
`claude_code` bots only when `template_type == "normalCC"` (or when the
personal-coding condition applies). Historical `claude_code` bot rows may have
`template_type` stored as `NULL` or an empty string. These two missing-value
forms must be treated as equivalent to `normalCC` for BCN Provider registration.

## 2. Existing flow and boundaries

### Existing flow

- `create_bot`, `start_bot`, and BaaS restart paths call the centralized
  `_should_register_bcn_provider()` predicate.
- A template-factory snapshot with explicitly declared capabilities is the
  authoritative source and returns before legacy fallback evaluation.
- Legacy fallback currently recognizes `claude_code + normalCC`, coding-personal
  templates, `teclaw`, and `openclaw` service bots.

### Allowed changes

- `src/backend/src/agentclaw/community/core/bot_management/services/bot_service.py`
  - Update only the legacy `claude_code` standard-template predicate.
  - Update nearby documentation to describe `None`/empty compatibility.
  - Add a stable non-sensitive diagnostic log when the compatibility fallback
    for a missing `template_type` is used.
- BCN-focused tests under
  `src/backend/tests/community/core/bot_management/services/`.
- Task reports under
  `src/backend/specs/claude-code-empty-template-bcn-register/`.

### Forbidden changes

- Do not change declared template-factory capability precedence.
- Do not make `applicationCoding` or other non-empty template types eligible.
- Do not change BCN registration RPC payloads, DRM behavior, exception
  semantics, repository schemas, routers, device allocation, or unrelated code.
- Do not log tokens, credentials, headers, user content, or serialized objects.

## 3. Coding Spec

1. Add a failing regression test proving `active_engine="claude_code"` with
   `template_type=None` is eligible.
2. Add a failing regression test proving the empty string is eligible.
3. Preserve `normalCC` eligibility and `applicationCoding` ineligibility.
4. Verify at least the `create_bot` entry path invokes BCN registration for a
   missing template type when the DRM gate is enabled.
5. Implement the minimum centralized predicate change. Whitespace-only values
   are not considered empty and remain ineligible.
6. Emit one stable diagnostic event for the compatibility branch, containing
   only engine/template classification fields and no bot/user/token data.

## 4. Review Spec

- Confirm the capabilities-first return is unchanged.
- Confirm only `None`, `""`, and `"normalCC"` are accepted by the standard
  `claude_code` legacy branch.
- Confirm personal-coding, `teclaw`, and `openclaw` service behavior is unchanged.
- Confirm logging contains no sensitive values and is not added to unrelated
  paths.
- Confirm no unused imports/variables and `git diff --check` passes.

## 5. QA / Regression Spec

Run from `src/backend`:

```bash
uv run pytest -q \
  tests/community/core/bot_management/services/test_bot_service_create_bcn_register.py \
  tests/community/core/bot_management/services/test_bot_service_stop_start.py \
  tests/community/core/bot_management/services/test_bot_service_restart_idempotency.py \
  -k 'bcn or BCN'
uv run ruff check \
  src/agentclaw/community/core/bot_management/services/bot_service.py \
  tests/community/core/bot_management/services/test_bot_service_create_bcn_register.py
git diff --check
```

Acceptance criteria:

- All selected tests pass.
- New tests fail against the pre-change production predicate and pass after the
  implementation.
- `None` and `""` are equivalent to `normalCC` only in the specified legacy
  fallback.
- `applicationCoding` remains rejected.

## 6. Ship / PR Spec

- Source repository: `inclusionAI/Avernet` on GitHub.
- Base branch: latest remote `dev`.
- Topic branch: `fix/claude-code-empty-template-bcn-register`.
- Rebase the topic commit(s) onto the latest GitHub `dev` immediately before
  push.
- Push with `--no-verify` because this is a feature/fix topic branch.
- Create an English PR with non-empty Problem, Solution, and Validation sections.
- Observe reviews and GitHub checks. Fix deterministic code/test/lint failures;
  report infrastructure or permission failures as pending/blocked.
