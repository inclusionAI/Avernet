# `agentclaw.community.core.bot_overview`

Non-user overview of a Bot: engine-neutral fields assembled
here, engine-specific fields dispatched to the provider registered for the
Bot's engine.

## Context Boundary

```yaml
purpose: "Non-user Bot overview — engine-neutral status/model/skills (local/repo/center)/MCPs/last session time plus a discriminated per-engine payload, filled by per-engine providers."
provides:
  - "BotOverviewService"
  - "BotOverview / EngineSpecificPayload models"
  - "EngineBotOverviewProvider seam + registry"
consumes:
  - "BotRepository (Bot record: active_engine, status)"
internal_dependencies:
  - agentclaw.community.core.repository.protocols.bot    # repository contracts consumed by this module
  - agentclaw.community.core.workspace
```

### Change impact

Served at `GET /openapi/v1/bots/{bot_id}/overview` through the gateway, which
fronts it with an auth strategy that needs no user credential (the backend
still verifies the gateway-signed principal; the operation is admission
`OPEN`, authorization `NoCheck`, no `user_id`). Anything added to the models
or an engine payload is readable by any caller in the tenant who knows a Bot
id, so it must be non-user data: no owner identity, conversation
content, credentials or device identifiers. Session data is reduced to the
last session time (`SessionActivity.last_session_at`) so clients apply their
own policy, such as "any conversation in the last two weeks".

### Adding an engine

1. Add `<Engine>EnginePayload` to `payloads.py` and to `EngineSpecificPayload`.
2. Implement `EngineBotOverviewProvider` under `engines/`.
3. Register it for each persisted `active_engine` value in
   `di/modules/bot_overview_module.py`.

`aicoding` and `claude_code` share `AICodingBotOverviewProvider`, which is a
draft returning only the empty `AICodingEnginePayload`; its owner fills the rest.
