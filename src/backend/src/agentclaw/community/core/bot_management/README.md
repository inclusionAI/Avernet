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

## Mandatory restart precondition (coding runtime, v1)

`EngineProvisioningStrategy.prepare_restart` is a mandatory lifecycle hook under
an acquired restart lease, before either release/start or BaaS update. Unlike
`apply_restart_extra_configs`, its failures propagate and MUST NOT detach the
old binding. The default implementation is a no-op. The aicoding strategy owns
the runtime entrypoint, versioned result validation and polling for both aicoding
and claude_code; BotService contains no runtime path, command or mount policy.
The platform DeviceService router chooses ARCA/BaaS command execution.

The runtime contract is `/opt/agentclaw/bin/restart_backup start|status <operation>`:
JSON version 1, operation identity, container identity and explicit completion
receipt. Only confirmed legacy script absence or confirmed no canonical bind
mounts can bypass backup. Errors/timeouts never permit destruction. See
`aixcoding-docker-scripts/docs/restart-backup-contract.md` in the companion repo
for root/admin ownership, rollout invariant and mandatory Linux/provider tests.

The lock repository's `renew` refreshes the DB-clock lease for long backups;
`release(expected_created_at=...)` prevents stale reaping from deleting a lease
renewed after the stale read. Existing release callers retain token-only behavior.

Propagation: concrete coding strategies and the shared restart caller, unified
lock repository and its test implementations, the runtime scripts, both platform
exec providers. No frontend or Relay HTTP contract is changed. The HTTP adapter
offloads the synchronous service to its thread pool so backup polling does not
block the server event loop. This is NOT a new durable asynchronous restart queue:
long-request/gateway behavior and process-death recovery require staging validation.


### Published and caller targets

`BotServiceProtocol.instance_restart_guard(bot, device_id)` is an asynchronous
context manager: it resolves the same `prepare_restart` engine hook and holds a
target-specific fenced lease through backup and replacement submission. Its
adapter lives in `services/instance_restart.py`; all runtime/mount/legacy policy
remains in `engines/aicoding/restart_backup.py`. The explicit target is the
publish-stage/caller bot UUID, never the source Bot binding. Source Bot state is
not updated. Ordinary Bot restart continues to own its existing status writes.

Published restart calls the guard inside the durable runner's issue callback,
not before workflow adoption. Both retirement branches are guarded as well.
Caller calls it only when upgrading an existing instance (including automatic
version upgrades); first creation, connection reuse and polling are unchanged.
Confirmed RELEASED targets need no backup; query errors are not proof of release.
Legacy targets with confirmed helper absence continue the original upgrade.
Permission/exec/protocol failures do not count as legacy absence.

A deterministic per-target runtime operation ID resumes an interrupted backup
without starting competing workers. The runtime's `/run` must be new after actual
replacement. Failed operations remain fail-closed pending runtime recovery.
Published/caller execution pins commands to each physical `provider_device_id`
from BaaS's target device inventory, then rechecks that inventory before allowing
replacement. Historical RELEASED/STOPPED devices need no script; every remaining
legacy container is probed independently. A malformed/unresolvable inventory is
not permission to destroy. `BaasServiceProtocol.exec_command_on_device` wraps the
existing PaaS command endpoint; it adds no server-side API. Local tests do not
prove provider inventory accuracy or Linux/NAS behavior.

Propagation: the BotService Service API gains `instance_restart_guard`; published
restart and caller upgrade consume it. No new DB schema, frontend or Relay HTTP
API is introduced. Backup runs outside the event loop. Cancellation during
preparation retains the lease until its worker finishes and never proceeds to
replacement on behalf of the cancelled request.
