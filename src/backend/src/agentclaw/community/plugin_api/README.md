# `agentclaw.community.plugin_api`

Plugin Protocol declarations (the kernel's outbound interface to swappable capabilities).

## Context Boundary

```yaml
purpose: "Plugin Protocol declarations (the kernel's outbound interface to swappable capabilities)."
provides:
  - "Plugin Protocol classes, including the independent SkillCenterGateway"
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

Changing a Protocol signature breaks every local + prod impl + the contract-test suite (Rule 25). `SkillCenterGateway` is separate from the legacy `SkillCenterClient`: its typed request objects carry no endpoint or credential configuration, every Team catalogue/publication operation requires a request-level Team ID, and its adapters do not own publication retries or domain state. Version/download reads require an explicit `PUBLIC` or `TEAM` scope; Public Reference reads omit Team only after the consumer verifies public visibility, because Skill Center skill codes are globally unique. Adding a new Protocol requires updating BOUNDARY_SIGNIFICANT_MODULES if it joins a new module, and adding paired impls (Rule 20).

The Gateway models the governed SC wire without exposing its envelope or field
names: public catalogue search/detail and tags, Team create/lookup and paged
Skill listing/detail, one-shot publish submission and status diagnostics,
non-paged version listing, and exact download metadata. Catalogue DTOs retain
stable SC facts needed by presentation and lazy Reference flows; presentation
aliases and TeamClaw persistence remain above this seam.
Exact-download metadata requires a 64-character hexadecimal SC SHA-256 so later
materialization cannot silently skip package integrity verification.
Team catalogue/detail methods return a distinct DTO with a required Team ID;
an adapter must verify and attach that identity rather than infer it from an
unscoped detail response. TEAM-scoped version/download reads preflight that
Team Skill before invoking SC endpoints that identify only the globally unique
skill code.

`SkillCenterGateway` is an outbound, trusted-service integration executed in the
Backend process. It may call only the configured Skill Center endpoint with
composition-root-provided service credentials; it may not access TeamClaw
persistence, Runtime, Draft/Attempt/Version state, arbitrary hosts, or caller
credentials. DTO validation plus adapter-specific endpoint/auth configuration
enforce that boundary. It has no startup or shutdown phase and owns no durable
resource; each synchronous operation either returns a normalized DTO or raises
a stable `SkillCenterGatewayError`, with no cleanup callback or hidden retry.
