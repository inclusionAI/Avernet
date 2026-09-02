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

## CI follow-up plan

The first GitHub Backend unit-test run reported 16 deterministic architecture and contract failures. Root-cause review found that they are all incomplete integration of the new CLI caller endpoint and Passport scope support, not a failed domain behavior assertion:

- mirror the existing MCP call-type operation in OpenAPI authorization, admission, route inventories, endpoint coverage, and user/stage address inventories;
- add the two new CLI domain errors to the shared HTTP status map and create the CLI configuration table in the isolated repository fixture;
- refresh the three intentionally changed response-schema snapshots;
- make the MCP sync resolver fixture return a complete Passport snapshot, keep the pure scope helper allow-listed at the HTTP boundary, and split the new scope/bootstrap orchestration out of the two modules exceeding the 1000-line architecture cap.

Validation after the fix will rerun the 16 failing gates first, then the affected Default-CLI suite and static checks before a normal commit and push.

## CI remediation result

- Restored the MCP-equivalent OpenAPI inventories and endpoint coverage for CLI call-type changes; CLI errors now use the shared HTTP status map and the isolated repository fixture creates the CLI call-config table.
- Regenerated only the three CLI-affected gateway snapshots. The original MCP sync error/seam remains available while its implementation is factored below the 1000-line cap; device bootstrap similarly depends on a narrow local protocol instead of a direct MCP service import.
- Local verification passed: focused CI contracts, architecture, device-bootstrap, Default-CLI, and caller-identity suite `334 passed`; `git diff --check`, bytecode compilation, and Ruff on the changed code passed. The duplicate callback rows found during the first static pass were removed in the second CI follow-up below.

## CI follow-up: second run

- GitHub Backend unit tests then found two remaining integration gaps: the generic endpoint runner seeded the CLI happy path without a phase-one engine, and the shared HTTP status-map module exceeded the 1000-line cap after the new CLI mappings.
- The endpoint seed now explicitly uses `openclaw`; duplicate callback map rows were removed while adding the CLI mappings, returning the module to the cap. The exact generic endpoint case and the oversized-module gate pass locally, along with Ruff and whitespace checks.

## Follow-up: legacy frontend CLI call-type route

- PR #1796 is merged and cannot receive the follow-up change.
- Source repository: GitHub `inclusionAI/Avernet`; base: `dev`.
- Scope: expose `PATCH /api/bots/{bot_id}/clis/{cli_code}/call-type` through the authenticated legacy frontend surface, add exact `ctoken` boundary sanitization, response schema, endpoint coverage, and frontend contract documentation.
- Security boundary: the actor is derived only from the authenticated request context; Bot ownership, CLI authorization, collaboration-lock fencing, persistence, and AgentPass reconciliation remain in the existing Caller Identity service.
- Local validation: the new route and compatibility middleware passed 9 tests; the related Caller Identity suite passed 64 tests; endpoint coverage gate, Ruff, bytecode compilation, and `git diff --check` passed.
- Rebase result: branch `rebase/legacy-cli-call-type-on-dev` contains only follow-up commit `c5226bf9b` above GitHub `dev@cb842b314`; the already-merged #1796 commits are not duplicated.
- Follow-up PR: [#1812](https://github.com/inclusionAI/Avernet/pull/1812), title `feat(backend): expose legacy CLI call-type updates`, head `rebase/legacy-cli-call-type-on-dev`, base `dev`.
- PR description sections: Problem / Solution / Validation / Compatibility and risk / Spec. Human comments mode: `auto`.
- Initial remote state: OPEN, review required, no comments or reviews; eight CI checks are queued or in progress and therefore remain `PENDING`.
