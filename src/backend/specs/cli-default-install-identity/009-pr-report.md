---
agent: tc-pr
status: in_progress
created: 2026-09-01T00:00:00+08:00
---

# Default CLI Passport Scope PR Report

## Change scope

- Feature branch before rebase: `feat/cli-default-install-identity`.
- Target branch: `origin/dev` at `14e6b4ed709053a1fe3489a0217231cef75aad59`.
- Planned rebased branch: `rebase/cli-default-install-identity-on-dev`.
- Repository: `inclusionAI/Avernet`.

## Rebase result

- Rebased successfully without conflicts onto `origin/dev` at `14e6b4ed709053a1fe3489a0217231cef75aad59`.
- Rebased feature commit: `701a05932d5390f649f8021211f7e9d17194c714` (`feat(backend): manage default CLI passport scopes`).
- The rebased branch contains `origin/dev` as an ancestor and no unrelated commits.

## Validation

- The focused Backend suite covering CLI manifest/scope reconciliation, MCP scope synchronization, caller identity, device bootstrap, Default skill-set projection, Passport scope validation, and HTTP contracts passed: `374 passed`.
- Ruff, bytecode compilation, and `git diff --check` passed before commit preparation.
- The same focused Backend suite passed again after the rebase: `374 passed`.

## PR status

Created: [#1796](https://github.com/inclusionAI/Avernet/pull/1796)

- Title: `feat(backend): manage default CLI passport scopes`.
- Base: `dev`.
- Head: `rebase/cli-default-install-identity-on-dev` at `580db7e19c49adce2e684449a7edb093bdac2f74` when the PR was created.
- Initial GitHub checks: eight required suites started and were `IN_PROGRESS` (Backend, BCS, Engine, BaaS, Gateway, Sandbox-proxy, Singlebox coverage, and BCS E2E).
- PR is open and awaiting both CI completion and review. No merge, deployment, or force-push was performed.
