# Default CLI Review-Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:test-driven-development` for every behavior change below.

**Goal:** Repair the rejected Default CLI scope, validation, observability, and portable-installation test contracts without changing MCP aggregate or IAM behavior.

**Architecture:** `CliPassportScopeReconciler` is the one complete-snapshot writer: both Bootstrap and CLI caller/owner mutation read the historical AgentPass MCP+CLI scope through it. Passport normalizes legacy query data, but refuses malformed overwrite payloads. DaaS continues to consume only its colocated manifest and executes fixed argv installation commands.

**Spec:** `001-spec-output.md`; review inputs: `003-review-report.md`, `003b-regression-report.md`.

## Global Constraints

- Preserve AgentPass-only MCP identity values during every CLI overwrite.
- Accept only `owner` and `caller` CLI identity modes; normalize legacy queried items without a mode to `owner`.
- Do not alter `ac_bots.call_type`, MCP aggregate, or IAM runtime exchange.
- Keep logs low-sensitive: no token, credentials, raw Passport response, or agent code.
- Do not add an `ac_bot_cli_capability` table or CLI support to non-Default skillsets.

### Task 1: Shared scope and Passport boundary

**Files:**
- Modify: `src/agentclaw/community/plugin_api/passport.py`
- Modify: `src/agentclaw/community/core/mcp/services/cli_passport_scope.py`
- Modify: `src/agentclaw/community/core/caller_identity/service.py`
- Test: `tests/community/plugin_api/test_passport_resource_scope.py`
- Test: `tests/community/core/mcp/services/test_cli_passport_scope.py`
- Test: `tests/community/core/caller_identity/test_service.py`

- [x] Write a regression that updates a CLI while an AgentPass-only MCP remains `caller`.
- [x] Observe it fail when the caller service rebuilds MCP scope from local rows.
- [x] Route CLI mutation through the reconciler's complete AgentPass snapshot and force its update after sparse persistence.
- [x] Write and run invalid/missing/duplicate CLI overwrite payload tests, including legacy query normalization.
- [x] Implement fail-closed normalization at the Passport overwrite boundary and verify all scope tests pass.

### Task 2: Failure observability and installer portability

**Files:**
- Modify: `tests/community/core/caller_identity/test_service.py`
- Modify: `tests/community/core/mcp/services/test_cli_passport_scope.py`
- Modify: `tests/test_managed_cli_installer.py` in DaaS worktree

- [x] Write logger-capture regressions for caller requested/succeeded/failed/compensated events and no credential leakage.
- [x] Run them RED against the former missing-event behavior, then verify structured fields after the minimal implementation.
- [x] Replace the DaaS Backend-worktree absolute path assertion with a local artifact digest contract.
- [x] Add and run installer failure-log test with a secret-bearing input fixture; assert code/error category but not the secret.

### Task 3: Verification and documentation

**Files:**
- Modify: `001-spec-output.md`
- Modify: `002-code-report.md`
- Modify: `/Users/helloworld/Desktop/codes/teamclaw/log.md`

- [x] Record that phase one configures AgentPass authorization; target-engine principal consumption remains an E2E acceptance item.
- [x] Run focused Backend/DaaS tests, coverage for review-core modules, `ruff`, unused-import checks, and `git diff --check`.
- [x] Report exact RED/GREEN commands, coverage, and the unverified managed-binary path/E2E limitation.

### Task 4: All overwrite writers preserve external MCP identity

- [x] Add a pure complete-snapshot builder for MCP/CLI overwrite writers.
- [x] Route MCP sync, runtime projection and Default-CLI removal through it, retaining AgentPass-only MCP `caller` values unless a sparse override exists.
- [x] Add regressions for all three writer paths and loosen the endpoint scope assertion to membership rather than an incorrect one-MCP whole-list expectation.

### Task 5: R-09 low-sensitive writer observability and profile gate

- [x] Replace adjacent raw AgentPass exception logging/returns with stable `error_type`, branch/stage/status and duration fields; prevent HTTP exception chaining from exposing external failure text.
- [x] Add requested/succeeded/failed observability for MCP sync, runtime projection and Default-CLI remove writers, with secret-bearing query/update failure regressions.
- [x] Expose the manifest's exact phase-one profile gate through the reconciler and reject CLI sparse mutations for `aicoding` and `claude_code/normalCC` before any Passport query or persistence.
- [x] Run focused writer/caller suites and changed-line differential coverage.
