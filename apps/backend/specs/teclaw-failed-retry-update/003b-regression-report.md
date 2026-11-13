# Regression report

PASS: targeted publish-flow tests and full service-bot domain regression.

Commands run from apps/backend with a fresh public-only `uv sync --frozen` environment:

- `DEPLOY_PROFILE=test uv run --no-sync pytest tests/community/core/service_bot/services/test_teclaw_failed_retry_update.py tests/community/core/service_bot/services/test_publish_flow_service.py -q --disable-warnings`: 185 passed.
- `DEPLOY_PROFILE=test uv run --no-sync pytest tests/community/core/service_bot -q -n 4 --disable-warnings --cov=agentclaw.community.core.service_bot.services.publish_flow.upgrade_resolution_mixin --cov-report=term-missing --cov-report=xml:/tmp/teclaw-failed-update-coverage.xml`: 1685 passed, 136 warnings in 44.86s.

New tests cover failed online retry and failed online restart reusing the same target, preserving binding, issuing upgrade and never retiring/creating; retry verifies diagnostic UUID/status/provider/decision and the returned workflow identifiers. Existing matrix and full domain protect STOPPED, STOPPING, gone-target fallback and ARCA behavior.

The focused coverage file has 47/74 executable statements covered (63.51%); uncovered statements belong to the pre-existing verify candidate resolver. This is not a repository-wide total coverage claim or a remote gate result. Actual remote checks remain separately recorded in 009-pr-report.md. No live deployment or production mutation was performed.
