# `agentclaw.community.core.bot_management`

Bot lifecycle, creation policy, repository, engine resolution, render-screen models, and skill-set switching.

## Context Boundary

```yaml
purpose: "Bot lifecycle, creation policy, repository, engine resolution, render-screen models, and skill-set switching."
provides:
  - "BotService"
  - "BotCreateContext"
  - "BotCreateDeploymentMode"
  - "PreparedBotCreate"
  - "BotRepository protocol + impl"
  - "EngineResolver"
  - "DataInitService"
  - "RenderScreenService"
  - "TeclawProvisionService"
  - "TeclawPublishTaskLifecycle"
  - "CreateBotForOthersService"
  - "DefaultBotPassportRepairService"
  - "BotQuotaService and BotQuotaScope"
consumes:
  - "DeviceAccessor"
  - "PassportPlugin"
  - "CallerIdentityRepositoryProtocol"
  - "AuthRelationshipPlugin"
  - "DeviceService"
  - "ResourceService"
  - "BotPublishService"
  - "SkillCenter factories"
  - "PolicyService"
  - "TaskQueueService"
  - "HandlerRegistry"
  - "CommonConfigService"
  - "BotSpaceAccessProtocol (implemented by the Spaces context)"
  - "SpaceAccessServiceProtocol"
  - "CachePlugin quota lock"
  - "CapabilityDesiredStateRepositoryProtocol"
internal_dependencies:
  - agentclaw.community.core.repository.protocols.bot    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.devices    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.identity    # MCP execution identity carried into the restart Passport refresh
  - agentclaw.community.core.repository.protocols.platform    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.publishing    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.skill_center    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.capability_desired_state    # explicit Skill/MCP Installation purge during Bot deletion
  - agentclaw.community.core.base
  - agentclaw.community.core.bot_app_grant.protocols    # sweep contract: deletion withdraws the bot's app authorizations
  - agentclaw.community.core.bot_startup_script.protocols    # sweep contract: deletion removes the bot's stored startup script
  - agentclaw.community.core.bot_collaborator
  - agentclaw.community.core.config
  - agentclaw.community.core.config_compose
  - agentclaw.community.core.common_config
  - agentclaw.community.core.cron.services.aicoding.cron_auto_setup
  - agentclaw.community.core.desktop_bot
  - agentclaw.community.core.mcp
  - agentclaw.community.core.devices
  - agentclaw.community.core.engine_runtime.errors    # EngineStageNotLiveError used by codefuse runtime-target resolution
  - agentclaw.community.core.engine_runtime.stage    # resolve_stage_bind_id / STAGE_* reused by codefuse runtime-target resolution
  - agentclaw.community.core.events
  - agentclaw.community.core.resources
  - agentclaw.community.core.service_bot
  - agentclaw.community.core.bot_management.bot_space    # narrow cross-context Space membership contract
  - agentclaw.community.core.spaces.errors    # typed Space membership failures propagated by Bot Space assignment
  - agentclaw.community.core.spaces.models    # SpaceRecord/SpaceType used by Bot Space assignment
  - agentclaw.community.core.spaces.protocols    # Space lookup used by quota configuration
  - agentclaw.community.core.skill_center
  - agentclaw.community.core.task_queue
  - agentclaw.community.core.workspace
  - agentclaw.community.di
  - agentclaw.community.di.modules
  - agentclaw.community.kernel.lifecycle
  - agentclaw.community.log
  - agentclaw.community.plugin_api.devices
  - agentclaw.community.plugin_api.drm
  - agentclaw.community.plugin_api.http_client
  - agentclaw.community.plugin_api.passport
  - agentclaw.community.plugin_api.secret_resolver
  - agentclaw.community.plugin_api.auth_relationship
  - agentclaw.community.plugin_api.cache
  - agentclaw.community.utils
  - agentclaw.community.utils.avernet_tenant
  - agentclaw.community.utils.env_utils
  - agentclaw.community.utils.secret_utils
  - agentclaw.community.core.access.policy_service_protocol  # Service API Protocol consumed by this module
```

### Change impact

Highest-fanout domain — 12+ other core domains import it. BotService signature changes ripple widely. Repository protocol changes break the corresponding plugin_impl repos.

## Owner display name on OpenAPI creation

OpenAPI creation supplies the matching verified gateway user's nonblank
`display_name` as `nick_name`, stripping surrounding whitespace. When no matching
user display name is available, the owner ID remains the fallback. The existing
`ac_bots.owner_name` column persists this value; catalog search projects it
without substituting the entity ID.

Create-with-manifest freezes `nick_name` in the durable job's `spec` at submission,
so background completion does not depend on request identity context. Jobs queued
before this field was introduced remain readable and fall back to their `user_id`.
This requires no database migration and does not backfill historical Bot rows.

## Coding-engine restart precondition

`EngineProvisioningStrategy.prepare_restart` is the single backup-related call
in ordinary Bot restart. The default invokes the original lock acquisition
callback and returns its result without probing containers or resolving devices.
Only aicoding/claude_code implement helper probing, legacy/no-mount skips,
backup polling, and receipt/instance verification in
`engines/aicoding/restart_backup.py`. That strategy waits BEFORE acquiring the
existing 120-second restart lock, verifies binding and receipt UNDER the lock,
and releases it on verification failure. The original caller still handles
lock contention, stop/update, allocation and lock hand-off unchanged. No lock
repository, BotService Service API, or generic status/retry policy is changed.
Backup failure preserves the old binding and status. Coding probes explicitly
opt into the existing command API's recovery mode so FAILED/STOPPED bindings
(which the original restart accepts) are not rejected before the script probe.
The default command status gate remains ACTIVE/PENDING for all existing callers;
RELEASED/unknown bindings and transport failures are never silently allowed.

Async HTTP/instance entrypoints select the engine's `execute_restart` policy.
Its default directly calls the existing synchronous operation on the calling
thread. Only the coding strategy offloads its blocking backup/lifecycle call;
routers and the composition root contain no thread-pool implementation. This
keeps long coding backup polling off the event loop without changing other
engines' execution context or converting existing synchronous Service APIs.
The runtime serializes backup workers and keeps writers stopped; successful
preparation does not itself authorize deletion.

Published restart and caller upgrade use thin engine dispatch. Published hooks
run only inside restart's issue/retirement branches, not ordinary publication
or workflow adoption. Existing caller creation/reuse/poll paths are unchanged.
BaaS's existing command API accepts an optional physical target resolved from the
authorized inventory; omitted means unchanged bot-level dispatch. The strategy
checks all live targets, pins polls, and rechecks inventory before replacement.
These checks do not replace existing provider workflow/concurrency semantics.

Logs use `event=aicoding_restart_backup`, with phase, outcome/reason, Bot/target,
operation ID, duration and generation. Wait logs are throttled to state changes
or once per minute. Raw commands, output and exception messages are not logged
by the strategy. Runtime failure/unknown state never authorizes replacement.
Legacy absence is confirmed, not inferred from permission/transport errors.
An absent helper with neither `/opt/agentclaw/restart-backup-v1` (installed only
by this rollout) nor the restart barrier is a legacy skip. The pre-existing
`.fastdisk.ready` marker is not a backup capability marker and cannot by itself
block an old Bot's restart. This rollout must install the helper before enabling
canonical fastdisk binds; a missing helper after that installation fails closed.

Propagation: the engine hook, the three restart consumers and the optional BaaS
command parameter; no frontend, Relay HTTP or database migration. Targeted unit,
entrypoint and architecture tests cover this contract. Linux root/admin, PaaS
routing and NAS behavior still require staging validation with the paired
container scripts. Failed runtime operations remain fail-closed pending recovery.
