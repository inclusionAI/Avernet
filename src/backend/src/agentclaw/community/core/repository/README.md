# `agentclaw.community.core.repository`

Every repository contract and every repository implementation in the backend,
grouped by domain. Persistence is the one role this package serves; it holds no
services, no routers, and no domain policy.

```text
core/repository/
├── protocols/        the contracts — @abstractmethod throughout, TYPE_CHECKING-only domain imports
└── implementations/  the ORM bodies — each declaring its Protocol(s) as a base
```

Repositories are not plugins. Each has exactly one implementation, and the only
per-profile difference is the `DatabasePlugin` injected into its constructor — one
layer below, where the swap belongs. See `plugins/README.md` for the test that
puts them here rather than there.

## Domains

Eleven subdirectories, mirrored on both sides. A domain is the consumer seam, not
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

`protocols/bot/` is a package rather than a module only because the single file
would exceed the Rule 9 size cap; its `__init__.py` re-exports every contract, so
importers see one module either way.

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

## Context Boundary

```yaml
purpose: "Every repository contract (protocols/) and implementation (implementations/) in the backend, grouped by domain. Persistence only — no services, no routers, no domain policy."
provides:
  # Contracts — protocols/<domain>.py. This is the surface domain services
  # import at runtime for DI; a change here is a change to their constructors.
  # bot
  - BotCollabLockRepositoryProtocol
  - BotCollabLogRepositoryProtocol
  - BotFriendRepositoryProtocol
  - BotRepository
  - BotRestartLockRepositoryProtocol
  - CollaboratorRepositoryProtocol
  - RenderScreenRepository
  - TemplateRepository
  - UserMCPConfigRepository
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
  - PolicyRepository
  - UserListRepositoryProtocol
  # platform
  - QualityTaskRepository
  - ResourceRepositoryProtocol
  - SessionResourceRepositoryProtocol
  - TaskQueueRepositoryProtocol
  # publishing
  - BotPublishRepositoryProtocol
  # skill_center
  - LocalSkillCleanupRepository
  - SkillCategoryRepository
  - SkillCenterSyncLogRepository
  - SkillMemberRepository
  - SkillPropagationLogRepository
  - SkillRepository
  - SkillSetRepository
  # skills_pool
  - QuarantineRepositoryProtocol
  - SkillsPoolLayoutRepositoryProtocol
  - SkillsPoolRolloutRepositoryProtocol
  - SkillsPoolSkillRepositoryProtocol
  # Implementations — implementations/<domain>/. Bound by DI, never constructed
  # directly outside di/modules/. Names repeat above where impl and contract share one.
  # bot
  - BotCollabLockRepository
  - BotCollabLogRepository
  - BotFriendRepository
  - BotRestartLockRepository
  - CollaboratorRepository
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
  - UserListRepository
  # platform
  - ResourceRepository
  - SessionResourceRepository
  - TaskQueueRepository
  # publishing
  - BotPublishRepository
  - OrmPublishOperationRepository
  # skill_center
  - SqlLocalSkillCleanupRepository
  # skills_pool
  - SkillsPoolLayoutRepository
  - SkillsPoolRolloutRepository
consumes:
  - DatabasePlugin                # the per-profile session seam, injected into every implementation
  - get_current_env               # environment scoping (utils.env_utils)
  - get_server_host               # ditto
  - get_current_avernet_tenant    # tenant scoping (utils.avernet_tenant)
internal_dependencies:
  - agentclaw.community.plugin_api.database    # DatabasePlugin — the injected session seam
  - agentclaw.community.plugin_api.models
  - agentclaw.community.core.models
  - agentclaw.community.core.access
  - agentclaw.community.core.bot_chat
  - agentclaw.community.core.bot_collaborator
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.bot_public
  - agentclaw.community.core.caller_identity
  - agentclaw.community.core.channel
  - agentclaw.community.core.common_config
  - agentclaw.community.core.devices
  - agentclaw.community.core.economy
  - agentclaw.community.core.expert_chat
  - agentclaw.community.core.harness
  - agentclaw.community.core.quality
  - agentclaw.community.core.service_bot
  - agentclaw.community.core.session_resources
  - agentclaw.community.core.skill_center
  - agentclaw.community.core.skills_pool
  - agentclaw.community.core.system_config
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
