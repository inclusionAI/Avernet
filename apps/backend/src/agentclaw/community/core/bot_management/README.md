# `agentclaw.community.core.bot_management`

Bot lifecycle, creation policy, repository, engine resolution, render-screen models, and skill-set switching.

## Context Boundary

```yaml
purpose: "Bot lifecycle, creation policy, repository, engine resolution, render-screen models, and skill-set switching."
provides:
  - "OwnerHandoffServiceProtocol and durable OwnerHandoffService"
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
  - "AicodingRestartLifecycle"
  - "AicodingArtifactTransferStrategy"
  - "ArtifactTransferStrategy"
  - "DefaultArtifactTransferStrategy"
  - "ArtifactTransferRegistry"
  - "CreateBotForOthersService"
  - "DefaultBotPassportRepairService"
  - "BotQuotaService and BotQuotaScope"
consumes:
  - "DeviceAccessor"
  - "PassportPlugin"
  - "CallerIdentityRepositoryProtocol"
  - "AuthRelationshipPlugin"
  - "DeviceService"
  - "ArcaRestartBindings (binding reads)"
  - "ArcaRestartTransport (fixed-target Arca command execution)"
  - "BaasRestartRuntime (coding backup inventory/commands)"
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
  - agentclaw.community.core.execution_identity.protocols
  - agentclaw.community.plugin_api.staff_dept
  - agentclaw.community.core.bot_inventory
  - agentclaw.community.core.engine_runtime
  - agentclaw.community.core.repository.protocols.bot    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.devices    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.identity    # MCP execution identity carried into the restart Passport refresh
  - agentclaw.community.core.repository.protocols.platform    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.publishing    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.skill_center    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.skills_pool    # persisted initial layout and restart selection
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
  - agentclaw.community.core.skills_pool.native_creation
  - agentclaw.community.core.skills_pool.types
  - agentclaw.community.core.task_queue
  - agentclaw.community.core.workspace
  - agentclaw.community.di
  - agentclaw.community.di.modules
  - agentclaw.community.kernel.lifecycle
  - agentclaw.community.kernel.device_dto    # CommandResult contract for coding-owned Arca backup execution
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

Coding restart recovery policy, target checks and the direct-Arca command transport
contract are internal to `engines/aicoding/restart_arca.py`. `ArcaRestartBindings`
only reads the captured binding; `ArcaRestartTransport` wraps the existing provider
hook. The composition root selects that hook, while the shared device router and
its ordinary APIs stay unchanged. Changes require community strategy tests and OCB
corp sandbox/tenant execution-versus-release contract tests. Bound coding Arca
replacement orchestration lives in `engines/aicoding/restart_arca_lifecycle.py`;
the coding worker invokes it directly, just as it invokes the BaaS executor.
Shared stop/start primitives and allocation lock handoff remain unchanged.
`aicoding/restart_dependencies.py` owns the immutable, lazy
`RestartBackupDependencies` envelope and its BaaS runtime contract. DI supplies
it once; BotService and both coding executors pass the same envelope rather
than separate provider-specific backup factories. Published/Caller dispatch
passes the existing runtime unchanged; only the coding strategy adapts it into
this envelope. Other engines do not construct backup dependencies.

## Stable Bot addressing

`addressing.py` owns the Backend-internal compatibility boundary. A request may
still supply the historical field named `owner_id`; repository lookup matches it
against either the immutable `entity_id` or the current owner and rejects more
than one live match. Internal flows resolve once to `ResolvedBotContext`, then
use `bot_pk` or `entity_id` for Bot resources while retaining
`current_owner_id` for authorization, notification and audit semantics.

The database tenant guard and current environment remain mandatory parts of the
lookup and are enforced by the persistence layer; `ResolvedBotContext` does not
duplicate them as fields. A bare `bot_id` is allowed only through the explicit
unique lookup and fails closed for repeated `default` Bots.

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

## Ordinary coding restart

Only explicit `aicoding` / `claude_code` strategies durably admit ordinary restart
requests. `engines/aicoding/restart_task.py` owns the queue handler and admission;
`restart_state.py` owns the ext journal and mutation fence. Failures persist existing `ext.start_status` / `ext.start_message` using
ext-only CAS, then write Bot `FAILED` using `update_by_owner`.
Both status routes use the original `get_bot` and error extraction, with no
restart-specific projection or queue lookup. Runtime callbacks are unchanged;
the journal is an audit/recovery record, not a public status override.

The existing async entrypoint only supplies neutral dependencies to the strategy.
The synchronous restart, stop/start and provider upgrade flows are unchanged. The queue
and Bot repository provide dedup and ext CAS primitives. Repository contracts and implementations are unchanged. Journal and status
writes are separate; lifecycle-field CAS is not provided. No table or frontend migration is needed.
See the 2026-10-08 durable-restart spec for recovery and query limitations.

### Restart backup scope

Only explicit `aicoding` / `claude_code` restart strategies run the guard. Direct
ARCA uses the captured old binding's sandbox/tenant through the existing
backup-only transport; BaaS uses the physical devices returned by its inventory.
No normal exec permission, provider transport or non-coding lifecycle is changed.

Before backup, Backend sends the embedded read-only publication detector to the
old container (`engines/aicoding/fastdisk_probe.py`). It does not require an
installed detector. The gate checks both workspace and sessions against actual
mountpoints and source/target inode identity, and preserves the scripts' valid
pending-cutover marker semantics. A ready marker alone is not proof of a live
mount. **Initial detection failure is treated as non-fastdisk** for restart
compatibility; publication/restore still fail closed on detection errors.

For a confirmed fastdisk target, `/opt/agentclaw/bin/restart_backup start/status`
must return `committed` with a matching operation, boot identity, generation and
successful backup receipt. `partial_success`, degraded completeness, failure,
missing helper, missing mounts, malformed output, transport error and unconfirmed
timeout block replacement. Strict backup has a shared **300-second wait budget**
across replicas; command durations count against that budget and receipts are
checked again before mutation. Non-fastdisk/unknown detection retains the
existing best-effort backup behavior and its original budget. Binding checks,
restart locks and mutation fences remain mandatory in either case.

Ordinary durable restarts persist positive target confirmations monotonically in
`ext.coding_restart.fastdisk_targets`, so redelivery cannot downgrade them. A
failure reuses `status`, `ext.start_status` and `ext.start_message`. Published
restarts translate the terminal engine veto to the existing publish FAILED and
`ext.error_message` fields using status/ext CAS, preserve the old binding, and
finish the task without issuing a provider workflow. All veto classification,
operation failure, guard ownership, replay checks and failure persistence live in
`engines/aicoding/restart_publish.py`; there is no shared backup exception.
The common publish flow keeps its original orchestration inside `execute_restart`
and enters the strategy's `published_restart_scope`; it does not extract a second
common execution function. The scope yields an empty result to proceed, or a
populated result to return before workflow execution. Only an engine that handles
an exception may fill that result on exit, after its required persistence succeeds.
The default scope performs no I/O and propagates exceptions unchanged. Workflow
acquisition still calls `acquire_restart_workflow`. Existing persistence operations
are supplied as `PublishedRestartServices`; only the strategy interprets its
restart metadata. Default strategies add no probing, metadata or status writes.
The operation runner accepts an acquisition callback, preserves the exact operation
identity and workflow adoption, and knows nothing about coding or backup failures.
No HTTP endpoint, frontend, database schema or container-script change is needed.
The internal `published_restart_scope` hook replaces the callback-based
`execute_published_restart` hook; deploy the common flow, base strategy and coding
strategy together. Third-party overrides of the former hook must implement the
scope contract instead. Ordinary Bot restart hooks and task payloads are unchanged.

The scripts may retain a maintenance barrier after partial backup, and a timed-out
backup worker may still be running. Backend does **not** delete that barrier or
force restart; investigate/repair the existing backup operation before retrying.
Published retries retain their existing deterministic helper operation key (so a
failed receipt on the same container is not silently replaced with a new attempt).
A committed backup is a recoverable generation, not a zero-RPO promise: live
writes after the backup are outside this guard's guarantees.

## Engine release inspection

`engine_version/` exposes an authorized, read-only Service API for personal and
service draft Bot release checks. It consumes existing repositories, the
DeviceContextResolver and DeviceAdapterTransport and never invokes lifecycle or
publish-policy writes. See its README for the contract and deployment boundary.

### Fastdisk transfer runtime layout and rollout

The artifact engine name is logical: `claude_code` may use the physical
`.aicoding` runtime. After a successful fastdisk gate, both upload source and
restore destination use the detected `layout.home`; OSS version/engine prefixes
continue to come from the build plan. Restore returns that physical draft path.
Only absent/null configuration blocks use defaults; non-mapping blocks fail
configuration validation.

Deploy schema-6 container scripts before this Backend change. Old/missing scripts
on fastdisk Bots fail closed. Non-target engines skip detection; non-fastdisk
coding engines retain NAS transfer only after successful detection and binding
checks. Restore is not transactional: completed replacements remain after failure;
required transfer failures stop subsequent stale-file cleanup.

To roll back, pause fastdisk transfers, retain compatible schema-6 scripts, and
revert Backend only after deciding how authoritative fastdisk files will remain
available. Do not resume the old NAS path on stale data; first establish a
verified migration to NAS or deploy the previous compatible fastdisk implementation.

### Runtime artifact allowlist parity (schema 6)

The shared build resolves `service_bot_runtime_artifact` once for the Bot owner
and environment. The same boolean is passed to the AICoding publication hook and
legacy NAS transfer; the strategy does not query a second allowlist. Schema 6
adds `include_runtime_artifacts` (boolean, omitted means disabled). With the gate
off, `.agentclaw` is untouched, while existing versioned `.local/bin` contents
are cleared without reading the draft CLI source or creating an absent OSS
subtree. Engine files and mount-path selection are unchanged. Historical draft
restore ignores today's publication gate and restores the selected version.
Schema-5 containers fail the pre-execution probe rather than ignoring the gate.


### Artifact transfer strategy selection

`engines/artifact_transfer.py` owns the neutral interface, default no-op and
registry. The composition root registers the coding implementation for
`aicoding` and `claude_code`; `BotBuildService` resolves using each build plan's
engine on both publication and draft restore. All other engines select the
default, whose `False` / `None` results retain the existing NAS flow without
reading coding configuration, runtime paths or container state. The default
never reports a completed transfer. Coding errors still propagate, not fall back.

Fastdisk detection, configuration, command execution and OSS request construction
remain in `engines/aicoding/`. DI assembly may reference the concrete coding
implementation; the shared build service imports only the neutral registry.
This is a selection refactor, not a script protocol change (schema 6 is unchanged).


The owner_handoff subcomponent owns the feature-gated responsibility-transfer
journal and TaskQueue coordinator. It preserves Bot/entity/tenant/environment
identity and commits mutable Owner responsibility only after fenced credential
and runtime cutover. See [its contract](owner_handoff/README.md). Legacy resource
addressing remains shared with normal Bot lifecycle callers.

### Resource lookup and deletion after Owner handoff

`BotService.get_bot_by_address(bot_id, user_id)` is an internal resource query:
`user_id` is a legacy entity/current-Owner locator, not caller authority. It uses
`BotAddressResolver` and enriches the resolved Bot through the strict current-Owner
`get_bot` query. Transport callers must authorize the authenticated actor before
using this resource lookup. Ambiguity and database errors propagate.

`validate_delete_bot` checks current ownership and existing deletion protections
without releasing devices or credentials. Publishing calls it before tearing
down any version container. Actual deletion repeats validation; current ownership
selects the Bot row while transferable service-Bot resources are cleaned using
immutable entity. Personal/default cleanup keeps its original Owner semantics.
