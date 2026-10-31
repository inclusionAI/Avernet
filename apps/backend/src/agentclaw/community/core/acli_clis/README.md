# `agentclaw.community.core.acli_clis`

Manage the acli market CLIs a bot runtime runs, through `/openapi/v1/bots/{bot_id}/clis`,
`/openapi/v1/bots/{bot_id}/cli-operations` and `/openapi/v1/bots/market/clis`, and
the same operations on the internal `/api/bots/...` routes for authenticated users.
Spec: `apps/backend/specs/2026-10-21-acli-cli-operations/spec.md` (builds on
`apps/backend/specs/2026-10-15-openapi-v1-acli-clis/`).

- **Every add, remove and cancel is a task-queue task** (`tasks.py`). The API
  enqueues it and waits up to 30 s unless `async` is asked for; a task still
  running is answered `202` with the operation, read back by its id.
- **One add or remove per runtime.** A runtime is a bot's stage (`draft`, or a
  service bot's `verify` / `online`); its add and remove share the idempotency
  key `acli:op:{bot_pk}:{stage}`, its cancels `acli:cancel:{bot_pk}:{stage}`.
- **`ac_acli_operation`** links the operation id the API returns to its task and
  to the engine operation the task started. Status is the task's.
- **The engine is the source of truth for what is installed**: a runtime's CLIs
  are listed from its container. The Passport grant is written (and read back)
  before an install and never revoked, so installed in any stage ⇒ granted.
- **The market search needs no container.** The catalogue is the same for every
  bot, so `AcliMarketSearch` calls the market API at the configured
  `acli_market.search_url`.

## Context Boundary

```yaml
purpose: Add, remove, cancel and list a bot runtime's acli market CLIs as task-queue operations against the runtime's container, keeping Passport granted ahead of installs, and search the acli market.
provides:
  - AcliCliService
  - AcliCliServiceProtocol
  - AcliMarketSearch
  - AcliPort
  - ArcaAcliPort
  - BotCli
  - AcliTool
  - AcliMarketPage
consumes:
  - "CliPassportScopeReconciler (core.mcp) — the only writer of a bot's whole Passport CLI scope"
  - "CliCapabilityManifestResolver (core.mcp) — which CLIs are a bot's defaults"
  - "core.caller_identity guard — bot lookup and the edit-lock check shared with the identity switch"
  - "BotRepository, BotCollabLockRepositoryProtocol, CallerIdentityRepositoryProtocol (core.repository)"
  - "CollaboratorServiceProtocol (core.bot_collaborator) — who may read a bot's CLIs"
  - "DeviceAdapterTransport (plugin_api) — the hop to the engine's /api/cli/acli/* endpoints (teclaw: /api/v1/cli/acli/*)"
  - "TaskQueueService, HandlerRegistry, TaskQueueRepositoryProtocol (core.task_queue, core.repository) — the operations run as tasks"
  - "DatabasePlugin (plugin_api) — the ac_acli_operation rows"
  - "HttpClient (plugin_api) — the general client, for the market's search API"
consumed_by:
  - "adapters/http/openapi_v1/bots/acli_clis.py — list, add and remove a bot's CLIs"
  - "adapters/http/openapi_v1/market/acli_clis.py — the market search"
  - "adapters/http/acli_clis/router.py — the internal /api/bots routes for the same operations"
internal_dependencies:
  - agentclaw.community.core.acli_clis
  - agentclaw.community.core.base
  - agentclaw.community.core.bot_collaborator
  - agentclaw.community.core.bot_management.addressing # stable entity locator for the shared edit lock
  - agentclaw.community.core.caller_identity
  - agentclaw.community.core.errors
  - agentclaw.community.core.mcp
  - agentclaw.community.core.repository
  - agentclaw.community.core.service_bot
  - agentclaw.community.core.task_queue
  - agentclaw.community.kernel
  - agentclaw.community.log
  - agentclaw.community.plugin_api
  - agentclaw.community.utils
```

CLI writes resolve the Bot before checking its current Owner and stable
`bot_id:entity_id` edit lock. Owner handoff changes the authorized person, not
the lock address; an existing lock held by somebody else must still block writes.
