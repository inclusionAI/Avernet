# `agentclaw.community.api`

**The Service API surface.** A flat collection of modules, one per
public service, that re-export the `@runtime_checkable Protocol` any
consumer (HTTP, CLI, in-process embedder) calls against. FastAPI routers
live next door under [`adapters/http/`](../adapters/http/README.md); they
Inject a Protocol from this layer rather than the concrete service class
in `core/`.

## Where a Protocol is defined

**In its owning core module**, and re-exported here:

```
core/skill_center/skill_query_service_protocol.py   # the Protocol
core/skill_center/services/skill_query_service.py   # class SkillQueryService(SkillQueryServiceProtocol)
api/skill_query_service.py                          # re-export only
adapters/http/skill_center/router.py                # Injected(SkillQueryServiceProtocol)
```

The concrete service inherits its Protocol, and because both live in
`core/` that inheritance is a `core -> core` import — no cross-layer
waiver. Adapters are unaffected: they still import the name from `api/`.

This is what the layer chain (`api/ -> core/ -> plugin_api/ -> plugins/`)
already allows and what `arch.rules.md` Rule 3 actually asks for — a
Service API defined separately from the *delivery* layer, which `core/`
is. Defining Protocols *in* `api/` instead forced every implementing
service to import across the boundary, and each one needed a hand-written
exception in `test_architecture_compliance.py`. That list grew per
service; it no longer does.

## Why inherit rather than duck-type

Conformance is checked structurally, so a service *can* satisfy its
Protocol without naming it. Prefer inheritance anyway:

- it makes the contract navigable in an IDE — jump from Protocol to
  implementation and back — instead of forcing a reader to find the DI
  binding first;
- when every Protocol member is `@abstractmethod`, a missing member is a
  construction-time `TypeError` naming it, rather than a silently
  inherited `...` body that returns `None`.

A Protocol whose members are still `*args: Any, **kwargs: Any` asserts
nothing either way. Give it real signatures when you touch it.

## Layout

```
api/
├── README.md
├── __init__.py
└── <service>_service.py   # re-export of the owning core module's Protocol
```

## Enforcement

Five gates under `tests/community/architecture/`:

- `test_protocol_base_ordering.py` — a Protocol base never precedes a
  non-Protocol base that defines the same members, so a service's `...`
  stubs cannot silently shadow a real mixin implementation.
- `test_api_layer_is_protocols_only.py` — every file under `api/` either
  defines a Protocol or re-exports one from a core contract module via
  `__all__`; no subdirectories, no router code.
- `test_http_adapter_layer_is_http_only.py` — every router under
  `adapters/http/` Injects an `<X>Protocol` from `api/`, never a concrete
  service class from `core/<m>/services/`.
- `test_architecture_compliance.py` — the layer chain, including
  `core/` not importing `api/`.
- `test_service_api_conformance.py` — parametrizes over every registered
  `(Protocol, ConcreteService)` pair and asserts `issubclass` plus full
  signature equality, catching drift (a renamed keyword, `async` -> `def`)
  that inheritance alone does not.

DI wires Protocol -> concrete via a per-module `@singleton @provider
@inject` alias in `di/modules/<m>_module.py`, so both
`Injected(Protocol)` and `Injected(Concrete)` resolve to the same
singleton.

## Exceptions

Two Protocols stay defined in `api/` because their only implementations
live under `plugins/` (`NoopCodePlatformService`,
`NoopWorkflowCatalogService`), so no core module owns them:
`code_platform_service.py` and `workflow_catalog_service.py`.

Six more stay defined in `api/` because `core/service_bot/__init__.py`
pulls a heavy import chain that reaches `core -> di -> api`; moving their
Protocols into that package closes the loop into a circular import. The
underlying problem is the `core -> di` imports (see
`core/task_queue/services/task_queue_service.py`,
`core/channel/services/channel_service.py`), not this layer — they need
untangling first: `baas_service.py`, `bot_build_service.py`,
`bot_publish_service.py`, `publish_approval.py`,
`publish_flow_service.py`, `service_publication_facade.py`.

## Context Boundary

```yaml
purpose: "Service API Protocols — transport-agnostic contracts between adapters and core services."
provides:
  - "One Protocol per public service / factory"
  - "Structural conformance gate via tests/architecture/test_service_api_conformance.py"
  - BotRuntimeProjectorProtocol
  - SkillVersionMaterializerProtocol
  - SpaceSkillPublicationServiceProtocol
  - SkillCenterPublicationGatewayProtocol
  - SkillCenterReferenceServiceProtocol
  - SkillCenterSyncServiceProtocol
  - TrackLatestServiceProtocol
  - SkillMetadataParserProtocol
  - ServiceArtifactLineageReaderProtocol
  - ServiceEditLockServiceProtocol
  - BotQuotaServiceProtocol
  - SpaceSkillOfflineServiceProtocol
consumes:
  - "No service impls at import time — Protocols only declare shape, they don't depend on concrete services"
  - "A small number of core dataclass / schema types used to type Protocol method signatures (see internal_dependencies)"
internal_dependencies:
  - agentclaw.community.core.bot_collaborator.models # Collaborator records, roles and permission levels — typed in collaborator_service.py
  - agentclaw.community.core.access.models            # UserInfoRecord — typed in user_service.py
  - agentclaw.community.core.bot_app_grant.models    # BotAppGrantRecord — typed in bot_app_grant_service.py (real signatures, so the conformance gate can compare them)
  - agentclaw.community.core.bot_chat.schemas        # ConversationDetail, HealthCheckData — typed in bot_chat_service.py
  - agentclaw.community.core.bot_inventory.types   # Bot inventory/local workflow DTOs — typed in bot_inventory_service.py and local_bot_workflow_service.py
  - agentclaw.community.core.bot_management.bot_space  # Bot Space assignment result typed in bot_space_service.py
  - agentclaw.community.core.bot_management.bot_quota_service_protocol  # Space-scoped Bot quota service contract
  - agentclaw.community.core.bot_startup_script.repository.models  # BotStartupScriptRecord — typed in bot_startup_script_service.py (real signatures, so the conformance gate can compare them)
  - agentclaw.community.core.bot_config_manifest.credentials.models  # SourceCredentialRecord — typed in source_credential_service.py (W3 #1471)
  - agentclaw.community.core.bot_config_manifest.credentials.service_protocol  # SourceCredentialServiceProtocol — defined in its owning core module, re-exported here (W3 #1471)
  - agentclaw.community.core.bot_config_manifest.credentials.errors  # error family raised by the re-exported Protocol's implementations
  - agentclaw.community.core.caller_identity.contracts  # Caller identity API DTOs and stable errors
  - agentclaw.community.core.caller_identity.credential  # CallerToken — typed in caller_credential.py
  - agentclaw.community.core.caller_identity.protocols  # Caller collaborators — typed in caller_identity_service.py
  - agentclaw.community.core.channel.models          # ChannelRecord — typed in channel_service.py
  - agentclaw.community.core.economy.governance.domain.enums     # GovernanceStatus — typed in governance_service.py LifecycleServiceProtocol
  - agentclaw.community.core.economy.governance.domain.record    # GovernanceRecord — typed in governance_service.py Protocol
  - agentclaw.community.core.economy.governance.domain.ticket    # GovernanceTicket — typed in governance_service.py Protocol
  - agentclaw.community.core.economy.governance.services.admin_service  # TicketActionOutcome — typed in governance_service.py Protocol
  - agentclaw.community.core.economy.governance.services.service_protocols  # Admin/Whitelist/Lifecycle Protocol — 定义在 core,api re-export 供 router 注入
  - agentclaw.community.core.engine_runtime.models    # EngineResult / BotFacts / ConnectionResult — typed in engine_runtime_service.py (real signatures, so the conformance gate can compare them)
  - agentclaw.community.core.quality.models          # QualityTaskRecord — typed in quality_service.py and task_processor_service.py
  - agentclaw.community.core.service_bot.repository.models  # BotPublishRecord — typed in engine_config_service.py
  - agentclaw.community.core.service_bot.service_artifact_lineage_reader_protocol
  - agentclaw.community.core.service_bot.service_edit_lock_service_protocol
  - agentclaw.community.core.resources.models        # Resource / ResourceType — typed in resource_service.py (Protocol signatures mirror slim ResourceService verbatim; round-2 review #4)
  - agentclaw.community.core.spaces.models           # Space/member records and enums — typed in space_service.py
  - agentclaw.community.core.repository.protocols.skill_center_types # Space Skill query projection
  - agentclaw.community.core.market_favorites.models # Favorite records and target enum — typed in market_favorite_service.py
  - agentclaw.community.core.work_orders.models       # Work-order, notification, query, status, and event contracts
  - agentclaw.community.core.work_orders.callbacks    # Work-order callback credential typed in work_order_service.py
  - agentclaw.community.core.service_bot.services.baas_service  # BotWsConnectionInfoResponse / HttpConnectionInfo — typed in baas_service.py (BaasService is a plain core service)
  - agentclaw.community.core.service_bot.types       # PublishStage enum — typed in baas_service.py
  - agentclaw.community.core.skills_pool             # Skills Pool rollout/query/recovery domain DTOs used by operator Service API Protocols
  - agentclaw.community.core.skill_center            # Local Skill desired-state query service DTOs
  - agentclaw.community.core.skill_center            # Local Skill upload lifecycle contract
  - agentclaw.community.core.skill_center            # Local Skill desired-state lifecycle contract
  - agentclaw.community.core.skill_center            # Local Skill recoverable deletion lifecycle contract
  - agentclaw.community.core.task.domain.models      # TaskInfo, TaskExecutionGraph, TaskOpResult, TaskCallbackData — typed in task_service.py and task_loop_callback.py
  - agentclaw.community.core.task.domain.requests    # TaskInfoRequest — typed in task_service.py Protocol execute signature
  - agentclaw.community.core.task.repository.types   # TaskInfoRecord — typed in task_service.py Protocol list_tasks signature
  - agentclaw.community.kernel.device_dto            # OutBoundOperationRule — typed in baas_service.py Protocol (B6)
  - agentclaw.community.plugin_api.auth              # AuthRequestContext — typed in caller_iam_token_service.py
  - agentclaw.community.plugin_api.passport          # PassportPlugin — typed in caller_identity_service.py
  - agentclaw.community.plugin_api.skill_center_gateway # Public catalogue request/result DTOs typed in skill_center_gateway_service.py
  - agentclaw.community.core.task.task_runner.integration.ports  # OpenApiBotPort — typed in task_grant_service.py (stateless secbaas grant/revoke relay)
  - agentclaw.community.log                          # get_logger used by task_grant_service.py grant/revoke relay logging
  - agentclaw.community.core.access.policy_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.access.user_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.aicoding.architect_rebind_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.aicoding.data_proxy_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.aicoding.workitem_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.aicoding.workspace_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.bot_app_grant.bot_app_grant_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.bot_chat.bot_chat_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.bot_chat.human_bot_friendship_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.bot_collaborator.collaborator_lock_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.bot_collaborator.collaborator_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.bot_collaborator.member_management_capability_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.bot_dormant.bot_dormant_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.bot_inventory.bot_inventory_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.bot_inventory.local_bot_workflow_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.bot_management.bot_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.bot_management.bot_space_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.bot_management.create_bot_for_others_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.bot_management.data_init_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.bot_management.default_bot_passport_repair_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.bot_management.render_screen_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.bot_public.bot_discover_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.bot_public.bot_public_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.bot_config_manifest.bot_config_manifest_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.bot_config_manifest.bot_config_manifest_apply_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.bot_startup_script.bot_startup_script_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.caller_identity.caller_credential_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.caller_identity.caller_iam_token_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.caller_identity.caller_identity_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.channel.channel_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.common_config.beta_quota_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.common_config.common_config_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.cron.cron_relay_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.desktop_bot.desktop_bot_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.devices.device_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.devices.oss_to_nas_migration_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.devices.oss_to_nas_switch_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.economy.governance_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.engine_runtime.engine_connection_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.engine_runtime.engine_runtime_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.expert_chat.expert_chat_instance_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.expert_chat.expert_chat_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.harness.content_scanner_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.harness.health_diagnosis_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.harness.patch_engine_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.harness.patch_library_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.harness.patch_planner_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.market_favorites.market_favorite_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.mcp.mcp_auth_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.mcp.mcp_config_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.mcp.mcp_market_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.mcp.mcp_sync_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.quality.quality_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.quality.task_processor_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.resources.resource_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.services.engine_config_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.session_resources.session_resource_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.skill_center.bot_capability_state_reader_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.skill_center.bot_runtime_projector_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.skill_center.direct_activation_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.skill_center.git_sync_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.skill_center.installation_backfill_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.skill_center.local_skill_delete_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.skill_center.local_skill_upload_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.skill_center.repository_catalog_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.skill_center.runtime_layout_probe_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.skill_center.skill_auth_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.skill_center.skill_batch_sync_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.skill_center.skill_center_gateway_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.skill_center.skill_center_sync_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.skill_center.skill_market_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.skill_center.skill_member_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.skill_center.skill_metadata_parser_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.skill_center.skill_parameter_service_factory_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.skill_center.skill_propagation_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.skill_center.skill_publish_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.skill_center.skill_query_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.skill_center.skill_scan_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.skill_center.skill_service_factory_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.skill_center.skill_set_management_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.skill_center.skill_set_service_factory_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.skill_center.space_skill_query_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.skills_pool.skills_pool_operational_query_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.skills_pool.skills_pool_operator_commands_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.skills_pool.skills_pool_recovery_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.skills_pool.skills_pool_rollback_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.skills_pool.skills_pool_rollout_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.spaces.space_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.system_config.device_config_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.system_config.system_config_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.task.task_grant_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.task.task_loop_callback_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.task.task_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.user_list.user_list_service_protocol  # Protocol defined in its owning core module, re-exported here
  - agentclaw.community.core.work_orders.work_order_service_protocol  # Protocol defined in its owning core module, re-exported here
```

### Change impact

Adding a Protocol method is a contract change. Every concrete service
that backs that Protocol must implement it; the conformance test will
fail otherwise. Removing or renaming a method is a breaking change for
every consumer (adapter / CLI / RPC) — coordinate with downstream.
Field shape in adapter-owned types (e.g. `AuthenticatedUser`) lives
under `adapters/http/`, not here.

`SkillMetadataParserProtocol` is additive. Its current concrete implementation
is `SkillParser`; `LocalSkillUploadService` consumes the core-owned form of the
same contract, while folder import, Git import and Draft/publication validation
can adopt it without changing the parser wire. It adds no deployment config.
The 100-character name and 65,535-byte description limits match the current
`ac_skill` persistence columns. Legacy no-frontmatter upload remains a
compatibility fallback outside the canonical Protocol; removing that fallback
would require a separate migration and compatibility review.
