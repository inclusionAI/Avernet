# Space knowledge orchestration
Knowledge assets and their members are held by the canonical provider, never mirrored here.
The current authenticated actor must be a live Space member, not necessarily an Admin.
The durable producer activates only on first knowledge creation. Ordinary reads never publish snapshots.
Member changes and immutable outbox snapshots commit together; the sink is disabled until configured.

## Single-table delivery

Only `ac_space_knowledge_sync` is introduced: one row per `(env, space_id, revision)`,
with the full immutable payload, its hash, source scope status and acknowledgement.
Its MySQL payload is MEDIUMTEXT, so large rosters require no queue fragmentation.
The latest row identifies an activated space; the latest acknowledged row is the
delivery cursor. No snapshot cleanup is implemented: retain pending revisions,
the latest row, and the last acknowledged row if a retention policy is later added.

The source-space row lock serializes revision allocation. All member entry points
append snapshots inside the source transaction; no network or queue operation
runs there. A bounded lifecycle scan (every 30 seconds) dispatches only the next
revision per space through the existing task-queue repository interface. A task
contains only a space/revision pointer. Queue unavailability, terminal tasks or
normal queue-history cleanup do not lose the outbox. Queue schema, ORM model,
indexes, worker and retention are unchanged. Re-enqueue uses its existing active
idempotency key; no task-history lookup is performed by this module.

Delivery is at-least-once and strictly acknowledged in revision order. Missing or
corrupt snapshots fail closed and are never replaced with a current roster. A
remove-then-rejoin sequence retains the removal, so old explicit grants can be
revoked by the canonical provider. Source deletion appends a disabled snapshot
without deleting knowledge assets. First creation waits for confirmation; reads
never activate or publish. Later revocations are eventually consistent, not
immediate. First-use HTTP work stays bounded to four delivery attempts.

### Rollout

Apply `core/spaces/sql/2026_10_24_space_knowledge_sync.sql` before enabling the
producer. The earlier progress-only schema was an unpublished experiment; do not
run CREATE IF NOT EXISTS against that incompatible shape and assume it upgrades
it. Preserve its snapshots/progress with an explicit local migration if it exists.
No existing application table is altered. Public HTTP and provider contracts are
unchanged; only the internal sync repository and composition root change.

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
  - TaskQueueRepositoryProtocol
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
