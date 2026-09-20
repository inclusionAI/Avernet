---
agent: tc-code
status: implemented_pending_integration
created: 2026-09-20
iteration: 1
---

# Database and API implementation

Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/service-bot-publish-ignore-ops-rel20260917`

Branch: `feat/service-bot-build-ignore-db` (no commit, branch switch or deployment performed).

## Changes

- Neutral build-ignore values and literal relative-path normalization.
- Independent `ac_bot_build_ignore` model, schema bootstrap import and additive MySQL/OceanBase DDL.
- Tenant-aware repository with length-prefixed hashed key, bounded CAS retry, idempotent add/remove and monotonic revisions. Empty rows remain after final removal.
- Management service authorizes the actual Bot, resolves its registered runtime build plan, and manages DB configuration without contacting Engine.
- New flat GET/POST `/api/service-bot/publish/ops/build-ignore`. POST rejects stage/version/client engine fields. Existing instance endpoints unchanged.
- DI for repository and service; BotBuildService receives repository/environment (build worker owns constructor changes).
- Protocol/concrete conformance registration and context boundary documentation.

## Verification

TDD red: new repository/normalization tests initially failed because modules did not exist (12 failures, 2 setup errors). Green: 14 passed initially; additional tenant/limit/invalid-operation cases added.

Latest completed command combined new repository/router tests with repository, Service API and module-boundary architecture gates: **187 passed**, 17 existing dependency warnings.

New files passed Ruff F checks; new files only were formatted. No existing source file was fully reformatted.

Application service tests and real endpoint round-trip remain for main-agent integration: the shared `build_ignore_rules.py` validation helper was not yet written by the parallel build worker when this subtask finished. Real endpoint already reached the new service and failed with sanitized ModuleNotFoundError, not a routing/DI failure.

## Logs and isolation

Service logs structured request/response/failure with request ID, Bot/entity/operator/engine, method, operation, normalized path, revision, changed and elapsed time. Large response lists are summarized in logs only. SQL/driver exception messages and transport request headers are not logged. Router confidentiality tests cover secret-bearing exceptions. SQLite tests cover env/entity/bot/engine isolation, two explicit tenants, and concurrent first writes (4 threads, 8 distinct rules, no lost updates).

## Integration notes

- Shared helper: `services.build_ignore_rules.validate_required_paths(paths, plan)` must raise a safe ValueError containing required-path conflict.
- Repository import: `core.repository.protocols.build_ignore.BuildIgnoreRepositoryProtocol`; `get(*, env, entity_id, bot_id, engine_type)` returns immutable `kernel.build_ignore.BuildIgnoreConfig | None`.
- Main agent owns endpoint registration/real HTTP tests, overall docs, work log, remaining coverage and full regression.
- Apply additive SQL before deployment where automatic schema bootstrap is disabled. No runtime ignore-file migration is performed.
