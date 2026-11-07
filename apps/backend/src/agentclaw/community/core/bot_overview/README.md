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
  - "TemplateRepository (per-Bot template snapshot row: ext carries template_config)"
internal_dependencies:
  - agentclaw.community.core.repository.protocols.bot    # repository contracts consumed by this module
  - agentclaw.community.core.workspace
  - agentclaw.community.core.bot_management.utils    # public template_config extractors + knowledge-key constants (DevFlow workflow, code-repo URLs); Yuque URL extraction is inlined in the aicoding provider
  - agentclaw.community.core.bot_management.capabilities
  - agentclaw.community.core.bot_management.services.aicoding.dima_workspace_capability
  - agentclaw.community.log
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

`aicoding` and `claude_code` share `AICodingBotOverviewProvider`. It fills
`AICodingEnginePayload` from the Bot record's coding-template fields:
`template_type` and `bot_type` from the record itself, and — from the
`template_config` snapshot, resolved the way every AICoding capability reads
it (Bot record top level, then `bot.ext.template_config`, then the template
repository row) — the template version, the template-factory identity, the
hosted-workspace capability flag, the DevFlow workflow name, and the
code-repo / Yuque knowledge-base URLs. It also fills the neutral
`default_model` from the snapshot's `model` field — the relay default model
the runtime consumes as `RELAY_DEFAULT_MODEL`. Credentials that ride along in
the snapshot (CodeFuse `token`, Yuque team tokens) are never surfaced. The
remaining engine-neutral fields (`skills`, `mcps`, `sessions`) stay at their
"nothing reported" defaults; the last-session source is an async BaaS call
behind this synchronous seam, so filling `sessions` is deliberately deferred.
