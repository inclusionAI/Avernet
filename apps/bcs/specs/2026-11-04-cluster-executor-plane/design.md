# Cluster Executor Plane — Design

[简体中文](design.zh-CN.md)

- Date: 2026-11-04 · Status: design agreed; input to the SDD spec
- Scope: item 1 of BCN cloud orchestration — how N BCN nodes share the work
  of driving task sessions, survive crashes and deploys, and never let two
  nodes drive the same session at once.
- Companion docs: [storage.md](storage.md) (why OceanBase),
  [capacity.md](capacity.md) (cost model),
  [cross-item-notes.md](cross-item-notes.md) (what items 2–5 must provide).

---

## 1. The problem

BCN runs as several nodes behind a load balancer, all sharing one highly
available database. Each personal-agent task is a **task session** that runs
for minutes to months. We need:

1. **Exactly one node drives a session at a time** (single writer).
2. **A node failure never loses or fails a task.** Another node takes over.
3. **Low latency.** A new round must reach the node that has the session warm
   in memory, without waiting for a poll.
4. **Painless deploys.** Restarting a group of nodes must not hurt
   time-to-first-token (TTFT).

The whole design rests on one sentence:

> **Hash = fast guess. Lease = truth. Fencing token checked by storage = safety.**

Nothing about correctness depends on nodes agreeing on membership, on clocks,
or on the load balancer.

---

## 2. The big picture

![Overview: the PA reaches any node through the load balancer; the entry node forwards to the lease owner over the peer channel; all nodes share one database holding the roster, task leases and session data; bot sockets terminate on whichever node accepted them.](assets/overview.svg)

| Mechanism | Answers | Wrong answer costs |
| --- | --- | --- |
| **Roster** — one row per node, with a heartbeat | Which nodes are alive? | Latency only |
| **Placement** — rendezvous hashing over the roster | Which node *should* drive session S? | One extra hop or one rehydration |
| **Lease + fencing token** — one row per session | Which node *does* drive S? | — this is the correctness mechanism |
| **Routing** — forward to the owner, redirect on mismatch | How does a round reach the owner fast? | Falls back to a durable write |

Two kinds of traffic:

- **North-south** (clients → cluster) goes through the load balancer, which
  only spreads requests over ready nodes.
- **East-west** (node → node) goes directly to a peer's advertised address:
  forwarding rounds, handoffs, relaying bot traffic. The roster is the
  address book.

Nodes never coordinate with each other directly to decide anything. The
shared database is the only place they meet.

### Vocabulary

| Term | Meaning |
| --- | --- |
| `member_name` | Stable identity of a node slot (e.g. host name); survives restarts. Used for placement |
| `node_id` | Identity of one process run (`member_name` + a fresh ULID). Used for ownership |
| `advertise_addr` | Internal `host:port` peers use to reach this node |
| Owner | The node whose `node_id` is on S's lease row and whose heartbeat is fresh |
| Entry node | The node the load balancer delivered a request to |
| Claim / Release / Transfer | Take an unowned lease / give one up / hand one to a chosen successor |
| Episode | The period a node keeps S loaded in memory (a cache lifetime, not data) |

---

## 3. Membership: who is alive?

Each node keeps one row in `bcs_node_roster` and refreshes `last_heartbeat_at`
every 5 s. States are `active` and `draining`. **"Dead" is never written** —
a crashed node can't write anything — it is derived: the heartbeat is too old.

**Why a table in the shared database** (not etcd, ZooKeeper or gossip):
membership here is only a hint. The one fact that must be linearizable — who
owns a session — already lives in the database next to the data. A separate
coordination service would add infrastructure and a second source of truth
for no gain. Cost: 20 nodes × one write per 5 s = 4 writes/s.

**Two identities.** `node_id` changes on every process start: a restarted
process has none of the old memory, so it is a *different owner*, and "is
this my lease?" becomes a simple equality check. `member_name` stays the
same, so placement (§4) gives a restarted node the same sessions as before.

**Restart at the same address.** When a node registers, it marks every other
roster row with the same `advertise_addr` as no longer fresh (heartbeat set
to NULL). Two processes cannot listen on one address, so that row belongs to
a dead run, and its leases become claimable at once instead of after a
timeout. The row itself is kept so survivors can still find its sessions.

**One clock.** All time comparisons use the database's `NOW()`, never node
clocks, so clock skew between nodes cannot make a live node look dead.

**Relative staleness.** A node is stale when it is behind *the rest of the
cluster*, not behind the wall clock:

```
stale(D) = max(last_heartbeat_at over all rows) − last_heartbeat_at(D)
```

After a short storage hiccup everyone missed heartbeats together, so nobody
looks dead — no takeover storm.

**Recovery grace.** After a *long* outage, the first node to heartbeat again
moves the maximum forward while healthy peers still look old. So an observer
must be healthy itself: each node tracks `healthy_since` (its first
successful heartbeat after its last failure, or after start) and treats a
peer as dead only once `now − healthy_since ≥ claim threshold`. During that
window every healthy peer heartbeats several times; only truly dead nodes
stay stale. (Chubby gives clients the same kind of grace after its own
master fails over.)

Two thresholds on the same heartbeat: a node leaves **placement** after 10 s
(cheap to be wrong) and its leases become **claimable** after 15 s (costlier
to be wrong).

---

## 4. Placement: rendezvous hashing

![Rendezvous hashing: each live node scores hash(key ‖ member_name); the highest score wins. When D joins only sessions D now wins move; when B leaves, only B's sessions move, each to its own second-ranked node.](assets/hrw-placement.svg)

For session S, every live node gets a score:

```
bucket(S)    = xxh3(user_id ‖ session_id) mod 4096               -- fixed seed, stored on the lease row
score(node)  = xxh3(bucket(S) ‖ node.member_name)
preferred(S) = the active node with the highest score
```

Hashing the session's **bucket** rather than the session itself keeps every
property below (4096 buckets ≫ nodes, so balance is unaffected) and lets
failover split a dead node's sessions with an indexed column (§8).

Every node computes the same ranking in microseconds with no coordination.
When a node joins or leaves, only the sessions whose winner changed move —
the minimum possible (1/N) — and a departed node's sessions spread evenly,
each to its own second-ranked node. The sorted scores double as a fallback
order. A bounded-load variant (skip overloaded nodes while walking the list)
can be swapped in later.

Why rendezvous rather than alternatives: `hash % N` reshuffles everything on
every change; a ring needs ~100+ virtual nodes per node to balance; jump hash
cannot remove a node from the middle (a crash); a fixed slot map needs a
coordinator.

**Placement is not ownership.** Placement only decides *who should claim* a
session that nobody owns. After a node joins, the hash may say D while the
lease still says A — existing leases don't move. So the hash is a guess that
the target node checks (§7).

---

## 5. Ownership: fenced leases

### 5.1 Why a lock alone is not enough

![Zombie scenario: A holds token 7 and pauses; the lease becomes claimable; B claims token 8 and writes; A wakes and its write carrying token 7 is rejected by the database.](assets/zombie-fencing.svg)

A node can pause (GC, VM migration, kernel stall) or be cut off, then wake up
believing it still owns S. Any "am I still the owner?" check can be followed
by another pause before its write lands. **Only the storage the write lands
in can reject it.** So every ownership change increments a **fencing token**,
every owner write carries its token, and the database refuses a stale one.

### 5.2 The lease row

`bcs_task_leases` has one row per session: `node_id` (owner, or `''` when
released), `fencing_token` (only ever increases), `expires_at` (NULL while
held), `assigned_at` (when `node_id` last changed), `bucket` (§4),
`write_seq`, and `has_pending_work` + `pending_seq` (§5.4).

**A lease is claimable** when it is released, or its owner's heartbeat is
stale (with the recovery grace above), or its owner's run is known dead
(same-address restart). There is no per-lease renewal: one node heartbeat
keeps all of that node's leases alive. Per-lease renewal would cost one row
write per session per tick — about 120 k writes/s at 600 k active sessions.

### 5.3 Operations

**Claim** — one transaction, no prior knowledge of the token needed:

```sql
UPDATE bcs_task_leases
   SET fencing_token = 1 + fencing_token, node_id = :me
 WHERE session_id = :s AND <lease is claimable>;
SELECT node_id, fencing_token FROM bcs_task_leases WHERE session_id = :s;
```

If the read-back shows `node_id = me`, I own S with that token; otherwise it
names the current owner. Exactly one claimant wins: the update takes the row
lock, and a racing claimant then sees a fresh owner and changes nothing. The
same read-back also tells a successor it already owns S if a handoff notice
was lost.

**Release** — give S up with no successor (only for sessions with nothing to
do). **Transfer** — hand S directly to a chosen successor with token + 1
(only the current holder can do this; used by drain, §9).

Rules:

- **Tokens never go backwards.** Lease rows are never deleted while the
  session lives; release clears `node_id` instead. A re-created row starting
  at 1 would let an old zombie token match again.
- **Expiry decides who may claim; the token decides who may write.** A late
  owner whose lease nobody has claimed can still write safely.
- **Pending work is never released, only transferred.** A released lease is
  invisible to failure discovery (§8), so a session that still needs a driver
  must always have one.

### 5.4 The pending-work flag

`has_pending_work` says "this session needs a driver now": a run in
progress, queued deliveries, a timer, or input nobody has processed. Every
write that **sets** it also increments `pending_seq`. Every **clear** —
including the one inside a release — only succeeds if `pending_seq` still
equals the value the owner saw when it confirmed it had processed
everything. So input that arrives at the last moment can never be erased or
stranded on a released lease: the release simply fails and the owner keeps
the session.

### 5.5 Fenced writes

![A fenced write is one transaction: first a checked update of the session's lease row (token must match; write_seq increments), then the durable writes. A stale token makes step 1 touch zero rows and the whole transaction rolls back.](assets/fenced-write.svg)

Every durable write an owner makes is one transaction whose first step is:

```sql
UPDATE bcs_task_leases SET write_seq = write_seq + 1
 WHERE session_id = :s AND fencing_token = :mine      -- must touch exactly 1 row
```

followed by the real writes (run state, checkpoints, dispatch records…). If
the token is stale, the first step touches 0 rows, the whole transaction
rolls back, and the caller gets **LeaseLost**: stop driving S, drop it from
memory, never retry. The row lock makes a concurrent claim wait until the
write commits, so there is no gap between "check" and "write" for a pause to
fall into. (`write_seq` always changes, so "1 row" is reliable; it also lets
a handoff successor see what changed.)

Rules: the check and the writes are in **one transaction**, in the **same
database**, for **one session**. If a commit's outcome is unknown (an error
during a storage leader switch), **read back** — never blindly repeat.

**Not fenced:** recording an inbound round as *input* (any node may do this),
reads, and the lease operations themselves.

**What fencing cannot do:** recall a network request a zombie already sent.
That is covered by write-ahead dispatch records with reused idempotency keys
and dedup at the receiving agent (item 3).

**Self-fencing** is a courtesy on top: a node that can't heartbeat in time
stops driving and fails its readiness probe, shrinking the zombie window.

---

## 6. Why the database, not a lock service

| Option | Fencing token? | Checked where the data lives? | Verdict |
| --- | --- | --- | --- |
| **Lease row in the shared database** | ✓ | ✓ same transaction | **Chosen** |
| etcd / ZooKeeper | ✓ | ✗ — the database would still need its own lease row | Rejected |
| Redis lock / Redlock | ✗ | ✗ | Rejected (safety depends on clocks and pause length) |

**Put the lock in the same store as the data it protects.** The store must
acknowledge a write only once a majority of replicas have it — otherwise a
storage failover can hand the same token to two owners (see
[storage.md](storage.md)).

---

## 7. Routing a round to its owner

Personal-agent rounds are latency-sensitive, so a round must not sit in the
database waiting for the owner's next poll. The entry node guesses in memory
and forwards; the target checks against what it knows.

![Routing: the entry node uses its hint cache or HRW to pick a target; the target serves if it owns S, claims if S is unowned, or replies NOT_OWNER(X); the entry node caches the hint and makes at most one more hop; on failure it tries the next-ranked node, then writes the round durably.](assets/request-routing.svg)

```
ENTRY NODE
  target = hint_cache[S] or HRW(S)                 -- microseconds, no database
  forward to target (or handle locally)
  on NOT_OWNER(X): remember X, forward to X        -- at most 2 hops
  on unreachable: try the next-ranked node; if nobody can serve or claim S
                  yet → durable fallback

TARGET NODE
  do I hold S's lease?  (in memory)  → serve: persist the round (fenced), drive, stream tokens back
  otherwise → claim transaction       → I own it now: serve
                                      → owned by X: reply NOT_OWNER(X)
```

| Case | What happens | Cost |
| --- | --- | --- |
| Stable cluster | The guess is the owner | One hop, ~2–7 ms added to TTFT |
| Just after a node joins | Hash says D, lease says A → `NOT_OWNER(A)` → remembered | One extra hop, once |
| Cold session | The preferred node claims it | One claim transaction |
| Owner crashed | Next-ranked node claims if possible, else durable fallback | TTFT ≤ takeover time |
| Forward timed out but the owner did process it | Two copies of one round | Deduplicated on `(caller, session, messageId)` |

**Durable fallback** — one transaction on S's lease row: if the lease is
claimable, the entry node claims S itself and serves (placement is only a
preference); otherwise it records the round as input, sets the pending-work
flag, and acknowledges. Recovery (§8) picks it up.

The entry node proxies the owner's token stream back to the PA, which only
ever talks to the load balancer. Nothing here depends on the load balancer:
it needs only unique node ids, the shared database, direct peer addresses,
and the same hash on every node. A balancer that hashes on a session header
just makes forwarding rarer.

---

## 8. Failover: when a node crashes

There is no handoff message after a crash. Survivors must **discover** the
dead node's sessions and split them without talking to each other.

![Failover: node D stops heart-beating; once D's leases are claimable, each survivor queries D's sessions with pending work at every roster poll and claims those HRW assigns to it; idle sessions are claimed on demand by their next round.](assets/failover-discovery.svg)

1. **Roster poll finds a dead node, then bounded, split queries.** Every
   heartbeat each node already reads the roster. While D is dead, each
   survivor computes which buckets rank it first without D, and reads only
   those, in bounded pages:
   `SELECT … FROM bcs_task_leases WHERE node_id = :D AND has_pending_work
   AND bucket IN (:mine) LIMIT :batch` (index on `node_id, has_pending_work,
   bucket`). So each survivor reads only its own share, never the whole set.
   Per poll it claims at most one batch (default 2,000), polls every 1 s
   while a backlog remains, and backs off on storage errors. Sessions with
   unprocessed input are taken first. Buckets whose first choice is also
   down are picked up after a short grace by the next-ranked node. Because
   the query repeats every poll, work that becomes pending later (a fallback
   round) is found too. A node holding 300 k pending sessions spread over 19
   survivors is claimed in under 10 s; claiming is cheap because state is
   reloaded lazily on first use.
2. **Claim on demand.** Idle sessions are not scanned at all: the next round
   for one finds the owner dead and the preferred survivor claims it — a
   normal cold start.
3. **Safety-net sweep** every 30–60 s for anything missed.

After claiming: reload state from the database (exactly like resuming a cold
session — *failover is a cache miss*), run the **reconciliation hook** for
in-flight dispatches (item 3: probe by key or read the agent's task, never
blindly resend), and resume.

**Taking over earlier on evidence.** Because fencing makes a wrong guess
safe, evidence can replace waiting: a restart at the same address frees the
old run's leases at once; a process that panics releases its leases on the
way out. A node that is alive but silently unreachable gives no evidence —
for it, wait the claim threshold.

| Situation | Sessions resume after |
| --- | --- |
| Restart in place | restart time + ≤ 5 s |
| Crash, preferred survivor claims | ~18–23 s |
| Crash, worst case (second choice claims) | ~26 s — within the 30 s target, assuming survivors are past their own recovery grace |

---

## 9. Planned drain and deploy

Deploy model: a group G of nodes is restarted in place, one group at a time.

**Principle: move ownership before the process stops, and keep serving until
the move.** Draining never means refusing work.

![Drain timeline: stop signal; roster draining and readiness red; sessions released or handed off cheapest first; non-resumable steps finish then hand off; bot sockets emptied last; roster row deleted; restart with same address and member_name.](assets/drain-timeline.svg)

### 9.1 The sequence for node A

1. **Stop signal:** roster `state = draining` (peers stop placing on A),
   readiness fails (the load balancer stops sending new connections), stop
   claiming. Keep serving every session A still owns.
2. **Move sessions, cheapest first** (a session that just got a round jumps
   the queue):
   - **cold** (nothing in memory) → release;
   - **warm, or waiting on something that can be picked up elsewhere** → hand
     off now;
   - **waiting on a stream that can't be resumed** → finish that step, then
     hand off (A stays owner meanwhile and does not start the next step);
   - **about to start a new step** → hand off first, so the step starts on
     the successor.
3. **Bot sockets last** (§9.4).
4. Keep relaying open PA streams and answering `NOT_OWNER` redirects.
5. **Deadline** (25 s, inside the platform's stop timeout): transfer whatever
   is left to its successors. A step still running on a non-resumable stream
   is cancelled and **re-run by the successor**. That is safe because such
   streams only ever carry read-only or idempotent steps: side-effecting
   steps are dispatched only to agents with durable, queryable tasks (items
   3 and 6). The cost is the step's time, never correctness.
6. Delete the roster row and exit. On restart: same address and
   `member_name`, new `node_id` → leftovers are claimable at once and the node
   gets its old placement back.

Successors must be outside G, or a session would move twice: a draining node
refuses a handoff and the next-ranked node is tried. If the deploy platform
can mark a whole group draining at once, double moves disappear entirely.

### 9.2 Handoff

![Handoff: A sends PREPARE; B loads S and replies READY; A pauses writes for S; A transfers the lease to B with token n+1; A sends COMMIT; B checks write_seq and drives S; A forwards held rounds and redirects later ones.](assets/handoff-sequence.svg)

1. **PREPARE** — A asks successor B to load S into memory (B is not owner yet).
2. **Pause** — A finishes its current write for S and holds new events for a
   few milliseconds.
3. **Transfer** — one database update: owner = B, token + 1.
4. **COMMIT** — A tells B; B starts driving, re-reading only what changed
   since PREPARE.
5. A forwards anything it held; later arrivals get `NOT_OWNER(B)`.

The gap for S is one database write plus one network round trip. If anything
fails midway, existing rules cover it: B dies → ordinary takeover; the COMMIT
is lost → B finds S anyway, because every node reads, each heartbeat, the
leases newly assigned to it (`WHERE node_id = :me AND assigned_at > :last −
margin`, usually empty); A writes after the transfer → fenced out. The same
read covers transfers made without a PREPARE (drain deadline, watchdog).
`:last` is the highest `assigned_at` value actually read back — never a node
clock — and `margin` exceeds the longest transaction, so a transfer that
commits after its timestamp was taken is still seen. Sessions the node
already drives are skipped, so the overlap is harmless.

### 9.3 When is it safe to move a run mid-flight?

A run can move between any two durable writes, because its whole position is
in the database. The cases that need care:

| The run is… | Move now? |
| --- | --- |
| Between steps | ✓ |
| Waiting on an agent task whose id is recorded | ✓ — the successor reads or re-subscribes to the task |
| Waiting on a dial-in bot | ✓ — the socket's node relays events to whoever owns S now |
| In the middle of sending a dispatch | Finish the send first (milliseconds) |
| Waiting on a stream that can't be resumed | ✗ — wait for that step to end (its output exists only on that stream); at the deadline it is re-run by the successor (§9.1) |
| Waiting on a timer | ✓ — provided timer deadlines are stored durably |

### 9.4 Bot sockets: a second, independent job

A dial-in bot keeps one WebSocket open to whichever node it connected to.
That node is just **transport**: it relays the bot's events to the owner of
each run's session, wherever that owner is.

![Bot socket roles: before handoff the socket holder delivers events in-process to the owner; after handoff the same socket holder relays each event to the new owner; the bot sees nothing.](assets/bot-socket-roles.svg)

- Handing off a session does **not** touch the socket: the socket's node
  simply relays the next event to the new owner, looked up per event.
- What must wait is **closing the socket**, because it may carry runs of
  sessions owned by any node. A draining node sends no new work over its
  sockets, lets in-flight runs finish (still relaying events and aborts),
  then asks the bot to reconnect, and the bot redials through the load
  balancer to another node.
- New work for that bot is never queued on the draining node. It stays a
  durable delivery owned by the session's owner, dispatched once the bot is
  reachable again.

### 9.5 Rolling out safely

- **Probes:** liveness ("is the process stuck?") stays green while draining;
  readiness ("send me work?") is red while draining, self-fenced or starting.
- **Pacing:** add one new node before removing one old one, wait ~30 s
  between steps so rehydration waves don't stack, and keep groups to ≤ 25 %
  of the cluster.
- **Mixed versions:** old and new nodes take over each other's sessions, so
  schema changes go expand → migrate → contract across releases; new durable
  formats ship the reader first and the writer later; a node that meets a
  format it can't read hands the session back instead of guessing. The lease
  protocol and the placement hash are frozen across versions.

---

## 10. Episodes: warm state is only a cache

- An episode starts when a node claims or receives S (loading lazily) and
  ends on release, lease loss, or 10 minutes idle.
- **Write-through only.** Every durable fact is written before it is
  acknowledged; memory never holds the only copy. That is why idle eviction,
  resuming a months-old task, and crash failover are the same operation:
  reload from the database.
- **Rebalancing touches only cold sessions.** When a node is no longer the
  preferred one for a session that has gone cold, it releases the lease and
  the preferred node claims it on the next round — free, so it never costs
  TTFT.
- A **stuck-session watchdog** (~5 min without progress) transfers a session
  whose driver is wedged on an otherwise healthy node.

---

## 11. Parameters

All configurable; defaults sized for a ≤ 30 s takeover.

| Parameter | Default |
| --- | --- |
| Heartbeat interval (also keeps leases alive) | 5 s |
| Placement-stale threshold | 10 s |
| Claim threshold | 15 s |
| Observer recovery grace | 15 s (= claim threshold) |
| Self-fence margin | 2 s |
| Claim grace for non-preferred nodes | 3 s |
| Failure discovery: batch per poll / poll interval while backlog remains | 2,000 rows / 1 s |
| Safety-net sweep | 30–60 s |
| Drain deadline | 25 s |
| Episode idle eviction | 10 min |
| Stuck-session watchdog | ~5 min |

---

## 12. Contracts

**Tables** (names pinned for sibling items):

- `bcs_node_roster`: `node_id`, `member_name`, `state`, `started_at`,
  `last_heartbeat_at`, `advertise_addr`, `build_version`.
- `bcs_task_leases`: `session_id`, `bucket`, `node_id`, `fencing_token`,
  `expires_at`, `assigned_at`, `write_seq`, `has_pending_work`, `pending_seq`.
  Indexes: `(node_id, has_pending_work, bucket)` for failure discovery,
  `(node_id, assigned_at)` for newly assigned leases.

**Interfaces** (protocol-neutral, each with a database implementation, a
single-node implementation and a no-op one):

- **Roster** — register, heartbeat, set draining, read the live roster.
- **Placement policy** — rank nodes for a session (rendezvous by default).
- **Task leases** — claim, release, transfer, list owned sessions, and a
  **fence step** other components put first in their transactions (item 3's
  dispatch records use it).

Single-node mode runs the same lease logic on its local database, so the
takeover path is exercised everywhere.

---

## 13. Deviations from Doc 02

| # | Doc 02 says | This design | Why |
| --- | --- | --- | --- |
| D1 | Each write carries `WHERE lease_token = my_token` | The first step of each transaction checks the session's lease row | Stronger: protects every write from the moment of a claim, with no extra column on every table |
| D2 | — | Recovery and dispatch run per lease, not on a single leader | Required for N > 1 nodes |
| D3 | "node id" | `node_id` is per process run | Makes restarts and renewal simple |
| D4 | "consistent hashing" | Rendezvous hashing | Same family, no tuning |
| D5 | Each lease expires and is renewed | Lease liveness = the owner's heartbeat | O(nodes) writes instead of O(sessions) |
| D6 | Rounds are written and picked up | Rounds are forwarded to the owner; the write is the fallback | Milliseconds instead of up to 1 s |
| D7 | — | Stable `member_name` for placement | Placement survives restarts |

---

## 14. Open decisions

| # | Decision | Recommendation |
| --- | --- | --- |
| 1 | Who builds the node-to-node channel (needed here and by item 2) | Item 1 builds it; item 2 reuses it |
| 2 | TTFT budget BCN may add at p99 | Decides whether load-balancer header hashing is needed early |
| 3 | Node-to-node authentication | mTLS if available; else a shared secret on an internal-only port |
| 4 | Deploy platform: stop timeout; group-level pre-stop hook? | Sizes the drain deadline; avoids double moves |
| 5 | Timer durability | Every run timer must be stored durably |

---

## 15. Alternatives we rejected

| Alternative | Why not |
| --- | --- |
| Consistent hashing as the correctness mechanism | Breaks when nodes disagree on membership |
| Active/standby | Caps throughput at one node; deploys become outages |
| Sticky sessions at the load balancer | Ties placement to infrastructure; useless for background work |
| etcd / ZooKeeper / gossip for membership | Not needed; the lease already lives next to the data |
| A message queue (e.g. a consistent-hash exchange) on the request path | A third membership view, lost ordering on rebalance, a second lease mechanism, and the reply stream still needs node-to-node addressing |
| Redis as the system of record | Asynchronous replication can lose acknowledged writes and reissue a token |
| Per-session lease renewal | O(sessions) writes per tick |
| Polling for new rounds | Adds up to 1 s to TTFT |

---

## 16. References

- M. Kleppmann, "How to do distributed locking" (2016) — fencing tokens
- M. Burrows, "The Chubby lock service" (OSDI 2006) — sessions, grace periods
- D. Thaler, C. Ravishankar, "Using name-based mappings to increase hit rates" (1998) — rendezvous hashing
- V. Mirrokni et al., "Consistent Hashing with Bounded Loads" (2017)
- J. Saltzer, D. Reed, D. Clark, "End-to-end arguments in system design" (1984)
- A2A specification — <https://github.com/a2aproject/A2A>
