# Space knowledge orchestration
Knowledge assets and their members are held by the canonical provider, never mirrored here.
The current authenticated actor must be a live Space member, not necessarily an Admin.
The durable producer activates only on first knowledge creation. Ordinary reads never publish snapshots.
When capture is explicitly enabled, member changes and immutable outbox snapshots
commit together; the Sink controls delivery separately and is disabled until configured.

## Single-table delivery

Only `ac_space_knowledge_sync` is introduced: one row per `(env, space_id, revision)`,
with the full immutable payload, its hash, source scope status and acknowledgement.
Its MySQL payload is MEDIUMTEXT, so large rosters require no queue fragmentation.
The latest row identifies an activated space; the latest acknowledged row is the
delivery cursor. No snapshot cleanup is implemented: retain pending revisions,
the latest row, and the last acknowledged row if a retention policy is later added.

Server-database source-space row locks serialize source/snapshot decisions. On
SQLite, a scoped no-op source UPDATE takes a real database write reservation
before reading the source/roster, shared by activation and every member writer.
The guard preserves metadata, rejects sources deleted before it, and propagates
busy-timeout/write failures without retry or success masking. All member entry points
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

### Receiver admission and retired identities

Every immutable insert enforces the receiver's 2000-member / 1 MiB UTF-8 JSON
limits before commit. An over-limit activated-space mutation rolls back with
`SPACE_MEMBERSHIP_LIMIT_EXCEEDED`; first activation leaves no outbox. Only counts
and scope/revision are logged, never roster/payload content. Rejected additions
cannot block later removals, rejoins or disabled delivery. No truncation, skipping
or compaction is permitted. Never-activated ordinary membership is unchanged.

SQLite TEAM/PERSONAL creation, including the additive Space Skill repository seam,
reserves IDs above retained same-environment outbox
history while the initial INSERT holds its database write lock, before members
or external effects. This changes neither source schemas nor physical deletion.
Other SQL dialects retain their native allocation. Retired outbox scope identities
must remain retained even after acknowledgement. See the spec's 2026-10-25 repair
section for upgrade preflight, existing-corruption limitations and rollback gates.

### Capture, delivery and rollout

```yaml
# user_config (optional; omitted means false)
space_knowledge_capture:
  enabled: false
```

This block rejects unknown keys and non-boolean values. Selection happens only
in the composition root; writers do not inspect Sink state or environment flags.

| Deployment | Required behavior |
| --- | --- |
| Never enabled, no history | Default no-op effects; legacy Space/member/approval/Skill Space paths work without the sync table, including `create_schema=false`. No outbox SELECT, knowledge write guard, remote calls or scan. |
| Capture enabled, Space not activated | Startup does not inspect the table; actual mutations require usable schema to check activation, and create no snapshots or receiver admission limit until activated. |
| Activated, delivering | Same-transaction immutable snapshots, FIFO and exact hash acknowledgement. |
| Activated, delivery paused | Keep capture enabled; continue removal/role/delete history and retired-ID protection. Resume the Sink to drain in order after restart. |
| Capture disabled with retained same-environment history | Startup and ordinary library writes remain available; history does not enable capture or get rewritten. Knowledge producers still require enabled capture. |

The lifecycle validates only capture/delivery configuration. It opens no DB
session at startup and never inspects table existence, columns, primary keys or
history, whether capture is enabled or disabled. Actual enabled-capture SQL
surfaces schema/connection errors at runtime in the caller transaction; errors
are not treated as absent history or successful synchronization. No explicit
primary-key/type compatibility probe is performed at runtime either. The guard
still rejects disabled producers, including callers without HTTP lifespan.
Configuration validation success is cached per graph. Changing capture/Sink
bindings requires restart; this is not per-request fail-open selection.

**Never-enabled deployments require no knowledge DDL.** To first enable capture,
apply `core/spaces/sql/2026_10_24_space_knowledge_sync.sql`, then roll out
`capture=true` to **all** writers (API instances, workers and library users)
before allowing any first activation. Mixed capture/no-op writers are unsafe;
history presence does not enforce this rollout requirement at startup.

The earlier progress-only schema was an unpublished experiment; `CREATE IF NOT
EXISTS` does not upgrade it. Preserve snapshots/progress with an explicit
state-preserving migration if it exists. No source or shared-queue schema/index
changes are introduced here. For consistent rollback with history, preserve
capture or pause affected writes.
Explicitly disabling capture no longer blocks startup, but loses recording and
SQLite retired-ID protection; it does not reconcile missed revocations. Never
delete history to change deployment availability.
Corporate composition roots must wire the new required transaction policy and
producer guard before enabling real knowledge. Public tests do not prove that
external assembly, MySQL/OceanBase or online migration is deployed.

### Bounded metadata dispatch

Each 30-second scan performs at most **32 covering-index seeks**, one per
candidate Space, jumping over that Space's remaining pending revisions. A scalar
progress check (latest + last acknowledged) ensures a candidate is exactly the
next revision. Thus discovery plus progress uses at most **96 SELECTs** per
full scan, independent of single-Space backlog depth; queue operations retain
their existing bounded per-candidate cost. Empty scans do one seek and no writes.
SQL round trips increase from a single aggregate to up to 32 seeks to avoid
linear enumeration of deep pending history. Instances multiply this cost; the
loop is serial and sleeps 30 seconds after completion, not an overlapping timer.

Dispatch/progress/acknowledgement use scalar metadata, never full rosters. Only
`next_snapshot` loads the exact next payload and verifies its envelope/hash before
Sink invocation. Corrupt-but-present heads may have queue pointers but always
retry without delivery or acknowledgement; a missing predecessor is not queued.
Acknowledgement compares the delivered envelope and canonical content hash to
the locked immutable row. Source no-op equality still reads the previous payload.
Local 1/1000/100000-pending probes and SQLite EXPLAIN verify this shape and the
existing covering index, not MySQL/OceanBase latency or plans.

## Context Boundary
```yaml
purpose: "Orchestrate canonical Space knowledge creation and provisioning lookup without asset persistence."
provides:
  - SpaceKnowledgeService
  - SpaceKnowledgeServiceProtocol
  - SpaceKnowledgeCaptureLifecycle
  - SpaceKnowledgeCaptureGuard
consumes:
  - DatabasePlugin
  - SpacePersistenceEffects
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
  - agentclaw.community.plugin_api.database
  - agentclaw.community.plugin_api.space_membership
  - agentclaw.community.plugin_api.space_knowledge
```

### Change impact
Provider/sync contract changes affect deployment adapters and authenticated HTTP consumers.
There is no Bot grant, enablement, or MCP installation side effect.
