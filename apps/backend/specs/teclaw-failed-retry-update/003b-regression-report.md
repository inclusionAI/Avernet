# Regression report

PASS: targeted publish-flow tests and full service-bot domain regression.

Commands run from apps/backend with a fresh public-only `uv sync --frozen` environment:

- `DEPLOY_PROFILE=test uv run --no-sync pytest tests/community/core/service_bot/services/test_teclaw_failed_retry_update.py tests/community/core/service_bot/services/test_publish_flow_service.py -q --disable-warnings`: 185 passed.
- `DEPLOY_PROFILE=test uv run --no-sync pytest tests/community/core/service_bot -q -n 4 --disable-warnings --cov=agentclaw.community.core.service_bot.services.publish_flow.upgrade_resolution_mixin --cov-report=term-missing --cov-report=xml:/tmp/teclaw-failed-update-coverage.xml`: 1685 passed, 136 warnings in 44.86s.

New tests cover failed online retry and failed online restart reusing the same target, preserving binding, issuing upgrade and never retiring/creating; retry verifies diagnostic UUID/status/provider/decision and the returned workflow identifiers. Existing matrix and full domain protect STOPPED, STOPPING, gone-target fallback and ARCA behavior.

The focused coverage file has 47/74 executable statements covered (63.51%); uncovered statements belong to the pre-existing verify candidate resolver. This is not a repository-wide total coverage claim or a remote gate result. Actual remote checks remain separately recorded in 009-pr-report.md. No live deployment or production mutation was performed.

## Provider-owned policy revision (2026-11-14)

PASS for the revised source, independently of the previous PR head:

- Red phase: custom provider False+FAILED was incorrectly forced to UPGRADE; new provider methods did not yet exist (5 failures before implementation).
- Focused retry/provider/publish-flow/build-routing regression: 235 passed.
- Full service-bot domain, four workers: 1691 passed, 176 warnings in 51.04s.
- Focused coverage: provider_behavior.py 117/119 statements (98.32%); upgrade_resolution_mixin.py 47/74. Both new provider policy methods are exercised, including the inherited False policy and Teclaw FAILED/STOPPED outcomes. Combined 164/193=84.97%; this remains a focused local metric, not remote total coverage.
- Ruff E4/E7/E9/F and git diff --check passed. Production file lengths remain below 1000.

No engine code or external-call implementation changed; synthetic/in-memory domain regression applies. Existing online restart/retry diagnostics continue to expose the selected candidate, provider, status and decision.
