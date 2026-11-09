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
consumes:
  - PassportPlugin
  - DatabasePlugin
  - CachePlugin
  - HttpClient
  - BotRepository
  - BotRecycleRepositoryProtocol
  - EngineProvisioningStrategy
  - RecycleServices
internal_dependencies:
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.common_config
  - agentclaw.community.core.repository.protocols.bot
  - agentclaw.community.kernel.lifecycle
  - agentclaw.community.plugin_api
  - agentclaw.community.log
  - agentclaw.community.utils
```

### Change impact

The shared service selects an engine strategy, just like restart. Default engines
keep synchronous recycling and its existing audit; AICoding alone owns durable
`bot.recycle` admission, backup policy and the dedicated operation query. The
shared module must not import `engines.aicoding`, interpret fastdisk configuration,
manage backup contexts, or inspect provider release progress. Its dependencies
are the engine-neutral strategy interface and lazy `RecycleServices` envelope.

AICoding backup failure does not mark the serving Bot FAILED. Its unresolved
release vetoes conflicting operations through the strategy's lifecycle hook.
Completion/audit atomicity belongs to the recycle repository, not the HTTP
adapter. Activation preserves existing provisioning and guarded claims/rollback.
See `apps/backend/docs/openapi-v1/README.md` and
`docs/plans/2026-11-07-openapi-recycle-async.md` from repository root for contract,
rollout ordering and limits; no new table is required.
