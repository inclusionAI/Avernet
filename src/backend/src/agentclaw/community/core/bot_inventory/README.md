# Bot Inventory

Bot Inventory builds transport-agnostic display models and action policy for the
personal-cloud and local-bot public API surface.

## Context Boundary

```yaml
purpose: Aggregate personal cloud Bots and local desktop Bots into user-scoped inventory view models and action decisions.
provides:
  - BotInventoryItem
  - BotInventoryService
  - BotLifecycleView
  - LocalBotWorkflowService
  - BusinessSpaceContextProtocol
  - ServiceLifecyclePort
consumes:
  - BotInventoryBotPort
  - DesktopBotInventoryPort
  - BusinessSpaceContextProtocol
  - ServiceLifecyclePort
internal_dependencies:
  - agentclaw.community.core.bot_inventory
  - agentclaw.community.core.errors
  - agentclaw.community.core.workspace
  - agentclaw.community.plugin_api
```

### Change impact

Changes affect the public `/openapi/v1/bots/inventory` and
`/openapi/v1/bots/local` views that compose existing Bot management and desktop
Bot services.  The module does not own business-space membership, storage, or
CRUD; replacing the fallback space context with the business-space owner's
Service API changes filtering/visibility behavior without changing this core
boundary.
