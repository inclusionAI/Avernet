# Implementation report: failed Teclaw retry UPDATE

## Existing chain and scope

The online release/restart decision remains in UpgradeResolutionMixin. Existing runners still own ledger issuance, binding persistence and progress polling; BotBuildService still maps Teclaw upgrade to update_teclaw_bot. Only the FAILED recovery decision changes. STOPPED capability and explicit gone-target fallbacks are retained. No transport, schema, engine, OCB or gitlink changes.

## Changes

- FAILED candidates select UPGRADE even when the provider cannot rebuild STOPPED containers. ARCA already selected this outcome and keeps its behavior.
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
