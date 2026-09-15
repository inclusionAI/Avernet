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
  - agentclaw.community.log
```

### Change impact

Bot storage policy is currently consumed by personal Bot creation and restart.
Creation initializes UPFS only; NAS does not write a policy. Restart/recovery only
reads saved choices. Service Drafts are excluded by caller-side personal guards;
the reusable initializer and BaaS capabilities remain available for later phases.
Persistence uses the existing repository/DatabasePlugin boundary; existing global
config is unchanged. Invalid policy or unavailable policy reads fail closed,
except for the not-yet-deployed table, which is treated as no policy.

## Implementation and contract

- `bot_config_protocol.py`: generic JSON access and storage-policy Service APIs.
- `bot_config_service.py`: separate generic-config and storage-policy services;
  rollout evaluation is an internal helper, not a separate public API.
- Persistence stays in `core/repository`; wiring stays in `di/modules`.
- One `storage_policy` JSON per Bot/entity/environment stores the UPFS
  choice only; no NAS rows are created. Initializers arbitrate through the unique key; write failures
  propagate. No creation status or failure reason is stored in this policy.
  Legacy Bots without a policy remain NAS.

See repository-root `docs/specs/2026-09-15-upfs-phase1/spec.md` for the shared
Backend/BaaS contract, deployment prerequisites, limitations and review order.
