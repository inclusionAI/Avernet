# Implementation report: failed Teclaw retry UPDATE

## Existing chain and scope

The online release/restart decision remains in UpgradeResolutionMixin. Existing runners still own ledger issuance, binding persistence and progress polling; BotBuildService still maps Teclaw upgrade to update_teclaw_bot. Only the FAILED recovery decision changes. STOPPED capability and explicit gone-target fallbacks are retained. No transport, schema, engine, OCB or gitlink changes.

## Changes

- The common decision delegates FAILED/STOPPED recovery to ProviderBehavior.should_upgrade_not_live_bot(status). Its default preserves the existing capability property, including third-party non-recovering providers. Only Teclaw overrides FAILED to UPGRADE; STOPPED and ARCA outcomes remain unchanged.
- Existing diagnostic event `[PublishFlowService._decide_online_deploy]` retains candidate UUID, status, provider and decision; message now says `candidate recovery` instead of assuming the container is not live. No credentials or external request fields are added.
- Provider capability comments and owning service-bot README distinguish operation failure from stopped-container recovery.
- Existing state/provider matrix updates Teclaw FAILED; new online retry/restart tests exercise real orchestration and SQLite operation ledger, assert upgrade issuance, unchanged target/binding, no retire/create and diagnostic fields. Existing build routing test verifies update_teclaw_bot.

## Validation

- RED: matrix before implementation failed only teclaw/FAILED (1 failed, 11 passed).
- GREEN: publish-flow and new regression files: 185 passed.
- Full service-bot domain: 1685 passed, 136 warnings, 44.86s, four workers.
- Ruff E4/E7/E9/F on changed production files and new test: passed.
- git diff --check: passed. Production files: 247 and 357 lines, below 1000.
- No external-call implementation changed, so request/response logging remains in the existing integration boundary rather than introducing unrelated wrappers.

## Provider-policy review revision (2026-11-14)

Removed the common FAILED override. Added a non-abstract status-aware method with a backward-compatible default; Teclaw owns the specific FAILED retry rule. Red-phase custom-provider regression proved that the old common condition overrode an existing False capability; 5 tests failed before implementation. After the revision, the four affected test files passed 235 tests. The existing decision diagnostic continues to record candidate UUID/status/provider/decision, so provider selection remains observable without adding transport logging or a new external boundary.

Revision full service-bot validation: 1691 passed, 176 warnings in 51.04s. Independent review PASS with 235 related tests. Provider behavior file focused statement coverage 117/119. New source file sizes: provider_behavior.py366 lines; upgrade_resolution_mixin.py244 lines. Ruff and diff whitespace validation passed.
