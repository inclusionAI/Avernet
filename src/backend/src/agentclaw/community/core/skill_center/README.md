# `agentclaw.community.core.skill_center`

Skill Center domain — skill set switching, market sync, repository sync, skill auth, propagation logging.

## Context Boundary

```yaml
purpose: "Skill Center domain — skill set switching, market sync, repository sync, skill auth, propagation logging."
provides:
  - "SkillSetService"
  - "SkillSetActivator"
  - "SkillSetSwitcher"
  - "MarketSyncService"
  - "RepositoryCatalogService"
  - "GitSyncService"
  - "SkillAuthService"
  - "CurrentRuntimeLayoutProbeService"
  - "LocalSkillQueryService"
  - "LocalSkillUploadService"
  - "LocalSkillStateService"
  - "LocalSkillDeleteService"
  - "BotCapabilityAuthorizationHookProtocol"
  - "SkillSetControlPlaneService"
  - "SkillInstallationRepositoryProtocol"
  - "BotSkillAssetService"
  - "RuntimeProjectionResolver"
  - "BotRuntimeProjectionReconciler"
  - "BotRuntimeProjectionReconcilerProtocol"
  - "ActiveSkillSetInstallationMaterializer"
  - "LocalSkillCleanupWorkModel"
  - "SkillActivationSyncAction"
  - "SkillActivationSyncScope"
  - "SkillActivationSyncTaskHandler"
  - "SkillActivationSyncWork"
  - "enqueue_skill_activation_sync"
  - "build_skill_activation_sync_payload"
  - "parse_skill_activation_sync_payload"
  - "build_skill_activation_sync_idempotency_key"
consumes:
  - "BotRepository"
  - "BotCollabLogRepositoryProtocol"
  - "CollaboratorServiceProtocol"
  - "Events"
  - "core/mcp services"
  - "CachePlugin"
  - "DeviceAccessor"
  - "DeviceContextResolver"
  - "DeviceAdapterTransport"
  - "MCPCenterPlugin"
  - "ObjectStoragePlugin"
  - "SecretResolver"
  - "SkillCenterClient"
  - "SkillRepoSyncPlugin"
  - "WorkspacePathFactory"
  - "LocalSkillCleanupRepository"
  - "BotRuntimeProjectionReconcilerProtocol"
internal_dependencies:
  - agentclaw.community.api.skill_parameter_service_factory
  - agentclaw.community.api.skill_market_service
  - agentclaw.community.api.space_skill_query_service
  - agentclaw.community.api.local_skill_query_service # Protocol LocalSkillQueryService inherits
  - agentclaw.community.api.bot_skill_asset_service # Protocol BotSkillAssetService inherits
  - agentclaw.community.core.repository.protocols.bot    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.skill_center    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.skill_center_types # query projection types consumed by this module
  - agentclaw.community.core.repository.protocols.skill_installation
  - agentclaw.community.core.repository.protocols.skills_pool    # Skills Pool repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.skill_set_control_plane
  - agentclaw.community.core.repository.skill_set_control_plane_types
  - agentclaw.community.core.access
  - agentclaw.community.core.base
  - agentclaw.community.core.bot_collaborator
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.config
  - agentclaw.community.core.config_compose
  - agentclaw.community.core.devices
  - agentclaw.community.core.errors    # transport-free DomainError base for SkillSet control-plane errors
  - agentclaw.community.core.events
  - agentclaw.community.core.mcp
  - agentclaw.community.core.models
  - agentclaw.community.core.spaces.services
  - agentclaw.community.core.skills_pool
  - agentclaw.community.core.task_queue    # durable enqueue for Bot-level activation sync
  - agentclaw.community.core.workspace
  - agentclaw.community.di.modules
  - agentclaw.community.di.runtime_mode
  - agentclaw.community.kernel
  - agentclaw.community.log
  - agentclaw.community.plugin_api.cache
  - agentclaw.community.plugin_api.local_skill_cleanup
  - agentclaw.community.plugin_api.models
  - agentclaw.community.plugin_api.device_adapter_transport
  - agentclaw.community.plugin_api.devices
  - agentclaw.community.plugin_api.mcp_center
  - agentclaw.community.plugin_api.mcp_auth
  - agentclaw.community.plugin_api.passport
  - agentclaw.community.plugin_api.object_storage
  - agentclaw.community.plugin_api.secret_resolver
  - agentclaw.community.plugin_api.skill_center_client
  - agentclaw.community.plugin_api.skill_repo_sync
  - agentclaw.community.plugin_api.skill_scanner
  - agentclaw.community.utils
  - agentclaw.community.utils.avernet_tenant
  - agentclaw.community.utils.env_utils
```

### Change impact

Skill-set switching is the highest-throughput flow in production. Changes here can break every chat session in flight. Coordinate with the propagation log schema before changing repository protocols.

`ac_bot_skill_installation` materializes the current active Desired State with
identity `(tenant, env, owner_id, bot_id, skill_id)`. During Phase 1 cutover,
`ActiveSkillSetInstallationMaterializer` lazily
inserts missing rows for a Bot's ordinary active SkillSet members. It is
invoked only before a complete runtime reconcile and before a new Service Bot
Artifact build; it never
deletes rows, reads historical Default exclusions, or runs in HTTP GET/list,
Pool convergence, or file-snapshot paths.

`GET /openapi/v1/bots/{bot_id}/skills` is the one deliberate exception, through
`repair_bot_skillset_installations` rather than the materializer. It lists every
Skill a Bot reaches — the rows it owns plus the ones a SkillSet bridges — and
`active` is a *filter*, deciding `total` and the page boundary, so the listing
repairs before it filters. Unlike the materializer this repair also deletes rows
and reads Default exclusions. It writes only the difference, in one transaction,
after the caller's Bot access has been checked, and never reconciles runtime.
See `specs/2026-08-23-openapi-v1-bot-skill-listing/`.

MCP Direct activation and ordinary SkillSet MCP membership share the same
active-only desired-state and compensation boundary as Skills.  The MCP
catalogue, user configuration, and permission grant remain separate facts;
the control plane consults the MCP authorization Service API before any MCP
membership or Direct-installation write.

`RuntimeProjectionResolver` is the only source of a mutation/restart runtime
snapshot. It receives Installation, active ordinary SkillSet membership,
System Default assets and required configuration, then produces a complete
Local/Repo/Center/MCP/CLI projection. Engine adapters receive that snapshot;
they do not reconstruct it from Default exclusions or BFF state.

Direct activation and canonical SkillSet mutations commit desired state in the
repository transaction and then reconcile the complete runtime projection.
They do not acquire `SkillsPoolEditGuard`: Pool editing is a file-corpus and
layout-migration concern, retained only by Local package upload/replacement/
deletion and Pool cutover/rollback paths. Phase 1 intentionally has no
cache-backed cross-command Bot mutation fence: the current compensating restore
remains a best-effort compatibility path for non-concurrent mutations, while
durable serialization is deferred to the task-queue design.

`skill_activation_sync_task.py` is the enqueue half of that durable design:
`skill_center.activation_sync`, one task type shared by every
activation-shaped operation, discriminated by the payload's `action_type` and
deduped on the Bot — `(env, entity_id, bot_id)` — so at most one
synchronization per Bot is ever live. A second operation arriving mid-sync
joins the live task and gets `created=False`; that is only correct while the
handler reconciles against desired state read from the database, so the
handler that consumes these rows must not replay `action_args` as its
desired-state write. `SkillActivationSyncTaskHandler` is a skeleton: it owns
the registry key and the payload validation, and its `_run` seam reports
`Fail` until the body lands. It is not registered, and no call site enqueues,
so the control plane's inline mutate-then-reconcile path is unchanged.

Phase 1 does not run a global Local Installation backfill and does not treat
historical Default exclusions as an active-state source. The small number of
such historical residues is corrected through DB operations. Normal
SkillSet/Direct commands continue to maintain Installation synchronously; the
lazy materializer only fills the missing active ordinary SkillSet rows that
pre-date the new command path.

Local Skill replacement stages a complete package before switching the existing
Skill metadata. Its old-package cleanup work is persisted before an Active
runtime switch can commit; a rollback cancels that old-locator work before the
old package becomes authoritative again. Failed post-switch obsolete-byte deletion is recorded through
`LocalSkillCleanupRepository` in the exact deployment-wide Bot scope
`(env, owner_id, bot_id)`.  The next serialized Local Skill mutation retries
pending work; it marks successful work `cleaned` and retains failures with an
attempt count and a stable operator-safe error.  A task that follows a failed
Active rollback retains its staged bytes and restores the old runtime mapping
before it attempts byte cleanup. Cleanup identity uses the full SHA-256 of the
locator, while retaining the locator itself for execution; a digest collision
fails closed. Apply
`sql/2026_08_04_local_skill_cleanup_work.sql` before deploying this behavior.

Public Local Skill deletion first persists a non-purgeable `preparing` record,
then promotes it to `repair_required` before copying and verifying package
bytes in a unique Bot-scoped quarantine. Its one transaction rechecks active
custom SkillSet references, removes the default-set exclusion, all SkillSet
associations, and the Skill row, and makes the retained cleanup work
purgeable. Bot-scoped SkillSet activation takes the same edit lease, so it
cannot publish a stale association while deletion is in flight. If the
transaction fails, the package is restored from quarantine before the request
fails. A post-commit purge failure retains the same durable cleanup work; it
never recreates the deleted Skill.

If a device reports source deletion failure after a partial delete and the
authoritative package cannot be verified repaired, the complete quarantine is
retained as `repair_required` cleanup work. It is deliberately excluded from
ordinary obsolete-byte purge retries until package repair is resolved.
Before a later deletion of that same Local Skill starts, it reacquires the
serialized edit lease and restores any such quarantine to the authoritative
locator; only after that succeeds can the deletion retry. If restoration leaves
a redundant quarantine, ordinary pending cleanup may purge that duplicate.
