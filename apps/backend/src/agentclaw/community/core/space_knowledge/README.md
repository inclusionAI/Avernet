# Space knowledge orchestration
Knowledge assets and their members are held by the canonical provider, never mirrored here.
The current authenticated actor must be a live Space member, not necessarily an Admin.
The durable producer activates only on first knowledge creation. Ordinary reads never publish snapshots.
Member changes and immutable delivery tasks commit together; the sink is disabled until configured.

## Context Boundary
```yaml
purpose: "Orchestrate canonical Space knowledge creation and provisioning lookup without asset persistence."
provides:
  - SpaceKnowledgeService
  - SpaceKnowledgeServiceProtocol
consumes:
  - SpaceAccessServiceProtocol
  - SpaceKnowledgeSync
  - SpaceKnowledgeProvider
  - SpaceMembershipSink
  - SpaceKnowledgeSyncRepositoryProtocol
internal_dependencies:
  - agentclaw.community.core.space_knowledge
  - agentclaw.community.core.spaces
  - agentclaw.community.core.base
  - agentclaw.community.core.repository
  - agentclaw.community.core.task_queue
  - agentclaw.community.kernel.lifecycle
  - agentclaw.community.log
  - agentclaw.community.plugin_api.space_membership
  - agentclaw.community.plugin_api.space_knowledge
```

### Change impact
Provider/sync contract changes affect deployment adapters and authenticated HTTP consumers.
There is no Bot grant, enablement, or MCP installation side effect.
