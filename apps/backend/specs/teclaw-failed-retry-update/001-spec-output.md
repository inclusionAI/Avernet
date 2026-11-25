# Retry failed Teclaw publications through UPDATE

## Requirement and acceptance

A Teclaw BaaS candidate in `FAILED` must be reused through the existing UPGRADE/UPDATE path when an online publication is retried. `FAILED` describes a failed operation and does not establish that the provider instance is gone. Do not retire the candidate, release its binding or create a replacement merely because its status is FAILED.

The shared online decision also applies to online restart: a FAILED Teclaw restart must reuse the existing target through UPDATE. STOPPED Teclaw instances retain retire-then-create; ARCA decisions, verify routing, empty/error status handling, and explicit BOT_NOT_FOUND/DEVICE_NOT_FOUND fallbacks remain unchanged.

## Existing chain and boundaries

`execute_release_phase` / online `execute_restart` → `_decide_online_deploy` → existing upgrade runner → `BotBuildService.upgrade_async` → `update_teclaw_bot`. Candidate resolution keeps current-record binding precedence and falls back to the previous record only when no candidate is found.

Allowed changes: the shared decision, matching provider capability documentation, focused regression tests, owning service-bot README. No new API, schema, external-call wrapper, cleanup, provider enumeration or engine change. No OCB gitlink or worktree. Existing provider calls and their error semantics stay intact.

## Plan and validation

1. Change the existing decision matrix expectation and add orchestration regressions; observe failure before implementation.
2. Delegate FAILED/STOPPED recovery decisions to the selected ProviderBehavior. A backward-compatible default method follows the existing recovery capability; only Teclaw overrides FAILED to UPDATE. Keep stable diagnostic fields (candidate UUID, status, provider, decision).
3. Verify actual online retry and online restart reuse, preserve binding and avoid retire/create. Cover STOPPED/RELEASED/DESTROYING/STOPPING, missing status and ARCA behavior with existing tests.
4. Run focused service-bot tests, lint, diff/credential checks and independent review. Fetch and rebase onto latest GitHub dev, then create the GitHub source PR and inspect actual CI/reviews.

## Review and QA

Reject any path that changes STOPPED to UPDATE or treats transport errors as missing instances. Diagnostics must expose FAILED/teclaw/upgrade and candidate UUID without credentials. UPDATE acceptance is not final publish success: existing progress polling/callbacks remain authoritative. Live deployment is outside this task; tests use synthetic services and an in-memory operation ledger.

## Review revision: provider-owned policy (2026-11-14)

The common decision must not force every provider's FAILED state to UPGRADE. Add a non-abstract status-aware method on ProviderBehavior whose default preserves `upgrade_recovers_not_live_bot`. Override the method in Teclaw only: FAILED reuses UPDATE and STOPPED retains False. Third-party providers inheriting the base method keep their existing capability, including False+FAILED retiring rather than being silently upgraded. The old property remains compatible. Add a custom non-recovering provider regression before implementation and provider contract tests for FAILED/STOPPED.
