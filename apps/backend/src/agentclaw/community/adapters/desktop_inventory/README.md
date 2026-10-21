# Desktop inventory adapter

## Context Boundary

```yaml
purpose: Translate desktop lifecycle transport failures into the inventory port's domain error contract.
provides:
  - DesktopInventoryAdapter
consumes:
  - DesktopBotInventoryPort
internal_dependencies:
  - agentclaw.community.core.bot_inventory.protocols
  - agentclaw.community.core.desktop_bot.errors
  - agentclaw.community.core.service_bot.baas_service_errors
```

### Change impact

The composition root binds this delivery adapter to `DesktopBotInventoryPort`.
Restart normalizes `BaasServiceError` and `httpx.HTTPError` (including status
errors and transport timeouts from the pool-layout update API) to the existing
`DesktopBotServiceError` contract. The inventory workflow therefore remains
transport-agnostic and maps known upstream failures to HTTP 502. Unknown
programming errors propagate, and other desktop operations are forwarded
without changing their behavior.
