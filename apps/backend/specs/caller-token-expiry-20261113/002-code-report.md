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

## Teclaw exclusion (2026-11-15)
- Existing IAM target selection, scope and session validation remain unchanged. The expiry updater checks the actual binding provider and scoped Bot active engine using existing constants. Teclaw takes the original runtime delegate path without reading/writing the expiry transaction scope, including legacy baas bindings.
- Enterprise ASC refresh returns before candidate enumeration/probe/issuance/install for a Teclaw engine, and defensively skips Teclaw-provider candidates on other Bots. Both owner callback and OpenAPI share this service.
- Diagnostic events: caller_token_expiry_bypassed (bot_id, stage, binding_id, engine, device_provider, reason); scheduled_caller_refresh_skipped (bot/entity/env/engine or existing safe target fields, reason=teclaw_scheduling_unsupported). Credentials are never logged.
- Public expiry+DI regression: 36 passed in 0.86s. Includes real ledger unchanged while shorter-token delegate still called for draft/verify/online, provider Teclaw/legacybaas engineTeclaw, and lookup failure propagation. Enterprise focused+DI+real-ledger coordination: 54 passed in 1.12s. Ruff and git diff --check pass. Enterprise full suite in progress; results recorded separately.
- For paired local enterprise testing only, two modified public files (expiry_runtime_updater.py and caller_identity_module.py) were copied into the existing nested checkout. Its HEAD/gitlink remains367cf8b2c2; this is an uncommitted source overlay, not delivery. No commit/push/deploy/external SQL performed this round.
- Final enterprise full regression: 3307 passed, zero failures/skips in67.37s (/tmp/teclaw-expiry-corp-full-tests.log). Independent public architecture checks:426 passed (root). Independent reviewer:PASS. Public focused36PASS and enterprise focused54PASS. Source remains local/uncommitted.
