# `agentclaw.community.core.bot_public`

Bot publication / discoverability domain — public bot listing, friend/relationship state, publish approvals.

## Context Boundary

```yaml
purpose: "Bot publication / discoverability domain — public bot listing, friend/relationship state, publish approvals."
provides:
  - "BotPublicService"
  - "BotDiscoverService"
  - "Bot-public SQLAlchemy models"
consumes:
  - "BotManagement repo + service"
  - "BotCatalogMetadata port (BCS search page or exact recommendation candidates, joined by (bot_id, entity_id))"
  - "AntProcess plugins (auth_relationship, bot_publish_approval, antprocess)"
  - "PassportPlugin"
  - "SkillCenter factories"
internal_dependencies:
  - agentclaw.community.core.bot_public_risk.repository_protocol
  - agentclaw.community.core.bot_public.catalog_metadata
  - agentclaw.community.core.repository.protocols.bot    # repository contracts consumed by this module
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.devices
  - agentclaw.community.core.operator_context
  - agentclaw.community.core.skill_center
  - agentclaw.community.core.workspace
  - agentclaw.community.di
  - agentclaw.community.log
  - agentclaw.community.plugin_api.approval_workflow
  - agentclaw.community.plugin_api.auth_relationship
  - agentclaw.community.plugin_api.bot_publish_approval
  - agentclaw.community.plugin_api.device_sync_dispatcher
  - agentclaw.community.plugin_api.http_client
  - agentclaw.community.plugin_api.models
  - agentclaw.community.plugin_api.passport
  - agentclaw.community.utils.avernet_tenant
  - agentclaw.community.utils.env_utils
```

### Change impact

Owns visible-to-other-users state; bugs here can leak unpublished bots or block legitimate publication. The approval callback path crosses with core/antprocess.

Catalog Search intersects BCS visibility/order with Backend service Bot
`online_available` before public pagination. Joins are bounded to 100 candidates
per batch; exact totals scan the matching catalog and retain only the requested
window. Personal and BCS-native Bots keep their existing inclusion rules.
Search offloads synchronous work at the HTTP boundary. After the first batch,
at most two page reads overlap and are merged in BCS order; a process-wide
two-scan reservation bounds extra workers and busy scans use the serial path.
All first/parallel/serial page reads additionally share a four-read process budget.
Every worker copies the request ContextVars and owns its repository Session.
Catalog joins skip unused COUNT via `list_catalog_bots_by_owner_bot_pairs`;
other repository callers keep their count and paging contract. No result cache
or approximation of filtered totals is introduced. See the read-budget spec
under `specs/2026-11-09-catalog-search-budget/` for rollout constraints.
Display projections include only the saved avatar URL, not raw extension data.
`publication_actions` and `friend_catalog_actions` contain the publication and
friend/catalog responsibilities composed by `BotPublicService`; shared errors
remain import-compatible through the original service module.

### BCSFuse publication synchronization

- Legacy `POST /api/bots/{bot_id}/public` syncs user publication through
  `PUT /v1/workers/{worker_id}/{online|offline}`. Community defaults to the
  bare bot ID; internal Backend overlays must set
  `user_config.bcsfuse.worker_id_with_owner: true` for `bot_id:owner_id`.
- `POST /openapi/v1/collaboration/bots/{bot_uuid}/public` uses the exact BCS
  UUID, independently of that legacy ID configuration. For `public_scope=user`,
  Backend syncs runtime state only after the BCS attribute PATCH succeeds:
  private becomes offline, public/protected become online. Approval submission,
  rejection, cancellation, and skipped/failed BCS writes do not change runtime.
- For `public_scope=agent`, the BCS Provider attributes PATCH dispatches the
  existing visibility sync after persisting `visibility`. BCS sends
  `POST /v1/workers/{bot_uuid}/sync` with `availability` set to the persisted
  visibility; it does not overwrite user-publication runtime state. This needs
  BCS's `bcsfuse.enabled` configuration and deployment of the updated BCS runtime.
- BCSFuse failure remains best-effort and is logged. User-publication logs use
  `[_sync_bcsfuse_runtime_state]` with the exact `worker_id`; Agent-publication
  logs live in BCS (`Worker synced to bcsfuse` / `Worker sync failed, retrying`).
  Successful publication is not proof of downstream synchronization; use these
  logs and the BCSFuse worker record when verifying a rollout.
