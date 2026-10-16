# `agentclaw.community.core.acli_clis`

Manage the acli market CLIs a bot runs, through `/openapi/v1/bots/{bot_id}/clis`
and `/openapi/v1/bots/market/clis`. Spec:
`apps/backend/specs/2026-10-15-openapi-v1-acli-clis/`.

- **AgentPass (Passport) is the only record** of which CLIs a bot has; this
  module stores nothing of its own.
- **Add is all-or-nothing:** install in the bot's container, then grant. Every
  Passport write is read back, and only a confirmed grant succeeds; otherwise
  the install is undone.
- **Remove is all-or-nothing:** revoke, confirm the revoke, then uninstall. A
  failed uninstall re-grants with the same identity, and that is read back too.
- When an undo fails, or an outcome cannot be confirmed (timeout, unreachable
  engine, malformed reply, cancellation), the error names the state the bot was
  left in (`AcliRollbackFailedError.state`) and the event is logged.
- **The market search needs no container.** The catalogue is the same for every
  bot, so `AcliMarketSearch` calls the market API at the configured
  `acli_market.search_url`.

## Context Boundary

```yaml
purpose: Add, remove and list a bot's acli market CLIs all-or-nothing across Passport and the bot's container, and search the acli market.
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
  - "core.caller_identity guard — the owner, active-bot and edit-lock checks shared with the identity switch"
  - "BotRepository, BotCollabLockRepositoryProtocol, CallerIdentityRepositoryProtocol (core.repository)"
  - "DeviceAdapterTransport (plugin_api) — the hop to the engine's /api/cli/acli/* endpoints"
  - "HttpClient (plugin_api) — the general client, for the market's search API"
consumed_by:
  - "adapters/http/openapi_v1/bots/acli_clis.py — list, add and remove a bot's CLIs"
  - "adapters/http/openapi_v1/market/acli_clis.py — the market search"
internal_dependencies:
  - agentclaw.community.core.acli_clis
  - agentclaw.community.core.caller_identity
  - agentclaw.community.core.errors
  - agentclaw.community.core.mcp
  - agentclaw.community.core.repository
  - agentclaw.community.log
  - agentclaw.community.plugin_api
```
