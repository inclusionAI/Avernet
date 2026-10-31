# 02 — Availability Architecture

- Parent: [BCN Cloud Orchestration for Personal Agents](README.md)
- Status: Draft for team review

## Problem

Cloud orchestration puts BCN in the critical path of a personal agent's task.
The requirement, stated as invariants:

1. **A server-node failure never fails or loses a task.** No task is
   "prematurely failed" because the node driving it died; acknowledged
   requests and performed side effects are never lost or silently repeated.
2. **Service availability stays high.** The API surface keeps answering
   through single-node failures, deploys, and scale events.

Today BCS deploys as one logical instance. Its recovery machinery
(write-before-dispatch, startup facts, immutable run snapshots, the
`dispatch/judge/opening` recovery modules) is real and battle-tested — but it
assumes *the same logical node restarts and re-reads its own tables*. Nothing
exists for another node detecting a failure, claiming the work, and fencing
the old executor. There is also no node identity, membership, or
task-to-node assignment anywhere in the workspace.

## Design: four planes

![The four availability planes: API (any node serves any request, write-before-ack), executor (hash placement as optimization, fenced DB leases as correctness), connection (node-pinned dial-in sockets plus per-dispatch dial-out streams, with a net-new connection-location registry), storage (one HA MySQL/Redis dependency, BCSFuse out of the execution path).](assets/availability-planes.svg)

### Plane 1 — API (stateless request handling)

Any node serves any request. All durable effects are written to shared
storage before acknowledgement (already the local discipline). The personal
agent's calls carry ids, not connection state (Doc 01), so a load balancer
can spread or fail over requests freely.

- Availability: survives N−1 node failures behind a load balancer.
- RPO: 0 for acknowledged requests, by construction.

### Plane 2 — Executor (who drives a task)

Between caller rounds, BCN is *not* passive: the collaboration runtime
advances workflows as supply agents answer, timeouts fire, and retries
dispatch. Exactly one node must drive a given task at a time.

Two mechanisms, deliberately separated:

- **Placement — `hash(user_id, task_id)`** routes rounds of the same task to
  the same node so the episode stays warm (Doc 01). This is a latency
  optimization. A mis-routed round (ring churn, scale event) is
  harmless-but-slow: it pays a rehydration, nothing more.
- **Ownership — fenced, expiring leases in the shared DB.** A node claims a
  task by writing a lease row (task id, node id, fencing token =
  monotonically increasing generation, expiry). It renews while driving;
  other nodes claim only expired leases. Every durable write made while
  driving carries the fencing token, and storage rejects writes bearing a
  stale token — which is what actually isolates a zombie: a paused/partitioned
  node that wakes and keeps writing is refused at the data, regardless of
  what it believes.

Why not consistent hashing alone: under membership change or partition, two
nodes can transiently both believe they own a slice of the ring. Hashing's
correctness depends on everyone agreeing on membership — the hard part —
while a lease anchors correctness in the storage every node already shares.
The existing `session_activation_count` (an activation generation the
recovery path already validates) is the in-repo seed of exactly this fencing
pattern, generalized.

**Takeover procedure** (new owner, after lease expiry):

1. Claim the lease with a higher fencing token (atomic compare-and-set).
2. Rehydrate the task from durable state — the same path as episode
   resumption; "failover is a cache miss".
3. Reconcile in-flight dispatches via the pull protocol (Doc 03): for each
   dispatch record not in a terminal state, probe the supply agent by
   idempotency key / `GetTask` — never blind-resend.
4. Resume the run from its checkpoint.

![Takeover sequence: the old owner writes the dispatch intent then stalls; its lease expires; the new owner claims the lease with a higher fencing token, rehydrates, probes the supply agent by the recorded idempotency key, gets "unknown", and resends with the same key; the zombie's late original send deduplicates at the agent, and its stale checkpoint write is refused by the fencing token.](assets/takeover-sequence.svg)

Failure detection: lease expiry is the ground truth; node heartbeats (to the
DB or a membership view) only *accelerate* detection. Proposed starting
point, to be tuned: lease TTL 15s, renewal every 5s, takeover RTO target
≤ 30s from node death to resumed execution (**open question #2**).

### Plane 3 — Connections (reaching supply agents)

Supply-agent channels are node-pinned and are the one thing rehydration
cannot rebuild — the remote side holds the other end:

- **Dial-in WebSocket bots** (plugins, bcs-bridge): the socket is the agent's
  entire presence. Recovery is the *agent's* reconnect loop re-dialing (the
  existing reconnect + admission rate-limiting machinery), after which the
  socket may land on any node.
- **Dial-out webhook+SSE providers**: each dispatch is a self-contained HTTP
  exchange; a lost stream is one step's feed, re-attached by sequence number;
  the provider's reachability (its URL) is node-independent.

Net-new: a **connection-location registry** (shared store or gossip) mapping
bot → node currently holding its socket, plus inter-node delivery forwarding,
so the task-owning node can deliver to a bot whose socket lives elsewhere.
Deliveries already queue durably, so an agent briefly disconnected *delays*
its messages rather than losing them.

### Plane 4 — Storage

- MySQL and Redis plugins exist; HA for them is delegated to managed/clustered
  deployments and is a prerequisite, not part of this design.
- **BCSFuse leaves the execution critical path**: discovery/recommendation
  results are consumed at workflow-selection time and cacheable; a BCSFuse
  outage degrades search freshness, never a running task (**open question
  #4** for the caching policy).

## Failure-mode table

| Failure | Detection | User-visible effect | Recovery bound |
| --- | --- | --- | --- |
| Node crash, idle tasks only on it | next round routes elsewhere | none — cold start = normal rehydration | none needed |
| Node crash, task mid-run | lease expiry | round stalls briefly; no loss, no duplicate side effects (Doc 03) | takeover RTO (target ≤ 30s) + reconcile |
| Network partition / paused ("zombie") node | lease expiry on the live side; fencing on the stale side | as above; zombie's late writes are refused by fencing token | same as crash |
| Rolling deploy / scale event | leases handed over or expire; ring moves | some rounds pay a rehydration | seconds per task |
| Supply agent's socket lost | agent reconnect loop | in-flight step waits; queued deliveries delayed | agent reconnect + registry update |
| Shared DB down | immediate | **control-plane outage**: new rounds rejected, in-flight runs pause; nothing acknowledged is lost; agent-side task state (Doc 03) continues accumulating results | DB HA failover (delegated) |
| BCSFuse down | health check | workflow search degrades to cache; execution unaffected | n/a |
| Whole BCN control plane down | caller-side | Edge track (BCN CLI against shared storage) is the degraded path — shared-contract dependency, designed in the Edge track | n/a |

The honest availability statement: the task-survival invariant reduces BCN's
availability dependency to **one HA storage system**. BCN nodes are
replaceable executors over it; BCSFuse is out of the path; the client holds
no connection. "The whole storage layer down" is the residual risk, shared
with every stateful service in the fleet and mitigated by the same managed-HA
practice.

## What exists vs. net-new (this doc's slice)

| Piece | Status |
| --- | --- |
| Write-before-dispatch, run snapshots, restart recovery | exists |
| Durable delivery queue, congestion control, reconnect admission | exists |
| `LeaderElectionPort` seam (standalone impl only) | exists (seam) |
| Fencing seed (`session_activation_count`) | exists (pattern) |
| Node identity, membership, heartbeats | **net-new** |
| Hash-based placement | **net-new** |
| Fenced task leases + takeover + storage-side token checks | **net-new** |
| Connection-location registry + forwarding | **net-new** |
| BCSFuse decoupling/caching | **net-new** |

## Rejected alternatives

- **Consistent hashing as the correctness mechanism**: fails under partition
  and membership disagreement; retained only as placement. 
- **Active/standby as the end state**: meets the invariant but caps
  throughput at one node and makes deploys an availability event; rejected as
  the destination. (It may appear as a rollout milestone — the lease design
  degenerates to it with N=2 — but the architecture reviewed here is the
  N-active one.)
- **Sticky sessions at the load balancer instead of placement hashing**:
  couples placement to L7 infrastructure, invisible to the executor plane and
  unusable for the background (between-rounds) work that also wants affinity.
