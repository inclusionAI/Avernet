# Cluster Executor Plane — Design

[简体中文](design.zh-CN.md)

- Date: 2026-11-04 · Revised 2026-11-05 (gateway tier; fan-out over pub/sub) · Status: design
  agreed; input to the SDD spec
- Scope: item 1 of BCN cloud orchestration — how N BCN nodes share the work
  of driving task sessions, survive crashes and deploys, and never let two
  nodes drive the same session at once. The gateway tier (§7) is where item 2
  lives; it is described here because the executor plane depends on its
  contract.
- Companion docs: [storage.md](storage.md) (why OceanBase),
  [capacity.md](capacity.md) (cost model),
  [cross-item-notes.md](cross-item-notes.md) (what items 2–5 must provide).

---

## 1. The problem

BCN runs as a cluster behind a load balancer, sharing one highly available
database. Each personal-agent task is a **task session** that runs for
minutes to months. We need:

1. **Exactly one node drives a session at a time** (single writer).
2. **A node failure never loses or fails a task.** Another node takes over.
3. **Low latency.** A new round must reach the node that has the session warm
   in memory, without waiting for a poll.
4. **Painless deploys.** Restarting a group of nodes must not hurt
   time-to-first-token (TTFT).
5. **Streams survive the executor.** A crash or deploy of the node driving a
   session may pause the token stream the personal agent is reading, but must
   not close it or lose a state change.

The whole design rests on one sentence:

> **Hash = fast guess. Lease = truth. Fencing token checked by storage = safety.**

Nothing about correctness depends on nodes agreeing on membership, on clocks,
or on the load balancer.

---

## 2. The big picture

![Overview: the PA reaches a gateway through the load balancer; gateways hold every long-lived connection (PA streams and dial-out streams to supply agents); they send rounds, deltas and control events to the session's owner executor over a direct control channel; the owner applies BCN's fan-out logic and publishes on the session's pub/sub channel, which gateways subscribe to; executors hold leases and write the shared database.](assets/overview.svg)

BCN has **two tiers**, which can ship as two deployments of one codebase:

| Tier | Holds | Does | Durable state |
| --- | --- | --- | --- |
| **Gateway** | Every long-lived connection: PA streams and streams to supply agents | Terminates connections, runs dispatches, sends their output to the owner, relays the owner's fan-out to clients | None |
| **Executor** | No long-lived connections | Owns sessions through leases, drives runs and orchestration, decides and performs fan-out, writes the database | Everything, through fenced writes |

Connections live on gateways, so an executor crash or deploy never closes a
PA's stream or a supply agent's stream: output pauses until a new owner
takes over, then continues (requirement 5). Fan-out stays BCN's logic, on
the owner, and reaches gateways through a non-durable pub/sub (§7.4). The
split also turns an executor deploy into moving leases (§8).

| Mechanism | Answers | Wrong answer costs |
| --- | --- | --- |
| **Roster** — one row per node, with a heartbeat | Which gateways and executors are alive? | Latency only |
| **Placement** — rendezvous hashing over the executors | Which executor *should* drive session S? | One extra hop or one rehydration |
| **Lease + fencing token** — one row per session | Which executor *does* drive S? | — this is the correctness mechanism |
| **Routing** — forward to the owner, redirect on mismatch | How does a round reach the owner fast? | Falls back to a durable write |
| **Event log + session channel** | How does every stream see every state change and every token, across owner changes? | Seconds of delay, never a lost state change |

Three kinds of traffic:

- **PA → load balancer → gateway**: the PA's own connection (SSE, WebSocket
  or gRPC stream). The load balancer only spreads connections over ready
  gateways.
- **Gateway ↔ executor**: one long-lived, multiplexed control channel per
  pair, straight to the peer's advertised address (no load balancer). It
  carries rounds, dispatch commands, and the deltas and control events of
  running dispatches.
- **Executor → session channels → gateways**: the owner publishes each
  session's events and deltas on a non-durable pub/sub channel; every
  gateway serving that session subscribes. This is BCN's fan-out.
- **Gateway → supply agents**: outbound A2A or Provider streams. Every
  supply agent is dial-out; BCN accepts no inbound agent connections.

Executors never coordinate with each other directly to decide anything. The
shared database is the only place they meet.

### Vocabulary

| Term | Meaning |
| --- | --- |
| `member_name` | Stable identity of a node slot (e.g. host name); survives restarts. Used for placement |
| `node_id` | Identity of one process run (`member_name` + a fresh ULID). Used for ownership |
| `advertise_addr` | Internal `host:port` peers use to reach this node |
| Owner | The executor whose `node_id` is on S's lease row and whose heartbeat is fresh |
| Entry gateway | The gateway that accepted the PA's connection for a request |
| Dispatch gateway | The gateway running one dispatch: it holds the stream to the supply agent |
| Session channel | S's topic on the pub/sub; the owner publishes, gateways subscribe (§7.4) |
| Subscriber | A gateway subscribed to S's channel, relaying to a client stream |
| Delta | A piece of output text or artifact (a "token" chunk); goes to the owner, is fanned out, never stored |
| Control event | Anything that changes what should happen next; recorded by the owner (§7.3) |
| Claim / Release / Transfer | Take an unowned lease / give one up / hand one to a chosen successor |
| Episode | The period an executor keeps S loaded in memory (a cache lifetime, not data) |

---

## 3. Membership: who is alive?

Each node keeps one row in `bcs_node_roster` and refreshes `last_heartbeat_at`
every second. The row's `role` is `executor` or `gateway`; only executors
take part in placement and leases, while gateways are listed so executors can
reach them and notice when one dies. States are `active` and `draining`.
**"Dead" is never written** — a crashed node can't write anything — it is
derived: the heartbeat is too old.

**Why a table in the shared database** (not etcd, ZooKeeper or gossip):
membership here is only a hint. The one fact that must be linearizable — who
owns a session — already lives in the database next to the data. A separate
coordination service would add infrastructure and a second source of truth
for no gain. Cost: one tiny write per node per second.

**Two identities.** `node_id` changes on every process start: a restarted
process has none of the old memory, so it is a *different owner*, and "is
this my lease?" becomes a simple equality check. `member_name` stays the
same, so placement (§4) gives a restarted executor the same sessions as
before.

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

Two thresholds on the same heartbeat: an executor leaves **placement** after
3 s (cheap to be wrong) and its leases become **claimable** after 5 s — five
missed heartbeats (costlier to be wrong). A gateway counts as dead after the
same 5 s; that is when its dispatches are moved (§7.6).

---

## 4. Placement: rendezvous hashing

![Rendezvous hashing: each live executor scores hash(key ‖ member_name); the highest score wins. When D joins only sessions D now wins move; when B leaves, only B's sessions move, each to its own second-ranked node.](assets/hrw-placement.svg)

For session S, every live executor gets a score:

```
bucket(S)    = xxh3(user_id ‖ session_id) mod 4096               -- fixed seed, stored on the lease row
score(node)  = xxh3(bucket(S) ‖ node.member_name)
preferred(S) = the active executor with the highest score
```

Hashing the session's **bucket** rather than the session itself keeps every
property below (4096 buckets ≫ nodes, so balance is unaffected) and lets
failover split a dead node's sessions with an indexed column (§9).

Every node — gateways included — computes the same ranking in microseconds
with no coordination. When an executor joins or leaves, only the sessions
whose winner changed move — the minimum possible (1/N) — and a departed
node's sessions spread evenly, each to its own second-ranked node. The
sorted scores double as a fallback order. A bounded-load variant (skip
overloaded nodes while walking the list) can be swapped in later.

Why rendezvous rather than alternatives: `hash % N` reshuffles everything on
every change; a ring needs ~100+ virtual nodes per node to balance; jump hash
cannot remove a node from the middle (a crash); a fixed slot map needs a
coordinator.

**Placement is not ownership.** Placement only decides *who should claim* a
session that nobody owns. After an executor joins, the hash may say D while
the lease still says A — existing leases don't move. So the hash is a guess
that the target executor checks (§6).

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
held), `adopted` (has the owner started driving it?), `bucket` (§4),
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
(only the current holder can do this; used by drain, §8).

Rules:

- **Tokens never go backwards.** Lease rows are never deleted while the
  session lives; release clears `node_id` instead. A re-created row starting
  at 1 would let an old zombie token match again.
- **Expiry decides who may claim; the token decides who may write.** A late
  owner whose lease nobody has claimed can still write safely.
- **Pending work is never released, only transferred.** A released lease is
  invisible to failure discovery (§9), so a session that still needs a driver
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

followed by the real writes (run state, checkpoints, dispatch records,
control events for subscribers…). If the token is stale, the first step
touches 0 rows, the whole transaction rolls back, and the caller gets
**LeaseLost**: stop driving S, drop it from memory, never retry. The row lock
makes a concurrent claim wait until the write commits, so there is no gap
between "check" and "write" for a pause to fall into. (`write_seq` always
changes, so "1 row" is reliable; it also lets a handoff successor see what
changed.)

Rules: the check and the writes are in **one transaction**, in the **same
database**, for **one session**. If a commit's outcome is unknown (an error
during a storage leader switch), **read back** — never blindly repeat.

**Not fenced:** recording an inbound round as *input* (any executor may do
this), reads, and the lease operations themselves.

**What fencing cannot do:** recall a network request a zombie already sent.
That is covered by write-ahead dispatch records with reused idempotency keys
and dedup at the receiving agent (item 3).

**Self-fencing** is a courtesy on top: a node that can't heartbeat in time
stops driving and fails its readiness probe, shrinking the zombie window.

---

## 6. Routing a round to its owner

Personal-agent rounds are latency-sensitive, so a round must not sit in the
database waiting for the owner's next poll. The entry gateway guesses in
memory and forwards; the target executor checks against what it knows.

![Routing: the entry gateway uses its hint cache or HRW to pick a target executor; the target serves if it owns S, claims if S is unowned, or replies NOT_OWNER(X); the gateway caches the hint and makes at most one more hop; on failure it tries the next-ranked executor, which serves, claims, or writes the round durably.](assets/request-routing.svg)

```
ENTRY GATEWAY
  target = hint_cache[S] or HRW(S)                 -- microseconds, no database
  send Submit(round) to target
  on NOT_OWNER(X): remember X, send to X           -- at most 2 hops
  on unreachable: try the next-ranked executor; if no executor answers at
                  all → retryable error to the PA

TARGET EXECUTOR
  do I hold S's lease?  (in memory)  → serve: dedup, persist the round (fenced), reply with the Task
  otherwise → claim transaction       → I own it now: serve
                                      → owned by X, reachable: reply NOT_OWNER(X)
                                      → owner dead, not yet claimable: durable fallback
```

| Case | What happens | Cost |
| --- | --- | --- |
| Stable cluster | The guess is the owner | One hop, ~2–7 ms added to TTFT |
| Just after an executor joins | Hash says D, lease says A → `NOT_OWNER(A)` → remembered | One extra hop, once |
| Cold session | The preferred executor claims it | One claim transaction |
| Owner crashed | Next-ranked executor claims if possible, else durable fallback | TTFT ≤ takeover time |
| Forward timed out but the owner did process it | Two copies of one round | Deduplicated on `(caller, session, messageId)` |

**Durable fallback** — one transaction on S's lease row, run by whichever
executor received the round: if the lease is claimable, that executor claims
S itself and serves (placement is only a preference); otherwise it records
the round as input, sets the pending-work flag, and acknowledges. Recovery
(§9) picks it up. Gateways never touch leases.

The entry gateway subscribes to S's session channel (§7.4) **before** it
sends `Submit`, so nothing the owner publishes for this round can be missed,
and relays it to the PA. For a brand-new session whose id only exists once
the round is accepted, it subscribes on `Accepted`, which carries the
current `event_seq` and `chunk_seq`, and catches up once from there with a
snapshot (§7.5). Nothing here
depends on the load balancer: it needs only unique node ids, the shared
database, direct peer addresses, and the same hash on every node. A balancer
that hashes on a session header just makes forwarding rarer.

---

## 7. The gateway tier and fan-out

### 7.1 Connections

![One round end to end: PA's SSE reaches GW1 through the load balancer; GW1 submits the round to owner E3; E3 places the dispatch on GW1; GW1 streams from the supply agent and sends deltas and control events to E3; E3 applies BCN's fan-out logic and publishes on session S's channel; GW1, subscribed to S, relays to the PA.](assets/round-flow.svg)

| Leg | Protocol | Notes |
| --- | --- | --- |
| PA → load balancer → gateway | The PA's SSE, WebSocket or gRPC stream | An L4 balancer passes the connection through. An L7 balancer must not buffer responses and must allow idle gaps longer than the gateway's SSE heartbeat (`: ping` every 15–30 s) |
| Gateway ↔ executor | One long-lived, multiplexed, bidirectional channel per pair (e.g. gRPC over HTTP/2) | Carries rounds, deltas and control events from dispatches, dispatch commands and results; keepalive pings detect a dead peer in seconds |
| Executor → gateways | **Session channels** on a non-durable, sharded pub/sub | BCN's fan-out: the owner publishes each session's events and deltas; every gateway serving that session subscribes (§7.4) |
| Gateway → supply agent | A2A `SendStreamingMessage` / `SubscribeToTask`, or a Provider stream | One per dispatch, opened by the dispatch gateway |

### 7.2 Dispatch placement and results

When the owner starts a step, it chooses where the dispatch runs: by default
the entry gateway of the caller's stream (one fewer hop for the PA's
output), otherwise any healthy gateway (group members, background steps, a
PA that disconnected).

The owner records **which gateway runs the dispatch** in the dispatch record,
inside its fenced write (item 3 owns the record). That fact must be durable:
a new owner needs it to supervise, cancel or collect the dispatch after a
takeover.

The dispatch gateway sends everything the agent produces to S's current
owner, looked up per message (hint cache + `NOT_OWNER`), never to a fixed
node:

- **Deltas** in batches (every 200 ms), numbered by `chunk_seq`, without
  acknowledgement: they are soft.
- **Control events and the final result**, each with a per-dispatch sequence
  number, **retried until the owner acknowledges** — an acknowledgement means
  the owner recorded it fenced. The owner drops repeats by `(dispatch, seq)`.
- **Progress counts** every few seconds, for the stuck-dispatch watchdog.

The dispatch gateway also **keeps the step's output so far** until the owner
acknowledges the result (it needs it to assemble the result anyway). A new
owner takes it over with `FetchBuffer(D)`, so an owner change loses no
output. If the dispatch gateway dies first, the owner moves the dispatch
(§7.6); the output is recovered from the agent's task, or the step is
re-run.

### 7.3 What a stream carries

An A2A stream carries four kinds of event: `Task` (the agent created a task
and names its id), `TaskStatusUpdateEvent` (the state changed:
`working`, `input-required`, `completed`…), `TaskArtifactUpdateEvent`
(a chunk of output, `append: true`) and `Message` (agents that reply without
a task). All of them go to the owner; what differs is whether they are
durable:

| Event | Example | Durable? | What the owner does |
| --- | --- | --- | --- |
| Agent's `Task` | `taskId: ta-77` | **Yes** | Records it on the dispatch, so the task can be found again after any crash |
| Artifact chunks (deltas) | "I found two options: …" | No | Adds them to its step buffer and fans them out |
| `working` + progress text | "Searching flights…" | No | Fans it out; counts as liveness |
| `input-required` / `auth-required` | "Which one should I book?" | **Yes** | Changes the Task's state, starts the human-wait timer, routes the PA's answer back to `ta-77` |
| `completed` / `failed` | `final: true` | **Yes**, with the assembled output | Records the step result and decides what happens next |
| Coordination tool calls | `tool_call_end` of a coordination tool | **Yes** | BCS acts on it mid-run |

**Fan-out is BCN's logic, and it runs on the owner.** For every event the
owner decides who sees it and in what form: the caller's stream, other
viewers allowed to watch the session, a group's watchers; only the main
speaker's stream or every member's; tags such as `[from:botName]`. Two kinds
of recipient are handled differently:

- **People's streams** (the PA, viewers, the workbench) get the event on the
  session's channel (§7.4).
- **Other bots in a group** get a **delivery**: the owner dispatches the
  member's complete message to them as a durable record under the fence,
  never as a token stream. Mentions are parsed on the complete message.

The PA sees **the owner's Task**, not the agent's. A downstream `completed`
ends one *step*: for a single agent the owner then completes its own Task,
but in a group session it starts the next member's turn and the PA's Task
stays `working`. The owner publishes a step's last delta before the event
that ends the step, and it is the only publisher on the channel, so the PA
never sees "completed" before the text.

### 7.4 Fan-out: session channels

![Session channels: the owner commits control events to the event log, then publishes events and deltas on S's channel; gateways subscribed to S relay them. When the owner crashes, subscribers stay on the channel; the new owner republishes S's latest event and step buffer, subscribers detect any gap in event_seq and read the missing events from the log.](assets/subscriptions.svg)

Every session S has a **channel** on a non-durable pub/sub. The owner is its
only publisher; every gateway with a stream for S subscribes. Subscribers
subscribe **by session, not by owner**, so an owner change is invisible to
them: they stay on the channel, and whichever executor owns S publishes to
it.

Messages on the channel:

- `Event(event_seq, token, event)` — a control event the PA must see. It is
  **committed to the session event log first** (in the owner's fenced commit,
  numbered by a per-session `event_seq`), then published.
- `Delta(dispatch, chunk_seq, token, chunk)` — output text, never stored.

Every message carries the publisher's **fencing token**. A subscriber drops
any message with a token lower than the highest it has seen. A stale owner's
`Event` never exists (its fenced commit fails), but a `Delta` is not stored,
so two more rules keep a paused former owner's deltas away from clients:
the **first thing a new owner publishes** after a claim or transfer is an
`Owner(token)` marker, and every snapshot (§7.5) carries the current token,
so a gateway that subscribes later knows it before it relays anything. A
stale delta can still slip through in the moment between a claim and its
marker; the new owner's republished step buffer is an `append: false`
replacement and overwrites it.

The pub/sub keeps nothing, so **the event log is the truth** and four rules
close the gaps:

1. **Gap detection.** `event_seq` has no holes. A subscriber that sees #43
   after #41 reads #42 from the log (`ReadEvents(S, after = 41)`) before
   relaying #43.
2. **Republish on takeover.** A new owner republishes each claimed session's
   latest event and its step buffer. That reveals any event the old owner
   committed but never published: even the last one, which no later event
   would expose. Subscribers drop repeats by `event_seq` and `chunk_seq`.
3. **Subscribe before reading.** A gateway subscribes to S *before* it asks
   for a snapshot, and drops what the snapshot already contains (§7.5), so
   nothing falls in between.
4. **Catch up in batches, never per session.** Silence on a channel is
   normal (long steps, human waits), so it triggers nothing by itself. A
   gateway catches up when its pub/sub connection breaks or a shard fails
   over: it re-subscribes and reads every affected session's missed events
   with batched `ReadEvents([(S, after)…])` calls (up to 500 sessions per
   call, answered by one indexed query), within a per-gateway budget of
   concurrent calls, backing off on storage errors, every 2 s until the
   connection is back. A slow safety check (each unfinished session silent
   for 30 s, in the same batches) catches a message the pub/sub dropped
   without a disconnect. Tokens pause; state changes are never lost.

> **Guarantee:** every committed control event reaches every live subscriber
> at least once and in order, however often ownership changes. Deltas are
> best effort and may pause or reset after a failure; the final result is
> exact.

**Product.** Any **sharded** pub/sub with cheap per-session channels: Redis 7+
sharded pub/sub (`SSUBSCRIBE` / `SPUBLISH`) or NATS core; TBase only if it
supports sharded pub/sub. Never classic Redis Cluster `PUBLISH`, which
copies every message to every node, and never a durable queue, which would
pay a replicated disk write for every token chunk and still not be the
truth. Single-node mode uses an in-process channel.

### 7.5 Reconnecting clients

A2A does not ask for frame-by-frame replay: a client that reconnects calls
`SubscribeToTask` and must get **one snapshot** — the current Task with its
status, history and every artifact so far — then live events. Everything for
the snapshot is on the owner:

| Part | Lives in | Durable? |
| --- | --- | --- |
| Status, history, interrupts, and artifacts of finished steps | The event log and step results | Yes |
| The current step's output so far | The owner's **step buffer** (backed by the dispatch gateway's copy) | No — memory |

The reconnect may land on any gateway (GW2):

1. GW2 subscribes to S's channel and holds what arrives.
2. GW2 asks the owner for `Snapshot(S)`: the durable part, the step buffer,
   and the `event_seq` and `chunk_seq` it reflects.
3. GW2 sends the PA the snapshot, then the held and live messages that come
   after those sequence numbers.

If the step ends in between, nothing is lost: the step-result event carries
the **full assembled artifact** as a replacement (`append: false`), and it is
either already in the snapshot or arrives on the channel after it.

**If the owner changed,** the new owner rebuilt the step buffer from the
dispatch gateway (`FetchBuffer`, §7.2). **If the dispatch gateway died too,**
the partial output survives at the supply agent: the dispatch's new gateway
re-subscribes to the agent's task, whose snapshot includes the artifact so
far, and the PA gets an `append: false` replacement. An agent that can't be
re-subscribed has the step re-run, and the PA sees the partial text reset —
the boundary we accept: the final result is exact; intermediate output may
reset.

Step buffers live only while their dispatch is in flight and are dropped
once the owner has recorded the step's result. Exact replay by
`Last-Event-ID` is an optional BCN extension: number the buffered chunks and
send `id: <event_seq>.<chunk_seq>`.

### 7.6 Gateway failure and drain

**A gateway crash** breaks the connections on it; the design keeps the damage
there:

- **PA streams** break; the PA reconnects through the load balancer and gets
  a snapshot (§7.5).
- **Dispatches running on it**: the owner sees the gateway dead in the roster
  (5 s) and moves each dispatch to another gateway, which runs item 3's
  reconciliation: read or re-subscribe to the agent's task by its recorded
  id, never blindly resend. A step with no queryable task is re-run, which is
  safe because side-effecting steps are dispatched only to agents with
  durable, queryable tasks (items 3 and 6).

**A pub/sub outage** stalls tokens but loses no state change: gateways
catch up from the event log in batches until channels recover (§7.4,
rule 4).

**Draining a gateway** (deploys of this tier are rare):

1. Readiness fails, so the load balancer sends no new connections; the
   roster says `draining`, so owners place no new dispatches here.
2. In-flight dispatches finish. A PA stream whose step has ended is asked to
   reconnect (HTTP/2 `GOAWAY` / `Connection: close`) and resumes on another
   gateway with a snapshot.
3. At the deadline, the gateway tells each owner that its remaining
   dispatches are abandoned, and the owners move them as after a crash.

---

## 8. Planned drain and deploy of executors

Deploy model: a group G of executors is restarted in place, one group at a
time. Executors hold no streams, so **draining one only moves leases**.

**Principle: move ownership before the process stops, and keep serving until
the move.** Draining never means refusing work.

![Executor drain timeline: stop signal; roster draining; sessions released or handed off cheapest first; leftovers transferred at the deadline; roster row deleted; restart with same address and member_name.](assets/drain-timeline.svg)

### 8.1 The sequence for executor A

1. **Stop signal:** roster `state = draining` (gateways and peers stop placing
   on A), stop claiming. Keep serving every session A still owns.
2. **Move sessions, cheapest first** (a session that just got a round jumps
   the queue):
   - **cold** (nothing in memory) → release;
   - **everything else** → hand off (§8.2), including sessions whose step is
     still streaming: the stream lives on a gateway, and its deltas and result
     go to the successor.
3. **Deadline** (25 s, inside the platform's stop timeout): transfer whatever
   is left to its successors.
4. Delete the roster row and exit. On restart: same address and
   `member_name`, new `node_id` → leftovers are claimable at once and the node
   gets its old placement back.

Successors must be outside G, or a session would move twice: a draining node
refuses a handoff and the next-ranked node is tried. If the deploy platform
can mark a whole group draining at once, double moves disappear entirely.

### 8.2 Handoff

![Handoff: A sends PREPARE with S's step buffers; B loads S and replies READY; A pauses S; A transfers the lease to B with token n+1; A sends COMMIT; B checks write_seq, drives S and publishes on S's channel; later deltas and events from gateways go to B.](assets/handoff-sequence.svg)

1. **PREPARE** — A asks successor B to load S into memory and hands it S's
   step buffers (B is not owner yet).
2. **Pause** — A finishes its current write for S and holds new events for a
   few milliseconds.
3. **Transfer** — one database update: owner = B, token + 1.
4. **COMMIT** — A tells B; B starts driving and publishing on S's channel,
   re-reading only what changed since PREPARE.
5. A forwards anything it held, deltas included; later arrivals get
   `NOT_OWNER(B)`. Dispatch gateways send their next message to B, because
   they look the owner up per message. Subscribers notice nothing: they are
   on S's channel, not on A.

The gap for S is one database write plus one network round trip: the PA's
stream pauses for milliseconds. If anything fails midway, existing rules
cover it: B dies → ordinary takeover; the COMMIT is lost → B finds S anyway;
A writes after the transfer → fenced out; A publishes after the transfer →
subscribers drop its lower token.

How B finds it: a transfer sets `adopted = FALSE` on the lease row; a claim
sets it `TRUE` in the same transaction; a new owner sets it `TRUE` (fenced)
when it starts driving. Every executor reads, each heartbeat, its leases
that are not yet adopted (`WHERE node_id = :me AND adopted = FALSE`,
indexed, usually empty), at most one batch per tick (the same 2,000-row
bound and backoff as failure discovery; state is reloaded lazily, so
adopting is cheap). Adopted rows leave the result set, so paging needs no
cursor either. There is no timestamp or cursor, so a transfer that commits
late can't be skipped: an unadopted row stays visible until it is adopted.
The same read covers transfers made without a PREPARE (drain deadline,
watchdog).

### 8.3 When is it safe to move a run mid-flight?

A run can move between any two durable writes, because its whole position is
in the database and its connections are on gateways:

| The run is… | Move now? |
| --- | --- |
| Between steps | ✓ |
| Waiting on a dispatch (any kind of stream) | ✓ — the dispatch gateway sends its deltas and result to whoever owns S |
| In the middle of a durable write or of starting a dispatch | Finish it first (milliseconds) |
| Waiting on a timer | ✓ — provided timer deadlines are stored durably |

### 8.4 Rolling out safely

- **Probes:** liveness ("is the process stuck?") stays green while draining;
  readiness is red while draining, self-fenced or starting.
- **Pacing:** add one new executor before removing one old one, wait ~30 s
  between steps so rehydration waves don't stack, and keep groups to ≤ 25 %
  of the cluster.
- **Mixed versions:** old and new executors take over each other's sessions,
  and gateways talk to both, so schema and protocol changes go expand →
  migrate → contract across releases; new durable formats ship the reader
  first and the writer later; a node that meets a format it can't read hands
  the session back instead of guessing. The lease protocol and the placement
  hash are frozen across versions.

---

## 9. Failover: when an executor crashes

There is no handoff message after a crash. Survivors must **discover** the
dead executor's sessions and split them without talking to each other.
Meanwhile client streams stay open on their gateways; output pauses until
the takeover, then continues.

![Failover: executor D stops heart-beating; once D's leases are claimable, each survivor queries D's sessions with pending work at every roster poll and claims those HRW assigns to it; idle sessions are claimed on demand by their next round.](assets/failover-discovery.svg)

1. **Roster poll finds a dead executor, then bounded, split queries.** Every
   heartbeat each executor already reads the roster. While D is dead, each
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
   round) is found too. A dead node is **retired from discovery** once its
   leases are claimable and no pending work is left in *any* bucket. An
   empty share of one's own buckets is not enough: if another survivor then
   died, nobody would query its share. So a survivor whose own query comes
   back empty checks all buckets with one indexed probe
   (`… WHERE node_id = :D AND has_pending_work LIMIT 1`) and retires D only
   if that is empty too. After that, late input for D's idle sessions
   doesn't need discovery, because the durable fallback claims a claimable
   lease itself (§6). All dead nodes together
   share one per-tick discovery budget, so a history of crashes adds no
   standing load. A node holding 300 k pending sessions spread over 19
   survivors is claimed in under 10 s; claiming is cheap because state is
   reloaded lazily on first use.
2. **Claim on demand.** Idle sessions are not scanned at all: the next round
   (or a gateway asking for a snapshot) finds the owner dead and the
   preferred survivor claims it — a normal cold start.
3. **Safety-net sweep** every 30–60 s for anything missed.

After claiming: reload state from the database (exactly like resuming a cold
session — *failover is a cache miss*), check each in-flight dispatch's
gateway in the roster (a live one hands over the step's output with
`FetchBuffer` and sends further deltas to the new owner; a dead one means
the dispatch is moved, §7.6), republish the session's latest event and step
buffer (§7.4, rule 2), and resume.

**Taking over earlier on evidence.** Because fencing makes a wrong guess
safe, evidence can replace waiting: a restart at the same address frees the
old run's leases at once; a process that panics marks its own roster row
dead on the way out (heartbeat set to NULL) and keeps its leases, so
survivors find them through the normal dead-node query (§9, step 1) at once —
pending work is never released. A node that is alive but silently unreachable gives no evidence —
for it, wait the claim threshold.

| Situation | PA's stream | Sessions resume after |
| --- | --- | --- |
| Executor restart in place | Pauses, then continues | restart time + ≤ 1 s |
| Executor crash, preferred survivor claims | Pauses, then continues; no output lost | ~6–7 s (5 s claim threshold + ≤ 1 s roster poll + lazy reload) |
| Executor crash, worst case (second choice claims) | Pauses, then continues | ~8 s (adds the 1 s non-preferred grace), assuming survivors are past their own recovery grace |
| Gateway crash | Breaks; the PA reconnects and gets a snapshot | Dispatches moved after 5 s, then reconciled (§7.6) |

These come from a 1 s heartbeat and a 5 s claim threshold. Going lower is
possible and still safe (fencing makes a wrong takeover harmless), but each
false takeover of a node that merely stalled for a few seconds costs a
rehydration. Cluster-wide stalls are already absorbed by relative staleness
and the recovery grace.

---

## 10. Episodes: warm state is only a cache

- An episode starts when an executor claims or receives S (loading lazily)
  and ends on release, lease loss, or 10 minutes idle.
- **Write-through only.** Every durable fact is written before it is
  acknowledged; memory never holds the only copy. That is why idle eviction,
  resuming a months-old task, and crash failover are the same operation:
  reload from the database. (Step buffers are the one exception: soft, and
  backed by the dispatch gateway's copy and the agent's own task, §7.5.)
- **Rebalancing touches only cold sessions.** When an executor is no longer
  the preferred one for a session that has gone cold, it releases the lease
  and the preferred executor claims it on the next round — free, so it never
  costs TTFT.
- A **stuck-session watchdog** (~5 min without progress) transfers a session
  whose driver is wedged on an otherwise healthy executor; a stuck-dispatch
  watchdog (no progress count from the gateway) moves a wedged dispatch.

---

## 11. Parameters

All configurable; defaults sized for a crash takeover in under ~10 s.

| Parameter | Default |
| --- | --- |
| Heartbeat interval, every node (also keeps leases alive) | 1 s |
| Placement-stale threshold | 3 s |
| Claim threshold; a gateway counts as dead after | 5 s |
| Observer recovery grace | 5 s (= claim threshold) |
| Self-fence margin | 1 s |
| Claim grace for non-preferred nodes | 1 s |
| Failure discovery: batch per poll / poll interval while backlog remains | 2,000 rows / 1 s |
| Safety-net sweep | 30–60 s |
| Executor drain deadline | 25 s |
| Gateway–executor channel keepalive / dead after | 1 s / 3 s |
| Delta batch interval (dispatch gateway → owner) | 200 ms |
| Catch-up while a pub/sub connection is down: interval / sessions per call | 2 s / 500 |
| Safety check for an unfinished, silent session | 30 s |
| Dispatch progress count | every 5 s |
| Gateway drain deadline | sized to the platform's stop timeout (e.g. 60 s) |
| Episode idle eviction | 10 min |
| Stuck-session watchdog | ~5 min |

---

## 12. Contracts

**Tables** (names pinned for sibling items):

- `bcs_node_roster`: `node_id`, `member_name`, `role` (`executor` |
  `gateway`), `state`, `started_at`, `last_heartbeat_at`, `advertise_addr`,
  `build_version`.
- `bcs_task_leases`: `session_id`, `bucket`, `node_id`, `fencing_token`,
  `expires_at`, `adopted`, `write_seq`, `has_pending_work`, `pending_seq`.
  Indexes: `(node_id, has_pending_work, bucket)` for failure discovery,
  `(node_id, adopted)` for leases transferred but not yet adopted.
- **Session event log** (logical): `session_id`, `event_seq`, event, written
  only in fenced commits; kept until the Task ends plus item 4's retention
  floor. Its physical home is open (§14).
- **Dispatch location**: the dispatch record (item 3) names the gateway that
  runs it, written fenced.

**Interfaces** (protocol-neutral, each with a database implementation, a
single-node implementation and a no-op one):

- **Roster** — register, heartbeat, set draining, read the live roster.
- **Placement policy** — rank executors for a session (rendezvous by
  default); choose a gateway for a dispatch (the caller's, else the least
  loaded).
- **Task leases** — claim, release, transfer, list owned sessions, and a
  **fence step** other components put first in their transactions (item 3's
  dispatch records use it).
- **Gateway–executor protocol**:
  - gateway → executor: `Submit(round)`, `Snapshot(S)`,
    `ReadEvents(S, after_seq)`, `DispatchDelta(D, chunk_seq, chunk)`
    (unacknowledged), `DispatchEvent(D, seq, event)`,
    `DispatchResult(D, seq, output)`, `Progress(D)`, `Abandoned(D)`;
  - executor → gateway: `Accepted(Task)`, `NOT_OWNER(X)`,
    `StartDispatch(D, target, request)`, `CancelDispatch(D)`, `Ack(D, seq)`,
    `FetchBuffer(D)`.
- **Session channel** (owner → subscribers, on the pub/sub):
  `Event(event_seq, token, event)` and `Delta(D, chunk_seq, token, chunk)`;
  with a pub/sub implementation, an in-process one for single-node mode and a
  no-op.

Single-node mode runs both tiers in one process and the same lease logic on
its local database, so the takeover path is exercised everywhere.

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
| D8 | Any node serves any request; the owning node runs its own dial-out streams; dial-in sockets land on any node (planes 1 and 3) | A gateway tier holds every long-lived connection; executors hold none; every supply agent is dial-out | An executor crash or deploy pauses streams instead of closing them, and executor drain becomes moving leases |
| D9 | — | The owner fans out on a per-session channel of a non-durable pub/sub; a durable per-session event log is the truth | Fan-out stays BCN's logic; owner changes are invisible to subscribers; no durable queue on the token path |

---

## 14. Open decisions

| # | Decision | Recommendation |
| --- | --- | --- |
| 1 | Who builds the gateway–executor channel and the gateway tier (here and in item 2) | Item 1 builds the channel and the executor side; item 2 builds the gateway |
| 2 | TTFT budget BCN may add at p99 | Decides whether load-balancer header hashing is needed early |
| 3 | Node-to-node authentication | mTLS if available; else a shared secret on an internal-only port |
| 4 | Deploy platform: stop timeout; group-level pre-stop hook? | Sizes both drain deadlines; avoids double moves |
| 5 | Timer durability | Every run timer must be stored durably |
| 6 | Physical home of the session event log | Extend item 3's delivery ledger rather than add an overlapping table |
| 7 | Gateway and executor as separate deployments, or roles of one | Separate deployments from one codebase: gateways deploy rarely, executors often |
| 8 | Exact replay by `Last-Event-ID` | Optional extension once the step buffer exists; snapshots are enough for A2A |
| 9 | Pub/sub product for session channels | Redis 7+ sharded pub/sub or NATS core; TBase only if it supports sharded pub/sub |
| 10 | Delta transport from dispatch gateway to owner if long-lived internal channels are not allowed | A per-dispatch channel on the same pub/sub, subscribed by the owner |

---

## 15. Alternatives we rejected

| Alternative | Why not |
| --- | --- |
| Consistent hashing as the correctness mechanism | Breaks when nodes disagree on membership |
| Active/standby | Caps throughput at one node; deploys become outages |
| Sticky sessions at the load balancer | Ties placement to infrastructure; useless for background work |
| etcd / ZooKeeper / gossip for membership | Not needed; the lease already lives next to the data |
| A lock service (etcd, ZooKeeper, Redlock) for ownership | The fencing check must run in the same transaction as the data it protects; Redlock has no fencing token at all |
| The owning executor holds its own streams (one node type) | Every executor crash or deploy cuts streams; drain must wait for non-resumable steps |
| Fan-out decided or performed in the gateway | Fan-out is BCN's logic: group membership, mentions, visibility, which member's stream is shown |
| The owner tracks its subscribers itself (owner as broker) | Subscribers would have to follow every owner change; a pub/sub makes owner changes invisible, and is a high-throughput fan-out we need not build |
| A durable queue for fan-out | A replicated disk write per token chunk, per-session topics don't scale, and it would still not be the truth |
| A message queue on the request path (e.g. a consistent-hash exchange) | A third membership view, lost ordering on rebalance, a second lease mechanism, and it cannot join the lease-row transaction the durable fallback relies on |
| Every input and token through a cache's streams (queue-centric executor) | Storage operations grow with tokens, a queue hop before every round, and acknowledged-message durability rests on the cache's failover |
| Redis as the system of record | Asynchronous replication can lose acknowledged writes and reissue a token |
| Per-session lease renewal | O(sessions) writes per tick |
| Polling for new rounds | Adds up to 1 s to TTFT |

The only pub/sub on the hot path is the non-durable session channel (§7.4).
A durable queue still fits **off** the hot path: outbound webhook push to
PAs (long retries, dead letters) and feeds for audit or analytics,
preferably as a change stream from the database.

---

## 16. References

- M. Kleppmann, "How to do distributed locking" (2016) — fencing tokens
- M. Burrows, "The Chubby lock service" (OSDI 2006) — sessions, grace periods
- D. Thaler, C. Ravishankar, "Using name-based mappings to increase hit rates" (1998) — rendezvous hashing
- V. Mirrokni et al., "Consistent Hashing with Bounded Loads" (2017)
- J. Saltzer, D. Reed, D. Clark, "End-to-end arguments in system design" (1984)
- A2A specification — <https://github.com/a2aproject/A2A>
