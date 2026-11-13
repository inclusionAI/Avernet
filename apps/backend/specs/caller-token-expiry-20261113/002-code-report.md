# Caller token expiry coordination — implemented and locally verified

- Added nullable UTC caller_token_expires_at, additive migration, repository serialization and expiry-aware runtime updater wired to both public Caller runtime ports.
- Earlier/equal token expiry skips outbound and expiry persistence; IAM success response is retained. Newer expiry records only after successful install.
- Instance identity changes (binding_id, bot_uuid, baas_publish_id), missing ext on upsert, and non-serving lifecycle writes invalidate expiry. Ordered MySQL assignments inspect the previous identity before replacing ext/status.
- Real SQLite transaction/concurrency tests, MySQL statement compilation, IAM application flow, public DI and existing shared runtime behavior are covered.
- Final command: DEPLOY_PROFILE=test .venv/bin/python -m pytest -q tests/community/repository/chat tests/community/core/caller_identity tests/community/core/expert_chat tests/community/api/test_caller_iam_token.py tests/community/di tests/community/architecture --tb=short --disable-warnings
- Result: 1661 passed, zero failed/skipped, 75.19s. Ruff for all 9 changed/added Python files and git diff --check pass. Source files remain below 1000 lines.
- Existing MySQL/OceanBase stores must apply core/expert_chat/sql/2026_11_13_caller_token_expiry.sql before deployment; SQL has not been executed against an external store. No live MySQL/OceanBase or runtime validation was performed.
- This change coordinates existing callers and introduces no independent scheduling loop. External install and database commit are not a distributed transaction; a successful install followed by failed commit can require a later retry.
- Local changes only: no commit, push, PR creation or deployment.
