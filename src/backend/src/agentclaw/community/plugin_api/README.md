# `agentclaw.community.plugin_api`

Plugin Protocol declarations (the kernel's outbound interface to swappable capabilities).

## Context Boundary

```yaml
purpose: "Plugin Protocol declarations (the kernel's outbound interface to swappable capabilities)."
provides:
  - "27 plugin Protocol classes (AuthPlugin, CachePlugin, DatabasePlugin, …)"
  - "Plugin marker"
  - "@plugin_impl decorator + Mode/Flavor enums"
  - "IMPL_REGISTRY"
consumes:
  []
internal_dependencies:
  - agentclaw.community.core.base
  - agentclaw.community.core.service_bot.services.baas_service  # BAAS dataclass (BotWsConnectionInfoResponse) typed in BaasServiceProtocol
  - agentclaw.community.core.service_bot.types                  # PublishStage enum, default value in BaasServiceProtocol signatures
  - agentclaw.community.core.workspace
  - agentclaw.community.kernel.device_dto                       # OutBoundOperationRule — typed in OutboundRuleProvider + BaasServiceProtocol (B6)
  - agentclaw.community.utils.avernet_tenant_guard              # BotModel registers with the model-agnostic tenant guard
  - agentclaw.community.utils.env_utils
```

### Change impact

Changing a Protocol signature breaks every local + prod impl + the contract-test suite (Rule 25). Adding a new Protocol requires updating BOUNDARY_SIGNIFICANT_MODULES if it joins a new module, and adding paired impls (Rule 20).
