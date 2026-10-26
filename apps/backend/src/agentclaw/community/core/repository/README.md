# `agentclaw.community.core.repository`

Every repository contract and every repository implementation in the backend,
grouped by domain. Persistence is the one role this package serves; it holds no
services, no routers, and no domain policy.

```text
core/repository/
├── protocols/        the contracts — @abstractmethod throughout, TYPE_CHECKING-only domain imports
└── implementations/  the ORM bodies — each declaring its Protocol(s) as a base
```

Repositories are not plugins. Each has exactly one implementation, and database selection remains the `DatabasePlugin` injected into the constructor.
Space writers additionally share the narrow transaction extension described below. See `plugins/README.md` for the test that
puts them here rather than there.

## Domains

Fifteen domains, mirrored on both sides. A domain is the consumer seam, not
the table: single-repository domains merge into the sibling that shares their
consumer, so `protocols/<domain>.py` and `implementations/<domain>/` always name
the same thing.

| Domain | Serves |
| --- | --- |
| `bot` | bot_management, bot_public, bot_collaborator, mcp |
| `chat` | bot_chat, expert_chat, channel |
| `skill_center` | skill_center, including local skill cleanup |
| `skills_pool` | skills_pool layout, rollout, quarantine |
| `governance` | economy governance tickets and notify log |
| `harness` | harness diagnostics, runs, patches |
| `platform` | task_queue, quality, resources, session_resources |
| `identity` | access, caller_identity, user_list |
| `devices` | device bindings |
| `publishing` | service_bot |
| `config` | system_config, common_config |
| `task` | task persistence, BBS Topic/Post content, and task-domain feedback |
| `spaces` | spaces and space membership |
| `market_favorites` | space-scoped market favorites |
| `work_orders` | approval work orders and recipient notifications |

`protocols/bot/` is a package rather than a module only because the single file
would exceed the Rule 9 size cap; its `__init__.py` re-exports every contract, so
importers see one module either way.

`protocols/task/` is likewise a package. `core.py` holds the original task
persistence contracts, while `forum.py` and `feedback.py` hold the BBS content
and task feedback contracts. `implementations/task/` mirrors that package
layout.

## Compatibility locators and immutable resource keys

Bot-owned resource reads/writes in chat, quality tasks, whitelist and ORM task
discovery resolve a legacy `(bot_id, owner_id)` locator to the live Bot's immutable
entity before using the existing resource column. The column name remains
`owner_id`; it is not a current-person foreign key. Tenant/environment filtering
remains in persistence. Ambiguous live matches raise an error. Detached historical
records preserve their explicit locator; database errors never trigger fallback.
Owner-only administrative lists can match a current Owner through a set-based Bot
subquery, while a complete Bot address uses the resolved exact entity.

Collaborator queries use the existing `bot_pk`, including bulk lists. Their
`owner_id` remains a historical snapshot. Device binding lookup accepts the same
owner/entity locator and detects ambiguity even when one Bot has no binding.
Neither repository operation grants caller permissions.

## The contract is enforceable

Every Protocol member carries `@abstractmethod` and every implementation declares
its Protocol as a base. An implementation that omits a member therefore fails at
construction, naming what is missing:

```
TypeError: Can't instantiate abstract class BotChatDbRepository without an
implementation for abstract method 'list_sessions'
```

That failure is the point of the package. Before it existed, the link between a
contract and its implementation lived only in a DI `binder.bind()` call, so a
Protocol could declare members nothing implemented and nothing in CI noticed.

`tests/community/architecture/test_repository_contracts.py` holds the line: every
member abstract, every implementation based, no runtime domain import in
`protocols/`, and contracts and bodies never sharing a path.

## Why `protocols/` imports nothing at runtime

Domain services import these Protocols at **runtime** — `injector` resolves
constructor annotations through `typing.get_type_hints()`, so a
`TYPE_CHECKING`-only import on that side would break DI. The reverse direction
must therefore stay type-only: ten domain `__init__.py` files eagerly import their
services, so a runtime import from `protocols/` back into a domain closes an
import cycle through package initialization.

Hence the rule, enforced by the guard: `from __future__ import annotations` plus
`if TYPE_CHECKING:` for every domain type. A signature default that must be
evaluated at runtime uses the typeshed `= ...` idiom with the real default named
in a comment, rather than an exception to the rule.

## Space persistence effects (internal transaction contract)

`SpacePersistenceEffects` is not a public Service API, remote Plugin API, event
bus or generic unit of work. Space, maintenance, work-order approval and the
Skill Space creator receive the same singleton policy from the composition root.
It only locks the source, records membership, or reserves a new Space identity
using the **caller-owned Session**. It never commits independently or performs
network/queue I/O. Permission checks and Skill publication/grant policy stay with
their existing owners.

`NoKnowledgeSpacePersistenceEffects` is the default: source locking only, no
knowledge imports, outbox queries, capture UPDATE or identity effects.
`KnowledgeSpacePersistenceEffects` reuses the original source write guard,
snapshot admission and retained-ID protection. The lifecycle wrapper validates
configuration/history after DB bootstrap or before the first library writer;
constructors perform no I/O. Its producer guard uses the same policy instance,
so calling synchronization without HTTP lifespan cannot bypass validation.

All members of this internal protocol are abstract, implementations explicitly
inherit it, and domain imports in the protocol are type-only like other
repository contracts. Behavioral tests cover missing-table business operations,
no-op effects without a DB, and real same-transaction capture rollback.
`test_space_persistence_boundary.py` rejects direct knowledge implementation
imports in the finite set of legacy writers. There is no new CI exemption.

## Context Boundary

```yaml
purpose: "Every repository contract (protocols/) and implementation (implementations/) in the backend, grouped by domain. Persistence only — no services, no routers, no domain policy."
provides:
  - SpacePersistenceEffects
  - SpaceKnowledgeSyncRepository
  - SpaceKnowledgeSyncRepositoryProtocol
  - BuildIgnoreRepository
  - BuildIgnoreRepositoryProtocol
  - BotCommonConfigRepository
  - BotCommonConfigRepositoryProtocol
  - DigitalEmployeeRepository
  # bot_message_feedback
  - BotMessageFeedbackRepository
  - BotMessageFeedbackRepositoryProtocol
  # Contracts — protocols/<domain>.py. This is the surface domain services
  # import at runtime for DI; a change here is a change to their constructors.
  # bot
  - BotAppGrantRepositoryProtocol
  - UserAppGrantRepositoryProtocol
  - BotCollabLockRepositoryProtocol
  - SourceCredentialRepositoryProtocol
  - BotCollabLogRepositoryProtocol
  - BotConfigManifestRepositoryProtocol
  - BotConfigManifestRepository
  - BotConfigManifestApplyRepositoryProtocol
  - BotConfigManifestApplyRepository
  - BotConfigManifestApplyLockRepositoryProtocol
  - BotConfigManifestApplyLockRepository
  - BotFriendRepositoryProtocol
  - BotRepository
  - BotRestartLockRepositoryProtocol
  - BotCliToolRepositoryProtocol
  - BotStartupScriptRepositoryProtocol
  - CollaboratorRepositoryProtocol
  - ContentStoreRepositoryProtocol
  - ContentStoreRepository
  - GenomeRevisionRepositoryProtocol
  - GenomeRevisionRepository
  - GenomeRefRepositoryProtocol
  - GenomeRefRepository
  - GenomePatchRepositoryProtocol
  - GenomePatchRepository
  - GenomePromotionRepositoryProtocol
  - GenomePromotionRepository
  - RenderScreenRepository
  - TemplateRepository
  - UserMCPConfigRepository
  - BotMCPConfigRepositoryProtocol
  - BotMCPConfigRepository
  - ScopedMCPConfigRepositoryProtocol
  - ScopedMCPConfigRepository
  - SourceCredentialRepository
  - CapabilityDesiredStateRepositoryProtocol
  - CapabilityDesiredStateRepository
  - MCPDefaultExclusionReaderProtocol
  - SkillEditorRequestRepositoryProtocol
  # task / forum
  - ForumRepositoryProtocol
  - ForumTopicTagRepositoryProtocol
  - ForumTopicTagRepository
  - ForumDigestReportRepositoryProtocol
  # task / feedback
  - FeedbackRepositoryProtocol
  # spaces / market_favorites
  - SpaceRepositoryProtocol
  - SpaceManagementRepositoryProtocol
  - MarketFavoriteRepositoryProtocol
  - WorkOrderRepositoryProtocol
  # chat
  - BotChatDbRepositoryProtocol
  - ChannelRepository
  - ExpertChatInstanceRepository
  - ExpertChatRepository
  - OpenBotChatRepositoryProtocol
  # config
  - CommonConfigRepositoryProtocol
  - ConfigRepositoryProtocol
  # devices
  - DeviceBindingRepository
  - OssToNasRecordRepository
  # governance
  - AuditRepositoryProtocol
  - NotifyLogRepositoryProtocol
  - TaskRecordRepositoryProtocol
  - WhitelistRepositoryProtocol
  # harness
  - HarnessPatchRecordRepository
  - HarnessPatchRepository
  - HarnessScanRecordRepository
  - HarnessTemplateRepository
  # identity
  - CallerIdentityRepositoryProtocol
  - ExecutionIdentityRepositoryProtocol
  - PolicyRepository
  - UserListRepositoryProtocol
  # platform
  - QualityTaskRepository
  - ResourceRepositoryProtocol
  - SessionResourceRepositoryProtocol
  - TaskQueueRepositoryProtocol
  # task
  - TaskInfoRepositoryProtocol
  - TaskGraphRepositoryProtocol
  - TaskActionLogRepositoryProtocol
  - TaskNodeRepositoryProtocol
  - TaskNodeRunInfoRepositoryProtocol
  - TaskNodeRelationRepositoryProtocol
  - TaskCallbackRepositoryProtocol
  - TaskCallbackCorrelationRepositoryProtocol
  - TaskTrajectoryRepositoryProtocol
  - TaskDiscoveryLockRepositoryProtocol
  - TaskArtifactRepositoryProtocol
  # publishing
  - BotPublishRepositoryProtocol
  - PublishOperationRepository    # an ABC, not a Protocol — same role, same surface
  # skill_center
  - CenterSkillAccessRepositoryProtocol
  - SkillCategoryRepository
  - SkillCenterSyncLogRepository
  - SkillMemberRepository
  - SkillPropagationLogRepository
  - SkillRepository
  - SpaceSkillRepository
  - SpaceSkillDraftRepository
  - SpaceSkillReadRepository
  - SpaceSkillVersionReadRepository
  - SpaceSkillOfflineRepositoryProtocol
  - SpaceSkillOfflineRepository
  - SkillEditorRequestRepository
  - DraftEditLeaseRepository
  - SkillVersionRepositoryProtocol
  - SkillVersionMaterializationRepositoryProtocol
  - SpaceSkillPublicationRepositoryProtocol
  - SkillSetRepository
  - SkillCenterReferenceRepositoryProtocol
  - LocalSkillSecurityObservationRepositoryProtocol
  - TrackLatestRepositoryProtocol
  # skills_pool
  - QuarantineRepositoryProtocol
  - SkillsPoolLayoutRepositoryProtocol
  - SkillsPoolRolloutRepositoryProtocol
  - SkillsPoolSkillRepositoryProtocol
  # Implementations — implementations/<domain>/. Bound by DI, never constructed
  # directly outside di/modules/. Names repeat above where impl and contract share one.
  # bot
  - BotAppGrantRepository
  - UserAppGrantRepository
  - BotCollabLockRepository
  - BotCollabLogRepository
  - BotFriendRepository
  - BotRestartLockRepository
  - BotCliToolRepository
  - BotStartupScriptRepository
  - CollaboratorRepository
  # task / forum
  - ForumRepository
  - ForumDigestReportRepository
  # task / feedback
  - FeedbackRepository
  # spaces / market_favorites
  - SpaceRepository
  - SpaceManagementRepository
  - MarketFavoriteRepository
  - WorkOrderRepository
  # chat
  - BotChatDbRepository
  - OpenBotChatRepository
  # config
  - CommonConfigRepository
  - ConfigRepository
  # devices
  - DeviceRepository
  # governance
  - GovernanceAuditRepository
  - GovernanceWhitelistRepository
  - NotifyLogRepository
  - TaskRecordRepository
  # identity
  - CallerIdentityRepository
  - ExecutionIdentityRepository
  - UserListRepository
  # platform
  - ResourceRepository
  - SessionResourceRepository
  - TaskQueueRepository
  # publishing
  - BotPublishRepository
  - OrmPublishOperationRepository
  # skill_center
  - SpaceSkillRepository
  - SkillVersionRepository
  - SpaceSkillPublicationRepository
  - SkillCenterReferenceRepository
  - LocalSkillSecurityObservationRepository
  - TrackLatestRepository
  # skills_pool
  - SkillsPoolLayoutRepository
  - SkillsPoolRolloutRepository
  # task
  - TaskInfoRepository
  - TaskGraphRepository
  - TaskActionLogRepository
  - TaskNodeRepository
  - TaskNodeRunInfoRepository
  - TaskNodeRelationRepository
  - TaskCallbackRepository
  - TaskCallbackCorrelationRepository
  - TaskTrajectoryRepository
  - TaskDiscoveryLockRepository
  - TaskArtifactRepository
consumes:
  - DatabasePlugin                # the per-profile session seam, injected into every implementation
  - get_current_env               # environment scoping (utils.env_utils)
  - get_server_host               # ditto
  - get_current_avernet_tenant    # tenant scoping (utils.avernet_tenant)
internal_dependencies:
  - agentclaw.community.core.space_knowledge.capture_guard
  - agentclaw.community.core.space_knowledge.repository
  - agentclaw.community.plugin_api.space_knowledge
  - agentclaw.community.plugin_api.space_membership
  - agentclaw.community.kernel.build_ignore
  - agentclaw.community.core.digital_employee.contracts
  - agentclaw.community.plugin_api.database    # DatabasePlugin — the injected session seam
  - agentclaw.community.plugin_api.models
  - agentclaw.community.core.models
  - agentclaw.community.core.access
  - agentclaw.community.core.bot_app_grant
  - agentclaw.community.core.bot_chat
  - agentclaw.community.core.bot_collaborator
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.bot_message_feedback  # BotMessageFeedback ORM model + record DTO used by the repository
  - agentclaw.community.core.bot_public
  - agentclaw.community.core.bot_config_manifest
  - agentclaw.community.core.bot_genome.repository.models  # the five ac_genome_* ORM models and row records
  - agentclaw.community.core.content_store.models  # the ac_manifest_content ORM model and record behind the content store repository
  - agentclaw.community.core.bot_startup_script
  - agentclaw.community.core.bot_config_manifest
  - agentclaw.community.core.caller_identity
  - agentclaw.community.core.channel
  - agentclaw.community.core.common_config
  - agentclaw.community.core.devices
  - agentclaw.community.core.economy
  - agentclaw.community.core.execution_identity
  - agentclaw.community.core.expert_chat
  - agentclaw.community.core.errors
  - agentclaw.community.core.task.feedback
  - agentclaw.community.core.task.forum
  - agentclaw.community.core.harness
  - agentclaw.community.core.quality
  - agentclaw.community.core.service_bot
  - agentclaw.community.core.session_resources
  - agentclaw.community.core.spaces
  - agentclaw.community.core.market_favorites
  - agentclaw.community.core.work_orders
  - agentclaw.community.core.skill_center
  - agentclaw.community.core.skills_pool
  - agentclaw.community.core.system_config
  - agentclaw.community.core.task
  - agentclaw.community.core.task_queue
  - agentclaw.community.core.user_list
  - agentclaw.community.core.workspace
  - agentclaw.community.log
  - agentclaw.community.utils.avernet_tenant
  - agentclaw.community.utils.env_utils
```

### Change impact

Adding a member to a Protocol obliges its implementation in the same commit —
otherwise every profile's injector refuses to construct the class. That is the
intended cost.

Moving a repository between domains changes a module path that `corp/ocb` imports.
`specs/2026-08-05-core-repository-consolidation/path-map.md` is the artifact those
rewrites are driven from.

Changing the Space persistence constructor affects direct library callers and
corporate composition roots: pass an explicit policy, never an optional fallback.
See `core/space_knowledge/README.md` for capture/delivery rollout and rollback.
