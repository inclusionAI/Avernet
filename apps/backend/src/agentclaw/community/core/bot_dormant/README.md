# bot_dormant

Personal managed-cloud Bot dormancy, recycling and reactivation. Teclaw,
desktop and service Bots are outside the managed dormant lifecycle.

## Context Boundary

```yaml
purpose: "Personal Bot dormant scanning, recycle safety and reactivation admission."
provides:
  - BotDormantRecycleOperationProtocol
  - BotDormantRecycleServiceProtocol
  - BotDormantActivateServiceProtocol
  - RecycleOperationService
  - RecycleHandler
  - RecycleTaskLifecycle
  - RecycleRuntime
  - assert_not_recycling
  - ActivateBotService
  - DormantBotService
consumes:
  - PassportPlugin
  - DatabasePlugin
  - CachePlugin
  - HttpClient
  - BotRepository
  - BotRecycleRepositoryProtocol
  - RestartBackupDependencies
  - TaskQueueService
internal_dependencies:
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.common_config
  - agentclaw.community.core.repository.protocols.bot
  - agentclaw.community.core.task_queue
  - agentclaw.community.kernel.lifecycle
  - agentclaw.community.plugin_api
  - agentclaw.community.log
  - agentclaw.community.utils
```

### Change impact

OpenAPI uses durable `bot.recycle` submission and its own read-only operation
query. Existing internal/cron recycling retains the synchronous contract but
must respect pending OpenAPI recycle. Bot runtime status is not an operation
phase: backup failure leaves the serving Bot ACTIVE. Unconfirmed destruction
keeps the journal protected as NEEDS_CONFIRMATION. Completion/audit atomicity
belongs to the recycle repository, not the HTTP adapter. Restore preserves
existing asynchronous provisioning, with atomic admission and conditional
rollback. Changes affect Bot lifecycle conflict checks, provider release proof,
DI worker registration and the Gateway's scoped public schema pin. See
`apps/backend/docs/openapi-v1/README.md` and
`docs/plans/2026-11-07-openapi-recycle-async.md` from repository root for the
contract, rollout ordering and limits; no new table is required.
