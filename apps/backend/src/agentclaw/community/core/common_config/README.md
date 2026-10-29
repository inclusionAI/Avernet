# Common and Bot-scoped configuration

## Context Boundary

```yaml
purpose: Shared configuration, whitelist evaluation, and persisted Bot storage selection.
provides:
  - CommonConfigService
  - BotCommonConfigServiceProtocol
  - BotCommonConfigService
  - BotStoragePolicyProtocol
  - BotStoragePolicyService
  - StoragePolicy
consumes:
  - CommonConfigRepositoryProtocol
  - BotCommonConfigRepositoryProtocol
  - PolicyServiceProtocol
internal_dependencies:
  - agentclaw.community.core.common_config
  - agentclaw.community.core.repository.protocols
  - agentclaw.community.core.access
  - agentclaw.community.core.bot_management.engines
  - agentclaw.community.core.devices.services.baas_template_resolver
  - agentclaw.community.core.service_bot.services.deploy
  - agentclaw.community.core.workspace.constants
  - agentclaw.community.kernel
  - agentclaw.community.log
```

### Change impact

Bot storage policy is consumed by Bot creation and restart. Creation
initializes UPFS only; NAS does not write a policy. Restart/recovery only reads
saved choices. There is no type/stage/migration-path scope restriction: every
Bot payload reuses the pinned creation choice or the saved policy, and falls
back to NAS when no policy exists.
BotService calls the policy component directly before allocation; DeviceService
and DeviceServiceRouter expose no storage-policy preparation method. The managed
deploy composer delegates context/storage policy to this component; BaasService
only queries template data, forwards context and builds/sends the request.
Environment and provider/template/query collaborators are wired in DI.
Persistence uses the existing repository/DatabasePlugin boundary; existing global
config is unchanged. Invalid policy or unavailable policy reads fail closed,
except for the not-yet-deployed table, which is treated as no policy.

## Implementation and contract

- `bot_config_protocol.py`: generic JSON access and storage-policy Service APIs.
- `bot_config_service.py`: separate generic-config and storage-policy services;
  rollout evaluation is an internal helper, not a separate public API.
- Persistence stays in `core/repository`; wiring stays in `di/modules`.
- The unique key is `(bot_id, entity_id, env, config_key)`: a Bot may have
  multiple configuration keys, but each key has one current value, not a history.
- Generic configuration values use `JsonValue` (objects, arrays, JSON scalars or
  null); a missing row also returns `None`. Storage policy uses the existing
  `StorageType` enum internally; persisted JSON still uses `"nas"` / `"upfs"`.
- `source` records origin: `rollout` for new-creation rollout, `manual` for an
  operator-supplied policy, and empty for unspecified origin/legacy rows. It is
  not a creation result. The initializer requires a user ID; creation resolves
  owner ID from owner/entity/operator before calling it.
- One `storage_policy` JSON per Bot/entity/environment stores the UPFS
  choice only; no NAS rows are created. Initializers arbitrate through the unique key; write failures
  propagate. No creation status or failure reason is stored in this policy.
  Legacy Bots without a policy remain NAS.

See repository-root `docs/specs/2026-09-15-upfs-phase1/spec.md` for the shared
Backend/BaaS contract, deployment prerequisites, limitations and review order.

## Reserved publication summary

`knowledge_online_selection` is maintained exclusively by knowledge publication
completion in the same Session as native completion and binding changes. Generic
config create/set/upsert/batch/update/delete methods reject that key and its
case/accent/width/trailing-space aliases. Persistence also checks the actually
matched key; the atomic put conflict branch preserves protected rows under the
server's collation. Ordinary
configuration keys keep their existing behavior. Operator reads remain available.
The summary is a current result, not a second publication workflow or a cache
which may be freely dropped. See core/bot_knowledge/README.md for scope and
initialization semantics. No common-config schema change is required.
