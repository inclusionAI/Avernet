# Cluster Executor Plane — Design

[简体中文](design.zh-CN.md)

- Date: 2026-11-04
- Status: **Design agreed in discussion; input to the SDD spec** (`spec.md` /
  `plan.md` / `tasks.md` will be added to this folder through the repo's
  specify → plan → tasks flow)
- Scope: **Item 1 of BCN Cloud Orchestration — the cluster core**: node
  identity and membership, placement, fenced task leases, request routing to
  the owner, failover, graceful drain and deploy, episode lifecycle.
- Parent design: *BCN Cloud Orchestration for Personal Agents*, Doc 02
  "Availability architecture" (Plane 2 — Executor). That doc set was proposed
  in [inclusionAI/Avernet#3387](https://github.com/inclusionAI/Avernet/pull/3387)
  (closed; its branch was deleted). This document refines Plane 2 and records
  every deviation in [§17](#17-deviations-from-doc-02).
- Audience: BCS engineers implementing items 1–5; reviewers new to
  distributed coordination. Each mechanism lists the alternatives considered
  and why they were rejected.

## Contents

1. [Summary](#1-summary)
2. [Problem and current state](#2-problem-and-current-state)
3. [Goals, invariants, non-goals](#3-goals-invariants-non-goals)
4. [Vocabulary](#4-vocabulary)
5. [Overview](#5-overview)
6. [Membership and failure detection](#6-membership-and-failure-detection)
7. [Placement: rendezvous hashing](#7-placement-rendezvous-hashing)
8. [Ownership: fenced task leases](#8-ownership-fenced-task-leases)
9. [Routing a round to its owner](#9-routing-a-round-to-its-owner-d6)
10. [Failover](#10-failover)
11. [Graceful drain and deploy](#11-graceful-drain-and-deploy)
12. [Episodes: warm-state lifecycle](#12-episodes-warm-state-lifecycle)
13. [Storage](#13-storage)
14. [Capacity and cost model](#14-capacity-and-cost-model)
15. [Parameters](#15-parameters)
16. [Schema and ports](#16-schema-and-ports)
17. [Deviations from Doc 02](#17-deviations-from-doc-02)
18. [Notes for sibling items](#18-notes-for-sibling-items)
19. [Open decisions](#19-open-decisions)
20. [Delivery plan](#20-delivery-plan)
21. [Rejected alternatives](#21-rejected-alternatives)
22. [References](#22-references)

---

## 1. Summary

BCN must run as N active nodes over one shared HA database, so that a node
failure never fails or loses a personal agent's task. Four mechanisms, each
with one job:

| Mechanism | Answers | Correctness role |
| --- | --- | --- |
| **Roster** (DB table + heartbeats) | Which nodes exist and are alive? | None — advisory; drives placement and speeds detection |
| **Placement** (rendezvous hashing) | Which node *should* drive session S? | None — a wrong answer costs one extra hop or one rehydration |
| **Lease + fencing token** (DB row per session) | Which node *does* drive S? | **All of it.** Storage rejects writes carrying a stale token |
| **Routing** (forward to owner, redirect on mismatch) | How does a round reach the owner fast? | None — every failure falls back to a durable write |

The one-sentence version: **hash = fast guess, lease = truth, storage-side
token checks = safety.** Nothing about correctness depends on nodes agreeing
on membership, on clocks, or on the load balancer.

---

## 2. Problem and current state

Today BCS deploys as one logical instance. Its recovery machinery
(write-before-dispatch, immutable run snapshots, restart recovery) assumes
*the same node restarts and re-reads its own tables*. Nothing lets another
node detect a failure, claim the work, and fence the old executor.

Code reading found three components that are single-instance by design:

| Component | Where | Single-instance assumption |
| --- | --- | --- |
| Progression recovery scanner | `crates/bootstrap/bcs/src/state_machine_progression_scanner.rs` | Scans **all** runs every 1 s, gated on `LeaderElectionPort::is_leader()` |
| Managed delivery scheduler | `crates/services/bcs-message-flow/src/delivery_runtime.rs`, `docs/message-delivery-service-api.md` | "Only one BCS scheduler instance per DB"; capacity/ordering under **in-process** locks; bot availability is a **process-local** check; bots offline past a 10 s grace are marked **failed** |
| Leader election | `crates/services/bcs-leader-election` | Only `StandaloneLeaderElection` exists — it always answers "leader" |

**Existing hazard.** `docker/kube-deploy.sh` defaults BCS to **2 replicas**.
Both run the standalone election, so both believe they are leader and both
run the scanner (and, where enabled, the delivery scheduler) against the same
database. Item 1 removes this hazard by scoping all of these to leases; until
it lands, production should run one BCS replica per database or confirm the
existing CAS guards suffice.

In-repo seeds this design generalizes:

- `session_activation_count` — a CAS generation on business state, checked in
  `WHERE` clauses by the collaboration store. Orthogonal to our token (it
  names *which activation*; ours names *which owner*); both stay.
- `bcs_session_speaking_leases` (migration 037) — lease + monotonic `epoch` +
  expiry, "an expired lease never revives". Our tables follow its conventions.
- `bcs-db-api` scripted transactions with `ExecuteChecked
  { expected_affected_rows }` — exactly what a fenced write needs (§8.4).

---

## 3. Goals, invariants, non-goals

**Invariants**

1. **Single writer per session.** At most one node's durable writes for a task
   session are accepted at any time. Sessions are indivisible placement units.
2. **A node failure never fails or loses a task.** Acknowledged rounds are
   durable before the ack (RPO = 0 by construction).
3. **Correctness never depends on** hashing, membership agreement, clocks, the
   load balancer, or a node knowing it lost its lease — only on storage-side
   token checks.
4. **Node-local state is a rebuildable cache.** Write-through only; write-back
   of durable facts is prohibited.

**Goals**

- Takeover RTO ≤ 30 s after a crash; immediate after a planned restart.
- Deploys add no material TTFT for personal-agent rounds.
- Single-node mode (singlebox) runs the same lease code path on SQLite.

**Non-goals (owned by sibling items)**

Connection-location registry and delivery forwarding (item 2); dispatch
intent records, idempotency keys, reconciliation (item 3 — this item only
stubs the takeover hook and provides the fencing contract); the BCN A2A
profile (item 4); the PA-facing A2A surface (item 5); dispatch gating (item
6); BCSFuse caching (item 7).

---

## 4. Vocabulary

| Term | Meaning |
| --- | --- |
| **Node** | One running BCS process |
| `member_name` | **Stable** identity of a node slot (e.g. host name); survives restarts. Used for placement |
| `node_id` | **Per-process-run** identity (`member_name` + ULID minted at start). Used for lease ownership |
| `advertise_addr` | Internal `host:port` peers use to reach this node directly (never through the LB) |
| **Roster** | Table `bcs_node_roster`: one row per live node, with heartbeat |
| **Lease** | Row in `bcs_task_leases`: who owns session S, with a fencing token |
| **Fencing token** | Monotonic per-session generation; bumped on every ownership change |
| **Owner** | The node whose `node_id` is on S's lease row and whose heartbeat is fresh |
| **Preferred node** | The node rendezvous hashing ranks first for S among live, active nodes |
| **Entry node** | The node the load balancer delivered a request to |
| **Socket holder** | The node a dial-in bot's WebSocket happens to be connected to |
| **Claim** | Taking an unowned or dead-owned lease (CAS, token n → n+1) |
| **Release** | Giving up a lease without choosing a successor (`node_id = ''`) |
| **Handoff** | Transferring a live lease directly to a chosen successor (§11.4) |
| **Episode** | The period a node keeps S hydrated in memory; cache lifetime, not data |
| **Safe point** | A moment in a run where ownership can move without losing anything (§11.3) |

---

## 5. Overview

![Overview: the PA reaches any node through the load balancer; the entry node forwards to the lease owner over the peer channel; all nodes share one database holding the roster, task leases and session data; bot sockets terminate on whichever node accepted them.](assets/overview.svg)

Traffic splits into two directions:

- **North-south** (clients → cluster) goes through the load balancer, which
  only needs to spread requests over ready nodes.
- **East-west** (node → node) goes directly to a peer's `advertise_addr`:
  forwarding a round to its owner, handoff messages, and (item 2) relaying
  bot traffic. The roster is the service-discovery directory.

Nodes never need to agree with each other on anything. The shared database is
the only place they meet, and the lease row is the only fact that matters for
correctness.

## 6. Membership and failure detection

### 6.1 Choice: a roster table in the shared database

| Option | Consistency | Detection | New infrastructure | Verdict |
| --- | --- | --- | --- | --- |
| **DB roster + heartbeats** | Linearizable rows; each node polls | ~heartbeat × k | **None** — BCS already requires HA storage | **Chosen** |
| etcd / ZooKeeper sessions | Linearizable, watchable | Session TTL | A quorum cluster to run | Rejected: buys linearizable *membership*, which we don't need — the only linearizable fact that matters (the lease) already lives next to the data |
| Gossip / SWIM | Eventually consistent | Sub-second | Peer UDP/TCP ports | Rejected: solves thousands-of-nodes scale and DB independence; a BCN node without the DB is useless anyway |

Cost: 20 nodes × one heartbeat per 5 s = 4 writes/s.

### 6.2 Rows and states

Each node upserts its row at start and refreshes `last_heartbeat_at` every
heartbeat. States: `active`, `draining`. **Dead is derived, never stored**: a
crashed node cannot write anything, so death is "heartbeat too old".

Identity is split deliberately ([D3](#17-deviations-from-doc-02), [D7](#17-deviations-from-doc-02)):

- `node_id` is per process run. A restarted process has none of the old warm
  state, so it is a different owner; equality on `node_id` then answers "is
  this my lease?" and enables one-statement lease renewal (§8.3).
- `member_name` is stable. Placement hashes on it, so a restarted node is
  preferred for exactly the sessions it was preferred for before.

`advertise_addr` defaults to configuration or an environment variable (e.g.
the pod IP via the Kubernetes downward API), falling back to the existing
`get_local_ip()`. Interface enumeration alone is unreliable in containers.

**Superseding a previous run at the same address.** When a node registers, in
the same transaction as its own upsert it sets `last_heartbeat_at = NULL` on
every other roster row with the same `(env, advertise_addr)`. Two processes
cannot listen on one address, so such a row belongs to a dead run. The row is
kept, so survivors can still run the discovery query (§10.1) with the old
`node_id`; it is simply no longer fresh, so the claim predicate (§8.3) treats
its leases as claimable at once and relative staleness (§6.3) ignores it.
This is how the early takeover in §10.4 is expressed without changing the
claim statement. It requires `advertise_addr` to be unique per live process
(no shared NAT addresses); stale rows are garbage-collected after about an
hour.

### 6.3 One clock, and relative staleness

All time comparisons use the database's `NOW()`, never node clocks, so
cross-node clock skew cannot make a live node look dead. (On OceanBase,
`NOW()` comes from whichever server runs the statement; OceanBase requires
its servers' clocks to stay tightly synchronized, which is negligible against
10–15 s thresholds.)

Staleness is measured **relative to the cluster**, not the wall clock:

```
stale(D) = max(last_heartbeat_at over all roster rows) − last_heartbeat_at(D)
```

Reason: after a cluster-wide storage hiccup, every node missed heartbeats at
the same time; wall-clock staleness would make everyone look dead to everyone
and trigger a takeover storm. Relative staleness sees everyone equally behind.

That alone is not enough after an outage **longer** than the claim threshold:
the first node to heartbeat again advances the maximum while healthy peers
still carry pre-outage timestamps. So the observer must be healthy too
(**recovery grace**, the idea behind Chubby's grace period after its own master
fails over). Each node tracks `healthy_since`, the time of its first
successful heartbeat after its last failed one, or after start. It treats
`stale(D) > threshold` as death, for claiming, only when
`now − healthy_since ≥ claim threshold`. After an outage every node's
`healthy_since` resets, so nobody claims a peer's leases for one claim
threshold; meanwhile every healthy peer heartbeats several times and only
truly dead nodes stay stale. Positive evidence (§10.4) and claims of released
leases need no grace.

### 6.4 Two thresholds on one heartbeat

| Threshold | Default | Effect | Cost of being wrong |
| --- | --- | --- | --- |
| Placement-stale | 10 s | Node drops out of everyone's placement ranking | One extra hop / rehydration (cheap) |
| Claim | 15 s | Node's leases become claimable | A false takeover: safe, but a stall and a rehydration |

---

## 7. Placement: rendezvous hashing

![Rendezvous hashing: each live node scores hash(key ‖ member_name); the highest score wins. When D joins only sessions D now wins move; when B leaves, only B's sessions move, each to its own second-ranked node.](assets/hrw-placement.svg)

### 7.1 Algorithm

```
key            = user_id ‖ 0x00 ‖ session_id
score(member)  = xxh3_64(key ‖ member_name, seed = fixed)
preferred(S)   = argmax over roster rows with state = active and fresh heartbeat
ranked(S)      = all candidates sorted by score (fallback order)
```

Properties: every node computes the same answer in microseconds with no
coordination; when membership changes, only the minimum possible fraction of
sessions (1/N) changes preference, and a departed node's sessions spread
evenly across survivors.

The hash must be **stable across processes and versions**: use xxh3 with a
fixed seed (`twox-hash` is already in `Cargo.lock`); never Rust's
`DefaultHasher`. The hash function and key are treated as frozen, like the
lease protocol: a change mid-rollout causes only extra hops, but is avoided.

### 7.2 Why rendezvous

| Scheme | Moves on N change | Fit |
| --- | --- | --- |
| `hash % N` | ~all keys | ✗ every deploy discards all warm state |
| Ring + virtual nodes | ~1/N | ✓ works; needs ~100–200 vnodes per node to balance |
| **Rendezvous (HRW)** | exactly 1/N; O(N) lookup | ✓✓ no tuning; ranked fallback list for free |
| Jump hash | 1/N | ✗ cannot remove a node from the middle (a crash) |
| Fixed slot map | assigner decides | ✗ needs a coordinator |

A **bounded-load** variant (skip nodes above `c × average` load while walking
the ranked list) is a later, swappable upgrade behind the `PlacementPolicy`
trait.

### 7.3 Placement is not ownership

Placement only decides **who claims** an unowned session. Ownership is the
lease. The two diverge after membership changes (a joining node is preferred
but existing leases do not move) and after takeovers. Routing (§9) therefore
treats the hash as a guess that the target verifies.

---

## 8. Ownership: fenced task leases

### 8.1 Why a lock is not enough

![Zombie scenario: A holds token 7 and pauses; the lease becomes claimable; B claims token 8 and writes; A wakes and its write carrying token 7 is rejected by the database.](assets/zombie-fencing.svg)

A node that pauses (GC, VM migration, kernel stall) or is partitioned can
wake believing it still owns S. Any check it makes ("am I still owner?") can
be followed by another pause before its write lands. **Only the storage the
write lands in can reject it.** Each ownership change bumps a fencing token;
every owner write carries its token; storage rejects a stale one.

### 8.2 Lease liveness: owner heartbeat ([D5](#17-deviations-from-doc-02))

A lease is **claimable** when its owner's roster heartbeat is stale beyond
the claim threshold, **or** the lease was released (`node_id = ''`), **or**
there is evidence its owner's process run is dead (§10.4). There is no
per-row renewal: the node heartbeat renews all of a node's leases at once.

Reason: per-row renewal costs one row write per lease per tick. At 600 k
in-flight sessions that is ~120 k row writes/s just to say "still alive";
node-level liveness costs one write per node per tick (the same model as
Chubby sessions and etcd leases with many keys attached).

`expires_at` keeps its pinned name with changed meaning: **NULL while held**;
set on release or handoff.

### 8.3 Lease operations

All comparisons use the DB clock. Rows are created **released** when the
task session is created, so the claim path is always a single `UPDATE`.

```sql
-- CLAIM (CAS first; 1 statement in the common cold case)
-- Dialect-neutral sketch: SET columns are unqualified (SQLite rejects
-- qualified SET targets); the SQL store renders the statement per flavor.
UPDATE bcs_task_leases
   SET fencing_token = :n + 1, node_id = :me, expires_at = NULL
 WHERE env = :env AND session_id = :s AND fencing_token = :n
   AND ( node_id = ''
         OR NOT EXISTS (SELECT 1 FROM bcs_node_roster r
                         WHERE r.node_id = bcs_task_leases.node_id
                           AND <r is fresh>) )
-- affected = 1 → owner with token n+1;  affected = 0 → read the row to learn who owns it

-- RELEASE (no successor; next claimer bumps the token)
UPDATE bcs_task_leases SET node_id = '', expires_at = NOW()
 WHERE env = :env AND session_id = :s AND node_id = :me AND fencing_token = :mine

-- TRANSFER (handoff to a chosen successor; only the live holder can do this)
UPDATE bcs_task_leases SET node_id = :successor, fencing_token = :mine + 1
 WHERE env = :env AND session_id = :s AND node_id = :me AND fencing_token = :mine
```

Rules:

- **The token never goes backwards.** A lease row is never deleted while its
  session can still be written; release clears `node_id`, it does not delete.
  Otherwise a re-created row restarts at 1 and an old zombie token matches
  again (ABA).
- **Expiry decides who may claim; the token decides who may write.** An owner
  whose heartbeat is late but whose lease has not been claimed can still
  write safely — nobody else is writing.
- **Exactly one claimant wins.** The CAS takes the row lock; a racing claimant
  re-evaluates, finds the token already bumped, and touches 0 rows.
- **A lease with pending work is never released, only transferred.** Release
  (`node_id = ''`) is for sessions with no pending work (cold, idle). A
  released lease is invisible to the dead-node query (§10.1), so releasing a
  session that still needs a driver would leave it waiting for the safety-net
  sweep. Whenever a node must give up such a session without a full handoff
  (drain deadline, stuck-session watchdog), it **transfers** the lease to the
  session's HRW successor and sends a best-effort `COMMIT` notice; if the
  notice is lost, the successor finds the session in its owned set at its next
  heartbeat read-back (≤ 5 s).

**Self-fencing** (a liveness courtesy, not the safety mechanism): a node that
cannot complete a heartbeat by `send_time + claim_threshold − margin` on its
local monotonic clock stops driving all sessions and fails readiness. Measure
from when the heartbeat was *sent*. Fencing still covers a node whose
self-fence is itself paused.

### 8.4 Fenced writes ([D1](#17-deviations-from-doc-02))

![A fenced write is one transaction: first a checked update of the session's lease row (token must match; write_seq increments), then the durable writes. A stale token makes step 1 touch zero rows and the whole transaction rolls back.](assets/fenced-write.svg)

```
DbTransaction [
  ExecuteChecked(expected = 1):
     UPDATE bcs_task_leases SET write_seq = write_seq + 1
      WHERE env = ? AND session_id = ? AND fencing_token = :mine
  Execute: <durable writes for this session>
]   -- stale token → step 1 touches 0 rows → whole transaction rolls back → LeaseLost
```

Why this shape:

- Check and writes **commit atomically**; the lease-row lock **serializes**
  the write against a concurrent claim or transfer.
- `write_seq = write_seq + 1` always changes the row, so the affected-row
  count is 1. A no-op `SET x = x` reports 0 without `CLIENT_FOUND_ROWS` — a
  classic trap. `write_seq` also lets a handoff successor detect what changed
  since it pre-loaded (§11.4).
- It maps onto the existing `ExecuteChecked` step; no new db-api capability.

Rules:

1. The fence runs **in the same transaction** as the writes.
2. The lease table and the protected tables live **in the same database**.
3. On `LeaseLost`, **stop driving the session and evict it. Never retry.**
4. One transaction fences **one session** (two would need both fences in a
   fixed order; none are expected; the audit checks).
5. A commit whose outcome is unknown (error or timeout during a storage leader
   switch) is resolved by **reading back** (`write_seq`, the row, or the lease
   owner) — never by blindly repeating a non-idempotent write.

**What is fenced:** every durable write an owner makes while driving — run
status, node-run progression, checkpoints and history appended by the run,
dispatch and delivery records (item 3 uses the same fence), durable timers,
and persisting an inbound round the owner is serving.

**What is not fenced:** a non-owner node durably recording an inbound round
as *input* (the fallback path, §9.4); reads; the lease operations themselves.

**The limit of fencing:** it protects *storage*; it cannot recall an HTTP
request a zombie already sent. That case is covered by item 3's write-ahead
dispatch intent and key reuse, and agent-side dedup.

### 8.5 Lease alternatives considered

| Option | Fencing token? | Checked where the data lives? | Verdict |
| --- | --- | --- | --- |
| **DB conditional-update lease** | ✓ | ✓ same transaction | **Chosen** |
| etcd lease + revision | ✓ | ✗ token in etcd, data in the DB — the DB still needs a lease row | Rejected |
| ZooKeeper ephemeral node + `czxid` | ✓ | ✗ same | Rejected |
| Redis lock / Redlock | ✗ | ✗ | Rejected (Kleppmann's critique: safety depends on bounded pauses and clocks) |
| MySQL `GET_LOCK()` | ✗ | partial; tied to a pooled connection | Rejected |

Rule to remember: **put the lock in the same store as the data it protects.**

---

## 9. Routing a round to its owner ([D6](#17-deviations-from-doc-02))

Personal-agent rounds are latency-sensitive (TTFT). Writing a round to the
DB and waiting for the owner's next poll (≤ 1 s, ~500 ms average) is not
acceptable. Instead, the entry node computes a guess in memory and forwards;
the target verifies against the truth it already holds.

![Routing: the entry node uses its hint cache or HRW to pick a target; the target serves if it owns S, claims if S is unowned, or replies NOT_OWNER(X); the entry node caches the hint and makes at most one more hop; on failure it writes the round durably.](assets/request-routing.svg)

### 9.1 Algorithm

```
ENTRY NODE, on a round for S:
    target = hint_cache.get(S) or HRW(S, live roster)          # µs, no DB
    if target == me: handle(S) else: forward(target, round)
    on NOT_OWNER(X): hint_cache.put(S, X); forward(X, round)    # hop limit = 2
    on forward failure (refused / timeout): durable fallback (§9.4)

TARGET NODE, handle(S):
    if I hold S's lease (in-memory set)        → serve: persist round (fenced), drive, stream back
    else try CLAIM (CAS)                       → affected 1: serve
                                               → affected 0: read lease row, reply NOT_OWNER(owner)
```

- Each node knows **its own** leases in memory; the common path needs no DB
  read beyond persisting the round itself.
- **CAS first** at the target: cold sessions (the common non-owner case)
  cost 1 statement; a mismatch costs 2 (failed CAS + read). MySQL and
  OceanBase have no `UPDATE … RETURNING`, so one statement cannot both claim
  and report the current owner.
- The **hint cache** learns from `NOT_OWNER` replies; after a scale-up the
  first mis-routed round pays one extra hop, later rounds go direct.
- `NOT_OWNER` replies from a draining node also say "I'm draining", so the
  entry node updates its roster view at once.
- **Streaming:** the entry node proxies the owner's SSE/gRPC stream back to
  the PA. The PA only ever talks to the LB address.

### 9.2 Walkthrough

| Case | What happens | DB statements at target | Extra hops |
| --- | --- | --- | --- |
| Stable cluster | Entry → HRW → owner serves | 0 (plus persisting the round) | 0 |
| Just after scale-up (owner ≠ preferred) | Preferred replies `NOT_OWNER(A)`; entry caches and forwards to A | 2 | 1, once |
| Cold session | Preferred claims and serves | 1 | 0 |
| Owner crashed, not yet claimable | Forward fails → durable fallback; picked up at takeover | — | — |
| Forward timed out but owner did process | Entry also falls back → two copies of one round | — | dedup required (§9.3) |

### 9.3 Dedup is required, not optional

Forwarding creates a second path for the same round (a timed-out forward
plus the fallback). Both write the same key
`(caller principal, session_id, A2A messageId)`; the unique key lets one
win. This depends on the PA reusing `messageId` on retry ([cross-item notes](cross-item-notes.md)).

### 9.4 Durable fallback

If the owner is unreachable, the entry node records the round durably as
unprocessed input (unfenced — it is input to the session, not owner
progress) and acks. The round is picked up by the owner's pending-input
query or by the takeover that follows. Time to first token then equals
takeover time; nothing can do better, because no other node may drive S
while its lease is live.

### 9.5 Cost and the LB

- Latency added to TTFT in a stable cluster: ~2–7 ms (one in-datacenter hop
  plus the owner's durable write), versus ~500 ms average with polling.
- With a random LB, ~(N−1)/N of rounds are forwarded; the entry node's share
  of work is light (parse, hash, proxy).
- **No LB dependency.** Requirements: unique per-run `node_id`, a shared DB,
  direct peer reachability at `advertise_addr`, and the same hash function on
  every node. An LB that can hash on a `session_id` header reduces
  forwarding but is not required; our HRW still decides.

### 9.6 Why not a message queue on the request path

A RabbitMQ consistent-hash exchange (or similar) was considered and
rejected: its ring is a third membership view that disagrees with the lease
(so verify-and-redirect is still needed), messages for one session split
across queues when the ring changes, a dead node's queue needs a second
lease-like failover mechanism, the token stream back to the entry node still
needs node-to-node addressing, and the broker becomes a second durable store
and a second availability dependency. See [§21](#21-rejected-alternatives).

---

## 10. Failover

A crash gives no `COMMIT` message. Survivors must **discover** which of the
dead node's sessions need a new owner, and split them without coordinating.

![Failover: node D stops heart-beating; at the next roster poll after the claim threshold, each survivor runs one indexed query for D's sessions with pending work and claims those HRW assigns to it; idle sessions are claimed on demand by their next round.](assets/failover-discovery.svg)

### 10.1 Trigger 1 — roster detects death, then one targeted query

```
roster poll (already happening every heartbeat):  D is stale beyond the claim threshold
  → each survivor, once:  SELECT session_id, fencing_token FROM bcs_task_leases
                          WHERE env = ? AND node_id = :D AND has_pending_work   -- indexed, paginated
  → for each row: if HRW(S, live nodes minus D) == me → claim now
                  else → claim after the grace period (in case the preferred node is also down)
  → claimed: rehydrate → reconciliation hook (item 3) → resume
```

Every survivor computes the same ranking, so each claims only its share and
takeover load spreads evenly; the CAS settles any race.

**Pending work** limits the query to sessions that need a driver now: a
non-terminal run, queued deliveries, a durable timer, or unprocessed inbound
input (including rounds that took the durable fallback during the outage).
Idle sessions are left alone.

### 10.2 Trigger 2 — claim on demand

An idle session is discovered by its next round: the entry node's forward
fails or the roster says the owner is dead; the preferred survivor claims
(if claimable) and serves — a normal cold start. If not yet claimable, the
round takes the durable fallback and Trigger 1 picks it up.

### 10.3 Trigger 3 — safety-net sweep

Every 30–60 s, each node runs one indexed, paginated query for released or
dead-owned leases with pending work. It catches failed claims and nodes that
were restarting when another died. Because pending work is transferred, never
released (§8.3), a planned shutdown does not depend on this sweep.

### 10.4 Taking over earlier on evidence

Fencing makes early takeover **safe**: if the old owner turns out to be alive,
its writes fail and it evicts the session. So speed is a pure performance
trade-off, and evidence can replace waiting:

| Evidence | Takeover | Status |
| --- | --- | --- |
| **Same `advertise_addr`, new `node_id`** — the process restarted in place; two processes cannot listen on one address | Immediately when the new run's roster row is seen | **Included**: the new run nulls the old row's heartbeat at registration (§6.2); also removes singlebox's post-crash delay |
| Release on panic / shutdown hook | Immediate | **Included**, best effort (no help for OOM kill or `kill -9`) |
| Shorter thresholds | e.g. 1 s / 3 s → ~5 s RTO | Tunable; relative staleness plus the observer recovery grace (§6.3) make it safe against storage hiccups and longer outages |
| Infrastructure signals (pod deletion events) | Fast | Later, as an optional plugin behind a port with a Noop default |

A node that is alive but silently unreachable gives no evidence; for it,
wait the claim threshold.

### 10.5 Recovery time

| Death evidence | Claimable at | RTO (approx.) |
| --- | --- | --- |
| Restart in place | New run's roster row seen (≤ 5 s after restart) | restart + ≤ 5 s + rehydrate |
| Heartbeat stops | 15 s after last heartbeat, seen at next roster poll | ~15–20 s + rehydrate + reconcile (**≤ 30 s target**) |

### 10.6 Takeover procedure

1. Claim with a higher token (CAS).
2. Rehydrate from durable state — the same path as resuming a cold session
   ("failover is a cache miss").
3. Call the **reconciliation hook** (stub in item 1; item 3 implements: for each
   unfinished dispatch, probe by key or `GetTask`, never a blind resend).
4. Resume from the checkpoint.

### 10.7 Recovery components become lease-scoped ([D2](#17-deviations-from-doc-02))

The progression recovery scanner and the managed delivery scheduler stop
being leader-gated. Each node recovers and dispatches **only for sessions it
owns**, plus the discovery triggers above. In standalone mode "me" owns
everything, so behavior equals today's.

---

## 11. Graceful drain and deploy

Deploy model assumed here: a group G of nodes is restarted in place (stop the
process, start it again on the same machine and address). Goal: rounds for
sessions owned by nodes in G keep a normal TTFT.

**Principle: move ownership before the process stops, and keep serving until
the move.** Draining never means refusing: a draining node keeps serving
every session it still owns.

### 11.1 Drain sequence for node A ∈ G

![Drain timeline: stop signal; roster draining and readiness red; sessions released or handed off cheapest first; non-resumable steps finish then hand off; bot sockets emptied last; roster row deleted; restart with same address and member_name.](assets/drain-timeline.svg)

```
STOP SIGNAL
 1. roster: draining; readiness: fail; stop claiming. Keep serving owned sessions.
 2. Sessions, cheapest first (a session that just received a round jumps the queue):
      cold                                 → release
      warm, or waiting on
        an A2A task with recorded task id  → hand off now
        a bot socket (on another node or A) → hand off now
        a non-resumable stream             → finish that step, then hand off
      about to dispatch a new step         → hand off first; the step starts on the successor
 3. Bot sockets on A: no new sends; "please reconnect" to idle bots; wait for busy
    bots' in-flight runs; close. Their deliveries stay queued with each session's owner.
 4. Keep relaying open PA streams; keep answering NOT_OWNER redirects (peer port stays open).
 5. Drain deadline: TRANSFER the rest to their HRW successors + COMMIT notice (no PREPARE);
    successor rehydrates (read-only steps: cancel and redo; side-effecting: reconcile).
 6. Delete roster row, exit.
RESTART
 7. Same address, new node_id → leftovers claimable at once;
    same member_name → same placement; cold sessions drift back.
```

### 11.2 Choosing successors outside the group

Successors must be outside G, or a session moves twice. A node that is
itself draining **refuses `PREPARE`**, and the drainer tries the next-ranked
node. No fixed settle wait is used. If the deploy platform can run a hook
before stopping a group, it should mark the whole group draining at once
(e.g. `bcs-admin cluster drain --members …`), which removes double moves
entirely.

### 11.3 Safe points

A run can move mid-run because its position is entirely in durable state
after each write. A **safe point** is: no fenced transaction open, no
dispatch sequence half done, and no non-resumable stream in flight.

| What the run is doing | Hand off now? | Why |
| --- | --- | --- |
| Between steps | ✓ | Successor starts the next step |
| Waiting on an A2A task, **task id recorded** | ✓ | Successor uses `GetTask` (core) or `SubscribeToTask` (optional, live tokens) |
| Waiting on a dial-in bot (socket anywhere) | ✓ | The socket holder relays events to the current owner (§11.6) |
| Mid-dispatch (`SENDING` written, `DISPATCHED` not yet) | Finish the sequence first (ms) | Pausing here is correct but forces a probe |
| Waiting on a **non-resumable** stream (Provider 2.0 SSE; Message-only agent) | **Wait for the step's terminal event** | The output exists only on that stream; Provider 2.0 has no resume and no status query |
| BCN-internal work (judge, LLM call) | Finish or let the successor redo | Its result only counts once committed |
| Waiting on a timer | ✓ if the deadline is durable | **Audit required** — an in-memory timer is lost |

While waiting on a non-resumable step, A remains owner and does everything
that step needs: persists its events and result, handles its mid-step input
(e.g. sends `interaction.resolve`), handles an abort. When the step
finishes, **A does not start the next step**; it hands off.

### 11.4 Handoff protocol

![Handoff: A sends PREPARE; B loads S and replies READY; A pauses writes for S; A transfers the lease to B with token n+1; A sends COMMIT; B checks write_seq and drives S; A forwards held rounds and redirects later ones.](assets/handoff-sequence.svg)

```
1. A picks B = HRW(S, roster minus draining nodes)
2. A → B  PREPARE(S)        B loads S (reads only); replies READY — or refuses if draining/overloaded
3. A pauses S               finishes the write in progress, starts no new ones; rounds/events for S wait in memory (ms)
4. A, in the DB:            TRANSFER: node_id = B, token n → n+1   (one statement)
5. A → B  COMMIT(S, n+1)    B drives S; if write_seq moved since PREPARE, B re-reads only the change
6. A forwards held items to B; later arrivals get NOT_OWNER(B)
```

The gap for S is steps 3–6: one DB write plus one peer round trip. Failure
cases: B dies after step 4 → ordinary takeover; A dies between 4 and 5 → B
finds S in its owned set at its next read-back; A stalls after 4 and writes
→ fenced out. A plain `UPDATE` without `PREPARE`/`COMMIT` would be correct
but leaves B cold and unaware for up to a heartbeat.

The pause is per session and exists only to make the handoff clean: without
it, A's writes after the transfer fail the fence (safe but noisy), and writes
landing after `PREPARE` would need re-reading.

### 11.5 New rounds during the drain

| S's state on A | What happens |
| --- | --- |
| Already handed off | Rounds reach A only via stale guesses; A replies `NOT_OWNER(B)` |
| Not yet handed off, nothing blocking | Hand off now, then forward the round |
| Waiting on a non-resumable step | Lease cannot move yet; A records the round durably and it travels with the handoff. If the round *is* input for that step, A processes it |

Whether a round arriving during an active run is input to that run, queued,
or rejected as busy is a product rule for item 5 ([cross-item notes](cross-item-notes.md)). Today's
state-machine run creation allows one active run per session
(`create_run_if_session_idle`).

### 11.6 Bot sockets: two jobs on one node

A node holding a dial-in bot's socket is **transport** for runs of sessions
owned by any node. That job is independent of session ownership.

![Bot socket roles: before handoff the socket holder delivers events in-process to the owner; after handoff the same socket holder relays each event to the new owner; the bot sees nothing.](assets/bot-socket-roles.svg)

- **Socket on another node C:** handing S from A to B only changes where C
  relays the next event. C must look up S's current owner **per event**
  (hint cache + `NOT_OWNER` redirect), never remember the dispatcher.
- **Socket on A itself:** S hands off immediately (A becomes the relay for
  S). What waits is **closing the socket**, which interrupts every run on it,
  including runs of sessions owned elsewhere. A sends no new work on its
  sockets, but still relays events, allows `chat.abort` for in-flight runs,
  and answers the bot's own requests. When every run on a socket has ended
  (or at the deadline), A asks the bot to reconnect or closes; the bot
  redials through the LB.
- **New work for a bot whose socket is draining is never queued at A.** It
  stays a durable delivery row owned by the session's owner, which dispatches
  it once the bot is reachable via another node. *This waiting behavior does
  not exist today* (offline bots fail after a grace period; state-machine
  runs bypass the managed queue) — see [cross-item notes](cross-item-notes.md).

### 11.7 Deadline, platform requirements, rollout

**Deadline.** `drain_timeout` (default 25 s) must fit inside the platform's
stop timeout. At the deadline, every remaining session with pending work is
**transferred** (not released) to its HRW successor with a `COMMIT` notice
(§8.3): read-only steps are cancelled and redone there; side-effecting steps
are reconciled there. A Kubernetes example:
`preStop` sleep 5 s (covers the race between SIGTERM and LB endpoint
removal) + drain 25 s + margin → `terminationGracePeriodSeconds: 40`.

**Probes.** Liveness (`/livez`: is the process wedged?) stays green while
draining. Readiness (`/readyz`: send me work?) is red while draining, while
self-fenced, and before startup completes. They must be separate endpoints;
today's template uses one path for both.

**Startup readiness gates.** DB reachable; schema version understood (check,
never migrate MySQL/OceanBase from a pod); roster row written and first
heartbeat committed; background loops running.

**Rollout pacing.** `maxUnavailable: 0`, `maxSurge: 1`, `minReadySeconds`
≈ 30 s so takeover/rehydration waves don't stack, a PodDisruptionBudget,
and a cap on group size (≤ 25 % of the cluster suggested). Bot reconnect
storms are paced by the existing `[bot_ws_admission]` token bucket.

**Mixed versions.** During a rollout old and new code take over each other's
sessions:

- Schema: expand → migrate (separate step before rollout) → contract in a
  later release. Never both in one release.
- Durable formats (checkpoints, run state, dispatch records): readable in
  both directions. Ship the reader first; turn on the new writer by flag
  after the rollout. Rehydration **fails closed**: an unknown format version
  releases the lease (with backoff and a metric) instead of guessing.
- The lease protocol (claim CAS, fence step, release, transfer) and the
  placement hash are a wire protocol between versions: frozen.
- Timing parameters: the self-fence deadline must be shorter than the
  smallest claim threshold of any config live at the same time; change
  timings in two steps (loosen, roll out, tighten).

**Signals of a clean deploy:** takeovers nearly all by release/handoff (not
expiry); `LeaseLost` ≈ 0; drain time well under the deadline; brief
rehydration bumps; no 5xx at the LB.

---

## 12. Episodes: warm-state lifecycle

- **Episode = lease lifetime on a node.** Claim or handoff → hydrate lazily
  (on first use, or eagerly on `PREPARE`); release, lease lost, or 10 min
  idle → evict.
- **Write-through only.** The episode hook offers no way to buffer a durable
  fact in memory. GC of an idle episode, resumption of a months-old task, and
  failover are the same operation: rehydrate from the DB.
- **Cooperative rebalancing touches only cold sessions.** When an owner is no
  longer HRW-preferred for an idle session whose episode has been evicted, it
  releases the lease; the preferred node claims it on the next round. Moving
  a cold session costs nothing, so rebalancing never costs TTFT.
- A **stuck-session watchdog** (default ~5 min without progress, per run
  type) transfers a session whose driver is wedged on a live node to its
  next-ranked node (a transfer, not a release — §8.3) — the case per-row
  lease renewal would have caught.

---

## 13. Storage

Decision: **OceanBase is the system of record; Redis only for state that can
be lost and rebuilt.** Anything that carries a fencing token or was
acknowledged must be on storage that acks only after a majority of replicas
have the write. The full comparison, what a storage failover does to fencing
tokens, and OceanBase-specific rules (co-partitioning, table groups, unknown
commit outcomes) are in **[storage.md](storage.md)**.

---

## 14. Capacity and cost model

Statements per lease/routing operation (renewal is one write per node per
tick; owner pickup is one indexed query per node per tick, never per
session), the 10 k-QPS and 100 k-rounds/s scenarios, and how to replace the
estimates with measurements are in **[capacity.md](capacity.md)**.

---

## 15. Parameters

All configurable; defaults chosen for RTO ≤ 30 s.

| Parameter | Default | Note |
| --- | --- | --- |
| Heartbeat interval | 5 s | Also renews leases (node-level liveness) |
| Placement-stale threshold | 10 s | Relative staleness |
| Observer recovery grace | = claim threshold (15 s) | No peer is declared dead until the observer's own heartbeats have been healthy this long (§6.3) |
| Claim threshold | 15 s | Effective lease TTL |
| Self-fence margin | 2 s | Deadline measured from heartbeat send time |
| Non-preferred claim grace | 3 s | |
| Safety-net sweep | 30–60 s | |
| Drain timeout | 25 s | Must fit the platform stop timeout |
| Episode idle eviction | 10 min | |
| Stuck-session watchdog | ~5 min | Per run type |
| Hint cache | bounded LRU | Entries dropped on roster change or redirect |

Crash RTO ≈ claim threshold (15) + roster poll (≤ 5) + grace (0 for the
preferred node, 3 otherwise) + rehydrate (1–3): typically ≈ 18–23 s when the
preferred node claims; worst case ≈ 26 s (15 + 5 + 3 + 3) when a non-preferred
node claims — inside the 30 s target, with ~4 s of headroom. This bound
assumes every surviving observer has been healthy for at least one claim
threshold (§6.3). If a peer dies while the survivors are still inside their
recovery grace (they restarted, or storage just recovered), the earliest
claim moves out by up to the remaining grace, so the bound does not hold for
that sequence.

---

## 16. Schema and ports

### 16.1 Tables (pinned names; sibling sessions build against them)

`bcs_node_roster` — pinned: `node_id`, `state` (`active`|`draining`),
`started_at`, `last_heartbeat_at`. Added and justified here: `env`,
`member_name` (stable placement identity), `advertise_addr` (east-west
address), `build_version` (rollouts and diagnostics). A registering node nulls
`last_heartbeat_at` on rows with its own `(env, advertise_addr)` (§6.2), so an
index on `(env, advertise_addr)` is needed.

`bcs_task_leases` — pinned: `session_id` (key), `node_id`, `fencing_token`
(monotonic BIGINT), `expires_at`. Changed meaning: `expires_at` is NULL while
held and set on release/handoff (D5). Added: `env`, `write_seq` (fence step
and handoff delta detection). The "has pending work" signal (§10.1) is
either a column here or derived from run/queue tables — decided in the plan,
weighing lock contention with the fence step.

New numbered migrations for MySQL, SQLite and OceanBase (with partition
clause and table group); committed migrations are never edited.
`bcs_dispatch_records` is **not** created here (item 3).

### 16.2 Ports and wiring

Contracts in `bcs-service-api` (the `LeaderElectionPort` precedent);
implementations under `services/*`; persistence through `bcs-db-api`:

- `NodeRosterPort` — register, heartbeat, set draining, read live roster.
- `PlacementPolicy` — `rank(key, roster) → ordered members`; HRW default.
- `TaskLeasePort` — claim, release, transfer, owned set, and a
  `fence(session_id, token) → DbTransactionStep` builder that other stores
  prepend to their transaction scripts (item 3's dispatch records use it).

Each has SQL (MySQL/OceanBase/SQLite), standalone and Noop/Mock
implementations with conformance tests under the repo's contract-test
conventions. Configuration-driven wiring (no scattered `is_local`).
**Singlebox runs the real SQL leases on SQLite**, so the takeover path is
exercised locally. SQLite serializes writers database-wide; fencing remains
correct, only coarser.

---

## 17. Deviations from Doc 02

Doc 02 is not edited from this work; corrections are for the owner to land.

| # | Doc 02 says | This design | Same guarantee? |
| --- | --- | --- | --- |
| **D1** | Each durable write is `… WHERE lease_token = my_token` | Token-checked update of the lease row as the first step of the same transaction (§8.4) | **Stronger.** A per-row token only protects rows the new owner has already rewritten; a central check protects every write from the moment of the claim. No per-table schema change |
| D2 | (silent) | Recovery scanner and delivery scheduler move from leader-gated to lease-scoped (§10.7) | Required for N > 1 |
| D3 | "node id" | `node_id` is unique per process run | Clarification; enables bulk renewal and safe restarts |
| D4 | "plain consistent hashing first" | Rendezvous hashing | Same family |
| **D5** | Per-lease expiry, renewed while driving | Lease liveness = owner's heartbeat; `expires_at` NULL while held | Same safety; renewal cost O(nodes) instead of O(sessions) |
| **D6** | (implied: rounds written, owner picks up) | Entry node forwards to the owner; durable write + pickup is the fallback (§9) | Same correctness; TTFT ms instead of ≤ 1 s |
| **D7** | (silent) | Stable `member_name` for placement, separate from per-run `node_id` | Placement survives restarts |

---

## 18. Notes for sibling items

Findings from this design that items 2–5 must act on (forwarding per event,
socket draining, the overlap between `bcs_message_deliveries` and Doc 03's
dispatch record, Task-only side-effecting steps, stable `messageId`,
idempotency across transports, exactly-once wording) are collected in
**[cross-item-notes.md](cross-item-notes.md)**.

---

## 19. Open decisions

| # | Decision | Recommendation |
| --- | --- | --- |
| 1 | Who builds the node-to-node peer channel (needed by item 1 forwarding and item 2) | Item 1 builds the shared layer; item 2 reuses it |
| 2 | TTFT budget BCN may add at p99 | Decides whether LB header hashing is needed from day one |
| 3 | Peer authentication | mTLS if infra provides certificates; otherwise a shared secret on an internal-only port |
| 4 | Deploy platform: default stop timeout; can it run a pre-stop hook per group? | Needed for drain sizing and double-move avoidance |
| 5 | Does production run 2 BCS replicas on one database today? | If yes, pin to 1 until item 1 lands, or confirm CAS guards |
| 6 | Where the bot `max_running` cap is enforced once several nodes schedule | In the DB (conditional insert / count under CAS), in item 1 |
| 7 | Shape of the "has pending work" signal | Decide in the plan |
| 8 | Timer durability audit | In the plan; any in-memory-only timer is a gap |

---

## 20. Delivery plan

One PR at a time on one branch, each targeting `totalfrank`:

| PR | Contents |
| --- | --- |
| PR0 | This design; then `spec.md`, `plan.md`, `tasks.md` (with a delivery table updated by every later PR) |
| PR1 | Ports, migrations, SQL/standalone/Noop implementations, conformance tests — sibling item 3 can start after this |
| PR2 | Node lifecycle: identity, heartbeat, roster, readiness/liveness, drain, config wiring |
| PR3 | Executor: lease-scoped recovery and scheduling, failure discovery, takeover with the reconciliation stub, episodes |
| PR4 | Peer channel, forwarding, hint cache, durable fallback, handoff protocol |
| PR5…n | Fencing existing owner writes, one group per PR (run status and node progression first) |

---

## 21. Rejected alternatives

| Alternative | Why rejected |
| --- | --- |
| Consistent hashing as the correctness mechanism | Fails under partition and membership disagreement; kept only as placement |
| Active/standby as the end state | Caps throughput at one node; deploys become availability events (may appear as a rollout milestone: the lease design degenerates to it with N = 2) |
| Sticky sessions at the LB | Couples placement to L7 infrastructure; invisible to the executor; useless for between-rounds work |
| etcd / ZooKeeper / gossip for membership | Linearizable membership is not needed; the lease already lives next to the data; new infrastructure |
| Per-row lease expiry renewed per lease | O(sessions) writes per tick (~120 k/s at 600 k sessions) |
| Per-row `lease_token` columns on every table | Protects only rows the new owner has rewritten; schema change across many stores |
| Polling-only pickup of rounds | ≤ 1 s added to TTFT |
| Message queue (e.g. RabbitMQ consistent-hash exchange) on the request path | Third membership view, ordering lost on ring change, second lease mechanism, still needs peer addressing for the return stream, second durable store and availability dependency |
| Log-based design (Kafka partitions as placement, consumer groups as ownership) | Coherent, but sessions move in partition-sized groups, rebalances interrupt consumers, DB fencing still needed — an architecture change |
| Redis as the system of record | Async replication can lose acked writes and reissue a fencing token; everything in RAM at petabyte scale |
| Redlock / `GET_LOCK()` | No fencing token |

---

## 22. References

- A2A specification (§3.1.1 Send Message, §3.1.6 Subscribe to Task, §3.3.1
  Idempotency, §3.3.4 capability validation) and `specification/a2a.proto`
  — <https://github.com/a2aproject/A2A>
- M. Kleppmann, "How to do distributed locking" (2016) — fencing tokens and
  the Redlock critique
- M. Burrows, "The Chubby lock service for loosely-coupled distributed
  systems" (OSDI 2006) — sessions, grace periods after master failover
- D. Thaler, C. Ravishankar, "Using name-based mappings to increase hit
  rates" (1998) — rendezvous hashing
- V. Mirrokni, M. Thorup, M. Zadimoghaddam, "Consistent Hashing with Bounded
  Loads" (2017)
- J. Saltzer, D. Reed, D. Clark, "End-to-end arguments in system design"
  (1984)
- In repo: `apps/bcs/docs/bcs-provider-2.0-sse-protocol.md`,
  `apps/bcs/docs/message-delivery-service-api.md`,
  `apps/bcs/specs/2026-09-21-nested-fixed-loops/capacity.md`,
  `docs/arch/arch.rules.md`
