# Path map — repository consolidation into `core/repository/`

Old module path → new module path for everything this change moved.
**This is the artifact the `corp/ocb` side is updated from** (spec R6): `ocb`
imports these paths, and the change lands as one squash-merged commit, so the
two sides must move together.

Generated from the tree, not hand-written. No class was renamed — only module
paths change, so every row is a pure path rewrite of an unchanged symbol.

## 1. Repository Protocols (46)

Contracts were scattered across nine filename conventions; they now sit in
`core/repository/protocols/<domain>.py`. Two are newly authored.

| Symbol | Old module | New module |
| --- | --- | --- |
| `BotCollabLockRepositoryProtocol` | `agentclaw.community.core.bot_collaborator.repository.protocol` | `agentclaw.community.core.repository.protocols.bot` |
| `BotCollabLogRepositoryProtocol` | `agentclaw.community.core.bot_collaborator.repository.protocol` | `agentclaw.community.core.repository.protocols.bot` |
| `BotFriendRepositoryProtocol` | `agentclaw.community.core.bot_public.repository.bot_friend_repository` | `agentclaw.community.core.repository.protocols.bot` |
| `BotRepository` | `agentclaw.community.core.bot_management.repository.protocol` | `agentclaw.community.core.repository.protocols.bot` |
| `BotRestartLockRepositoryProtocol` | `agentclaw.community.core.bot_management.repository.protocol` | `agentclaw.community.core.repository.protocols.bot` |
| `CollaboratorRepositoryProtocol` | `agentclaw.community.core.bot_collaborator.repository.protocol` | `agentclaw.community.core.repository.protocols.bot` |
| `RenderScreenRepository` | `agentclaw.community.core.bot_management.render_screen.repositories` | `agentclaw.community.core.repository.protocols.bot` |
| `TemplateRepository` | `agentclaw.community.core.bot_management.repository.template_repository_protocol` | `agentclaw.community.core.repository.protocols.bot` |
| `UserMCPConfigRepository` | `agentclaw.community.core.mcp.services.repositories` | `agentclaw.community.core.repository.protocols.bot` |
| `BotChatDbRepositoryProtocol` | — *(new contract, authored by this change)* | `agentclaw.community.core.repository.protocols.chat` |
| `ChannelRepository` | `agentclaw.community.core.channel.services.repositories` | `agentclaw.community.core.repository.protocols.chat` |
| `ExpertChatInstanceRepository` | `agentclaw.community.core.expert_chat.repository.expert_chat_instance_repository` | `agentclaw.community.core.repository.protocols.chat` |
| `ExpertChatRepository` | `agentclaw.community.core.expert_chat.repository.expert_chat_repository` | `agentclaw.community.core.repository.protocols.chat` |
| `OpenBotChatRepositoryProtocol` | — *(new contract, authored by this change)* | `agentclaw.community.core.repository.protocols.chat` |
| `LocalSkillCleanupRepository` | `agentclaw.community.plugin_api.local_skill_cleanup` | `agentclaw.community.core.repository.protocols.skill_center` |
| `SkillCategoryRepository` | `agentclaw.community.core.skill_center.services.repositories` | `agentclaw.community.core.repository.protocols.skill_center` |
| `SkillCenterSyncLogRepository` | `agentclaw.community.core.skill_center.services.skill_center_sync_service` | `agentclaw.community.core.repository.protocols.skill_center` |
| `SkillMemberRepository` | `agentclaw.community.core.skill_center.services.repositories` | `agentclaw.community.core.repository.protocols.skill_center` |
| `SkillPropagationLogRepository` | `agentclaw.community.core.skill_center.services.skill_propagation_service` | `agentclaw.community.core.repository.protocols.skill_center` |
| `SkillRepository` | `agentclaw.community.core.skill_center.services.repositories` | `agentclaw.community.core.repository.protocols.skill_center` |
| `SkillSetRepository` | `agentclaw.community.core.skill_center.services.repositories` | `agentclaw.community.core.repository.protocols.skill_center` |
| `QuarantineRepositoryProtocol` | `agentclaw.community.core.skills_pool.quarantine` | `agentclaw.community.core.repository.protocols.skills_pool` |
| `SkillsPoolLayoutRepositoryProtocol` | `agentclaw.community.core.skills_pool.repository.protocol` | `agentclaw.community.core.repository.protocols.skills_pool` |
| `SkillsPoolRolloutRepositoryProtocol` | `agentclaw.community.core.skills_pool.rollout_repository` | `agentclaw.community.core.repository.protocols.skills_pool` |
| `SkillsPoolSkillRepositoryProtocol` | `agentclaw.community.core.skills_pool.ports` | `agentclaw.community.core.repository.protocols.skills_pool` |
| `AuditRepositoryProtocol` | `agentclaw.community.core.economy.governance.domain.protocols` | `agentclaw.community.core.repository.protocols.governance` |
| `NotifyLogRepositoryProtocol` | `agentclaw.community.core.economy.governance.domain.protocols` | `agentclaw.community.core.repository.protocols.governance` |
| `TaskRecordRepositoryProtocol` | `agentclaw.community.core.economy.governance.domain.protocols` | `agentclaw.community.core.repository.protocols.governance` |
| `WhitelistRepositoryProtocol` | `agentclaw.community.core.economy.governance.domain.protocols` | `agentclaw.community.core.repository.protocols.governance` |
| `HarnessPatchRecordRepository` | `agentclaw.community.core.harness.repository_protocol` | `agentclaw.community.core.repository.protocols.harness` |
| `HarnessPatchRepository` | `agentclaw.community.core.harness.repository_protocol` | `agentclaw.community.core.repository.protocols.harness` |
| `HarnessScanRecordRepository` | `agentclaw.community.core.harness.repository_protocol` | `agentclaw.community.core.repository.protocols.harness` |
| `HarnessTemplateRepository` | `agentclaw.community.core.harness.repository_protocol` | `agentclaw.community.core.repository.protocols.harness` |
| `QualityTaskRepository` | `agentclaw.community.core.quality.repositories` | `agentclaw.community.core.repository.protocols.platform` |
| `ResourceRepositoryProtocol` | `agentclaw.community.core.resources.repository.protocol` | `agentclaw.community.core.repository.protocols.platform` |
| `SessionResourceRepositoryProtocol` | `agentclaw.community.core.session_resources.repository.protocol` | `agentclaw.community.core.repository.protocols.platform` |
| `TaskQueueRepositoryProtocol` | `agentclaw.community.core.task_queue.repository.protocol` | `agentclaw.community.core.repository.protocols.platform` |
| `CallerIdentityRepositoryProtocol` | `agentclaw.community.core.caller_identity.repository` | `agentclaw.community.core.repository.protocols.identity` |
| `PolicyRepository` | `agentclaw.community.core.access.repository` | `agentclaw.community.core.repository.protocols.identity` |
| `UserListRepositoryProtocol` | `agentclaw.community.core.user_list.repository` | `agentclaw.community.core.repository.protocols.identity` |
| `DeviceBindingRepository` | `agentclaw.community.core.devices.repository.protocol` | `agentclaw.community.core.repository.protocols.devices` |
| `OssToNasRecordRepository` | `agentclaw.community.core.devices.repository.protocol` | `agentclaw.community.core.repository.protocols.devices` |
| `BotPublishRepositoryProtocol` | `agentclaw.community.core.service_bot.repository.bot_publish_repository` | `agentclaw.community.core.repository.protocols.publishing` |
| `PublishOperationRepository` | `agentclaw.community.core.service_bot.repository.publish_operation_repository` | `agentclaw.community.core.repository.protocols.publishing` |
| `CommonConfigRepositoryProtocol` | `agentclaw.community.core.common_config.repository.protocol` | `agentclaw.community.core.repository.protocols.config` |
| `ConfigRepositoryProtocol` | `agentclaw.community.core.system_config.repository` | `agentclaw.community.core.repository.protocols.config` |

## 2. Repository implementations (43 modules, 44 classes)

| Old module | New module |
| --- | --- |
| `agentclaw.community.plugins.bot_repository` | `agentclaw.community.core.repository.implementations.bot.bot` |
| `agentclaw.community.plugins.bot_collab_lock_repository` | `agentclaw.community.core.repository.implementations.bot.collab_lock` |
| `agentclaw.community.plugins.bot_collab_log_repository` | `agentclaw.community.core.repository.implementations.bot.collab_log` |
| `agentclaw.community.plugins.bot_collaborator_repository` | `agentclaw.community.core.repository.implementations.bot.collaborator` |
| `agentclaw.community.plugins.bot_friend_repository` | `agentclaw.community.core.repository.implementations.bot.friend` |
| `agentclaw.community.plugins.render_screen_repository` | `agentclaw.community.core.repository.implementations.bot.render_screen` |
| `agentclaw.community.plugins.bot_restart_lock_repository` | `agentclaw.community.core.repository.implementations.bot.restart_lock` |
| `agentclaw.community.plugins.template_repository` | `agentclaw.community.core.repository.implementations.bot.template` |
| `agentclaw.community.plugins.user_mcp_config_repository` | `agentclaw.community.core.repository.implementations.bot.user_mcp_config` |
| `agentclaw.community.plugins.channel_repository` | `agentclaw.community.core.repository.implementations.chat.channel` |
| `agentclaw.community.core.bot_chat.repository.product` | `agentclaw.community.core.repository.implementations.chat.db` |
| `agentclaw.community.plugins.expert_chat_repository` | `agentclaw.community.core.repository.implementations.chat.expert_chat` |
| `agentclaw.community.plugins.expert_chat_instance_repository` | `agentclaw.community.core.repository.implementations.chat.expert_chat_instance` |
| `agentclaw.community.core.bot_chat.repository.open` | `agentclaw.community.core.repository.implementations.chat.open` |
| `agentclaw.community.core.common_config.repository.common_config_repository` | `agentclaw.community.core.repository.implementations.config.common_config` |
| `agentclaw.community.plugins.config_repository` | `agentclaw.community.core.repository.implementations.config.config` |
| `agentclaw.community.plugins.device_repository` | `agentclaw.community.core.repository.implementations.devices.device` |
| `agentclaw.community.plugins.oss_to_nas_record_repository` | `agentclaw.community.core.repository.implementations.devices.oss_to_nas_record` |
| `agentclaw.community.core.economy.governance.repositories.audit_repo` | `agentclaw.community.core.repository.implementations.governance.audit` |
| `agentclaw.community.core.economy.governance.repositories.notify_log_repo` | `agentclaw.community.core.repository.implementations.governance.notify_log` |
| `agentclaw.community.core.economy.governance.repositories.task_record_repo` | `agentclaw.community.core.repository.implementations.governance.task_record` |
| `agentclaw.community.core.economy.governance.repositories.whitelist_repo` | `agentclaw.community.core.repository.implementations.governance.whitelist` |
| `agentclaw.community.plugins.harness_patch_repository` | `agentclaw.community.core.repository.implementations.harness.patch` |
| `agentclaw.community.plugins.harness_patch_record_repository` | `agentclaw.community.core.repository.implementations.harness.patch_record` |
| `agentclaw.community.plugins.harness_scan_repository` | `agentclaw.community.core.repository.implementations.harness.scan` |
| `agentclaw.community.plugins.harness_repository` | `agentclaw.community.core.repository.implementations.harness.template` |
| `agentclaw.community.plugins.caller_identity_repository` | `agentclaw.community.core.repository.implementations.identity.caller_identity` |
| `agentclaw.community.plugins.policy_repository` | `agentclaw.community.core.repository.implementations.identity.policy` |
| `agentclaw.community.plugins.user_list_repository` | `agentclaw.community.core.repository.implementations.identity.user_list` |
| `agentclaw.community.plugins.quality_repository` | `agentclaw.community.core.repository.implementations.platform.quality` |
| `agentclaw.community.plugins.resource_repository` | `agentclaw.community.core.repository.implementations.platform.resource` |
| `agentclaw.community.plugins.session_resource_repository` | `agentclaw.community.core.repository.implementations.platform.session_resource` |
| `agentclaw.community.plugins.task_queue_repository` | `agentclaw.community.core.repository.implementations.platform.task_queue` |
| `agentclaw.community.plugins.bot_publish_repository` | `agentclaw.community.core.repository.implementations.publishing.bot_publish` |
| `agentclaw.community.plugins.publish_operation_repository` | `agentclaw.community.core.repository.implementations.publishing.publish_operation` |
| `agentclaw.community.plugins.skill_category_repository` | `agentclaw.community.core.repository.implementations.skill_center.category` |
| `agentclaw.community.plugins.local_skill_cleanup_repository` | `agentclaw.community.core.repository.implementations.skill_center.local_skill_cleanup` |
| `agentclaw.community.plugins.skill_member_repository` | `agentclaw.community.core.repository.implementations.skill_center.member` |
| `agentclaw.community.plugins.skill_propagation_log_repository` | `agentclaw.community.core.repository.implementations.skill_center.propagation_log` |
| `agentclaw.community.plugins.skill_repository` | `agentclaw.community.core.repository.implementations.skill_center.skill` |
| `agentclaw.community.plugins.skill_center_sync_log_repository` | `agentclaw.community.core.repository.implementations.skill_center.sync_log` |
| `agentclaw.community.plugins.skills_pool_layout_repository` | `agentclaw.community.core.repository.implementations.skills_pool.layout` |
| `agentclaw.community.plugins.skills_pool_rollout_repository` | `agentclaw.community.core.repository.implementations.skills_pool.rollout` |

## 3. Support modules — mixins and helpers, no Protocol (7)

These fail the repository test (no `DatabasePlugin`, no DI binding) and
relocate beside the composite they serve. See
[#912](https://github.com/inclusionAI/Avernet/issues/912) for the
`SkillsPoolLayoutRepository` decomposition this change deliberately does not do.

| Old module | New module |
| --- | --- |
| `agentclaw.community.core.economy.governance.repositories.task_record_query` | `agentclaw.community.core.repository.implementations.governance.task_record_query` |
| `agentclaw.community.plugins.skills_pool_cutover_diagnostics` | `agentclaw.community.core.repository.implementations.skills_pool.cutover_diagnostics` |
| `agentclaw.community.plugins.skills_pool_capability_repository` | `agentclaw.community.core.repository.implementations.skills_pool.layout_capability` |
| `agentclaw.community.plugins.skills_pool_operational_repository` | `agentclaw.community.core.repository.implementations.skills_pool.layout_operational` |
| `agentclaw.community.plugins.skills_pool_layout_persistence` | `agentclaw.community.core.repository.implementations.skills_pool.layout_persistence` |
| `agentclaw.community.plugins.skills_pool_post_cutover_repository` | `agentclaw.community.core.repository.implementations.skills_pool.layout_post_cutover` |
| `agentclaw.community.plugins.skills_pool_quarantine_repository` | `agentclaw.community.core.repository.implementations.skills_pool.layout_quarantine` |

## 4. Relocated outside `core/repository/` (2)

| Old module | New module | Why |
| --- | --- | --- |
| `agentclaw.community.plugins.skills_pool_runtime` | `agentclaw.community.core.skills_pool.runtime` | Transport client — injects no `DatabasePlugin` and does no persistence. Its Protocol already lived in `core/skills_pool/ports.py`. |
| `agentclaw.community.core.economy.governance.repositories.orm` | `agentclaw.community.core.economy.governance.orm` | Four `Base` subclasses imported by three `domain/` modules — domain-owned ORM models, not repository code. |

## 5. Shared ORM models out of the local-profile plugin package (5 classes)

The Rule-14 layering violation the spec called out (R7), fixed at its root
rather than allowlisted. Table names and columns are unchanged.

| Class | Old module | New module |
| --- | --- | --- |
| `EntityDeviceBinding` | `agentclaw.community.plugins.local.sqlite_models` | `agentclaw.community.core.devices.repository.models` |
| `DefaultSkillsetMcpExclusion` | `agentclaw.community.plugins.local.sqlite_models` | `agentclaw.community.core.skill_center.orm` |
| `DefaultSkillsetSkillExclusion` | `agentclaw.community.plugins.local.sqlite_models` | `agentclaw.community.core.skill_center.orm` |
| `AcConfigCategory` | `agentclaw.community.plugins.local.system_config_models` | `agentclaw.community.core.system_config.orm` |
| `AcConfigItem` | `agentclaw.community.plugins.local.system_config_models` | `agentclaw.community.core.system_config.orm` |

Both old modules are **deleted**. They existed to be imported for their
table-registration side effect, so `plugins/local/database.py` follows them.

## 6. Domain types separated out of Protocol source files (6 classes)

Records and errors that lived inside contract modules, moved so
`protocols/` can be contract-only.

| Class | Old module | New module |
| --- | --- | --- |
| `QualityTaskRecord` | `agentclaw.community.core.quality.repositories` | `agentclaw.community.core.quality.models` |
| `ChannelRecord` | `agentclaw.community.core.channel.services.repositories` | `agentclaw.community.core.channel.models` |
| `CallerIdentityLockMismatchError` | `agentclaw.community.core.caller_identity.repository` | `agentclaw.community.core.caller_identity.contracts` |
| `CallerIdentityEngineChangedError` | `agentclaw.community.core.caller_identity.repository` | `agentclaw.community.core.caller_identity.contracts` |
| `ActiveSkillSetReferenceError` | `agentclaw.community.core.skill_center.services.repositories` | `agentclaw.community.core.skill_center.errors` |
| `BotLookupAmbiguousError` | `agentclaw.community.core.bot_management.repository.protocol` | `agentclaw.community.core.bot_management.errors` |

## 7. Deleted modules

Emptied by the move. Any `ocb` import of these must be re-pointed via the
tables above.

```text
agentclaw.community.core.access.repository
agentclaw.community.core.bot_collaborator.repository.protocol
agentclaw.community.core.bot_management.render_screen.repositories
agentclaw.community.core.bot_management.repository.protocol
agentclaw.community.core.bot_management.repository.template_repository_protocol
agentclaw.community.core.bot_public.repository.bot_friend_repository
agentclaw.community.core.caller_identity.repository
agentclaw.community.core.channel.services.repositories
agentclaw.community.core.common_config.repository.protocol
agentclaw.community.core.devices.repository.protocol
agentclaw.community.core.economy.governance.domain.protocols
agentclaw.community.core.expert_chat.repository.expert_chat_instance_repository
agentclaw.community.core.expert_chat.repository.expert_chat_repository
agentclaw.community.core.harness.repository_protocol
agentclaw.community.core.quality.repositories
agentclaw.community.core.resources.repository.protocol
agentclaw.community.core.service_bot.repository.bot_publish_repository
agentclaw.community.core.service_bot.repository.publish_operation_repository
agentclaw.community.core.session_resources.repository.protocol
agentclaw.community.core.skill_center.services.repositories
agentclaw.community.core.skills_pool.repository.protocol
agentclaw.community.core.skills_pool.rollout_repository
agentclaw.community.core.system_config.repository
agentclaw.community.core.task_queue.repository.protocol
agentclaw.community.core.user_list.repository
agentclaw.community.plugin_api.local_skill_cleanup
agentclaw.community.plugins.local.sqlite_models
agentclaw.community.plugins.local.system_config_models
```

Five modules were **trimmed, not deleted** — they kept other content and are
still importable: `core.skills_pool.ports`, `core.skills_pool.quarantine`,
`core.mcp.services.repositories`, `core.skill_center.services.skill_center_sync_service`,
`core.skill_center.services.skill_propagation_service`.

## 8. Test tree

`tests/community/plugins/test_*.py` → `tests/community/repository/<domain>/`,
44 modules. Four stayed: `test_http_client.py` (genuine plugin),
`test_passport_save_sub_resources.py`, `test_avernet_tenant_guard.py`,
`test_resource_tenant_guard.py` (cross-cutting).
