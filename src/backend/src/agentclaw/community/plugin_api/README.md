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

Changing a Plugin Protocol signature breaks every local + prod impl + the contract-test suite (Rule 25). `SkillCenterGateway` is separate from the legacy `SkillCenterClient`: its typed request objects carry no endpoint or credential configuration; Team catalogue and publish-submission operations require a request-level Team ID, while publish-status lookup follows SC's globally unique `skill_code` contract. Its adapters do not own publication retries or domain state. Version/download reads require an explicit `PUBLIC` or `TEAM` consumer trust scope; the scope and Team ID are preflight context and are not invented SC wire arguments. Public Reference reads omit Team only after the consumer verifies public visibility. Adding a new Protocol requires updating BOUNDARY_SIGNIFICANT_MODULES if it joins a new module, and adding paired impls (Rule 20).

`ImmutableObjectStorageCapability` is an optional structural capability beside
`ObjectStoragePlugin`, rather than a breaking expansion of its corp-facing
surface. It provides atomic write-once publication and FOUND/NOT_FOUND/FAILED
reads for immutable consumers. Composition fails closed when the selected
object store lacks that capability. Existing `put_object/get_object` retain
their compatibility behavior for mutable and legacy consumers. The Community
filesystem implementation stages conditional creates under a reserved internal
root on the same filesystem, outside every logical object-key tree; staging
residue is neither addressable nor returned by `list_objects`.

`ObjectCopyCapability` is the second optional capability beside
`ObjectStoragePlugin`, for consumers that duplicate an object within the store
(a CLI tool's bytes copied to a publish stage's prefix, W9). It is an
efficiency capability, not a correctness one: a consumer that finds it absent
reads the source and writes it back, so composition does **not** fail closed.
It exists because the objects it copies can be hundreds of megabytes, and a
copy performed by the store moves none of those bytes through the backend.

The Interface belongs here because Avernet-owned consumers include Space Team
binding today and the governed Publication/Reference flows in the Phase 2
contract. This does **not** make Skill Center a community implementation:
Community fails closed, while OCB owns the sole Prod HTTP Adapter and binds it
to this consumer-owned Interface at the corp composition root. Moving the
Interface into OCB would reverse the dependency direction and require Avernet
core to import `agentclaw.corp`.

The Gateway models the governed SC wire without exposing its envelope or field
names: public catalogue search/detail and tags, Team create/lookup and paged
Skill listing/detail, one-shot publish submission and status diagnostics,
non-paged version listing, and exact download metadata. Catalogue DTOs retain
stable SC facts needed by presentation and lazy Reference flows; presentation
aliases and TeamClaw persistence remain above this seam.
Public search preserves the already-published `belong_to` compatibility filter.
An SC Team lookup miss has the stable `TEAM_NOT_FOUND` category and is distinct
from upstream rejection or malformed data. Publish-status requests contain only
`skill_code`; their response carries the SC-reported current version, and raw
standard/security reports are retained losslessly in addition to normalized
fields so presentation adapters can preserve existing response data.
Exact-download metadata requires a 64-character hexadecimal SC SHA-256 so later
materialization cannot silently skip package integrity verification.
Team catalogue/detail methods return a distinct DTO with a required Team ID;
an adapter must verify and attach that identity rather than infer it from an
unscoped detail response. TEAM-scoped version/download reads preflight that
Team Skill before invoking SC endpoints that identify only the globally unique
skill code. Because SC exposes Team Skill detail through a paged Team list, an
adapter implementing `get_team_skill` must exhaust that listing (or use an
equivalent authoritative lookup) before concluding that an exact Skill is
absent.

`SkillCenterGateway` is an outbound, trusted-service integration executed in the
Backend process. It may call only the configured Skill Center endpoint with
composition-root-provided service credentials; it may not access TeamClaw
persistence, Runtime, Draft/Attempt/Version state, arbitrary hosts, or caller
credentials. DTO validation plus adapter-specific endpoint/auth configuration
enforce that boundary. It has no startup or shutdown phase and owns no durable
resource; each synchronous operation either returns a normalized DTO or raises
a stable `SkillCenterGatewayError`, with no cleanup callback or hidden retry.
This PR intentionally does not predeclare Catalog, Publication, Public
Reference, or Track Latest application modules; each is introduced only with
its real consumer workflow.
