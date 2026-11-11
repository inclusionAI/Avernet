# `agentclaw.community.core.bot_overview`

Non-user overview of a Bot: engine-neutral fields assembled
here, engine-specific fields dispatched to the provider registered for the
Bot's engine.

## Context Boundary

```yaml
purpose: "Non-user Bot overview — engine-neutral status/model plus platform-owned skills (local/repo/center)/MCPs/last session time, and a discriminated per-engine payload filled by per-engine providers."
provides:
  - "BotOverviewService"
  - "BotOverview / EngineSpecificPayload models"
  - "EngineBotOverviewProvider seam + registry"
  - "BotSessionActivityReaderProtocol + EngineSessionActivityReader (last session time)"
consumes:
  - "BotRepository (Bot record: active_engine, status)"
  - "TemplateRepository (per-Bot template snapshot row: ext carries template_config)"
  - "BotCapabilityStateReaderProtocol (the Bot's active Skill assets)"
  - "EffectiveMCPStateReaderProtocol (the Bot's runtime-equivalent MCP server codes)"
  - "EngineRuntimeRelayProtocol (engine-neutral GET /api/sessions on the Bot's runtime)"
internal_dependencies:
  - agentclaw.community.core.repository.protocols.bot    # repository contracts consumed by this module
  - agentclaw.community.core.workspace
  - agentclaw.community.core.bot_management.addressing    # stable resource owner locator for Skill Center reads
  - agentclaw.community.core.skill_center    # active Skill assets (capability state contract)
  - agentclaw.community.core.skills_pool    # runtime Skill mapping intent → Skill summaries
  - agentclaw.community.core.mcp    # effective MCP state reader port
  - agentclaw.community.core.engine_runtime    # relay to the Bot's engine for the last session time
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

### Platform-owned fields

`skills`, `mcps` and `sessions` are filled by `BotOverviewService` for every
engine; providers cannot report them (`EngineBotOverview` has no such fields).

- **Skills** — `BotCapabilityStateReaderProtocol.active_skill_assets`, projected
  through the runtime mapping intent (`skills_pool.mapping_intent`), so the
  overview names exactly the Skills the runtime receives, with exact Center
  Versions. Like every effective read, it first synchronizes the Bot's
  Installation rows.
- **MCPs** — `EffectiveMCPStateReaderProtocol.effective_mcp_server_codes`:
  installed MCPs, Skill MCP dependencies and platform defaults minus
  exclusions — the same set runtime projection delivers. Codes only; per-Bot
  MCP configuration (URLs, tokens) is never read.
- **Sessions** — the newest `gmt_modified` from the engine adapter's
  engine-neutral `GET /api/sessions`, forwarded by the engine-runtime relay
  with the Bot's own Owner as caller (draft runtime; a `service` Bot's online
  runtime). Only that timestamp leaves the module. An unreachable runtime
  (no device, stage not live, engine error, or the 8 s budget) reports `null`
  rather than failing the overview. Pages are 5 rows because OpenClaw
  fetches chat history for every returned row; up to 4 pages are scanned,
  stopping at the first that yields a time, because Claude Code drops BCS
  group sessions after paging and can return an empty page while older
  sessions exist.

Skill Center read failures propagate: reporting an empty Skill set for a
failed read would misstate the Bot.

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
platform-owned fields above are not the provider's concern.
