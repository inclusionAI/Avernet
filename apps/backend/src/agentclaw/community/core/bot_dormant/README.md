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
  - ActivateBotService
  - DormantBotService
  - RecycleOperations
  - RecycleLifecycle
  - ManagedRecycleRuntime
consumes:
  - PassportPlugin
  - DatabasePlugin
  - CachePlugin
  - HttpClient
  - BotRepository
  - BotRecycleRepositoryProtocol
  - EngineProvisioningStrategy
  - RecycleServices
  - TaskQueueService
  - BaasRecycleRuntime
internal_dependencies:
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.common_config
  - agentclaw.community.core.service_bot
  - agentclaw.community.core.task_queue
  - agentclaw.community.core.repository.protocols.bot
  - agentclaw.community.kernel.lifecycle
  - agentclaw.community.plugin_api
  - agentclaw.community.log
  - agentclaw.community.utils
```

### Change impact

All supported personal managed-cloud Bots share durable `bot.recycle` admission,
read-only operation queries, physical release confirmation and guarded activation.
`recycle_task.py` is the single worker; `recycle_service.py` owns the facade and
pinned release runtime. Neutral journal/contracts live in the existing
`bot_management/engines/restart_contract.py`; no extra table or state machine is added.

Only the backup precondition dispatches an engine strategy. AICoding owns the
existing live-mount probe, helper, failure switch, receipt policy and backup budget;
default engines do none of those operations. Shared dormant code must not import
`engines.aicoding` or interpret its backup configuration. The older synchronous
`recycle()` remains for dormant scanning/ops; OpenAPI uses uniform async `submit()`.

Backup failure does not mark the serving Bot FAILED. Unresolved releases veto
conflicting lifecycle operations for every engine. Completion/audit atomicity
belongs to the recycle repository, not HTTP adapters. All three Bot types use the
same activation claim/rollback while preserving existing provisioning.
See `apps/backend/docs/openapi-v1/README.md` and
`apps/backend/docs/plans/2026-11-07-openapi-recycle-async.md` from repository root for contract,
rollout ordering and limits; no new table is required.
