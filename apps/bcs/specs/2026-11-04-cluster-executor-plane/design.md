# Cluster Executor Plane — Design

[简体中文](design.zh-CN.md)

- Date: 2026-11-04 · Revised 2026-11-05 (gateway tier) · Status: design
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
   session must not cut the token stream the personal agent is reading.

The whole design rests on one sentence:

> **Hash = fast guess. Lease = truth. Fencing token checked by storage = safety.**

Nothing about correctness depends on nodes agreeing on membership, on clocks,
or on the load balancer.

---

## 2. The big picture

![Overview: the PA reaches a gateway through the load balancer; the gateway holds every long-lived connection (PA streams, streams to supply agents, bot sockets) and relays tokens itself; it sends rounds and control events to the session's owner executor over a direct control channel; executors hold leases and write the shared database.](assets/overview.svg)

BCN has **two tiers**, which can ship as two deployments of one codebase:

| Tier | Holds | Does | Durable state |
| --- | --- | --- | --- |
| **Gateway** | Every long-lived connection: PA streams, streams to supply agents, dial-in bot sockets | Terminates connections, relays tokens, runs dispatches, buffers the current step's output | None |
| **Executor** | No long-lived connections | Owns sessions through leases, drives runs and orchestration, writes the database | Everything, through fenced writes |

Splitting them is what makes requirement 5 cheap: tokens go from a supply
agent's stream to the PA's stream **inside a gateway** and never touch an
executor, so an executor can crash or restart without cutting a stream. It
also turns an executor deploy into moving leases (§8).

| Mechanism | Answers | Wrong answer costs |
| --- | --- | --- |
| **Roster** — one row per node, with a heartbeat | Which gateways and executors are alive? | Latency only |
| **Placement** — rendezvous hashing over the executors | Which executor *should* drive session S? | One extra hop or one rehydration |
| **Lease + fencing token** — one row per session | Which executor *does* drive S? | — this is the correctness mechanism |
| **Routing** — forward to the owner, redirect on mismatch | How does a round reach the owner fast? | Falls back to a durable write |
| **Event log + subscriber cursor** | How does every stream see every state change, across owner changes? | Seconds of delay, never a lost event |

Three kinds of traffic:

- **PA → load balancer → gateway**: the PA's own connection (SSE, WebSocket
  or gRPC stream). The load balancer only spreads connections over ready
  gateways.
- **Gateway ↔ executor**: one long-lived, multiplexed control channel per
  pair, straight to the peer's advertised address (no load balancer). It
  carries rounds, control events and dispatch commands — **never tokens**.
- **Gateway → supply agents**: outbound A2A or Provider streams, plus
  dial-in bot sockets that arrive through the load balancer.

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
| Subscriber | A gateway relaying S's events to a client stream |
| Delta | A piece of output text or artifact (a "token" chunk); stays in the gateway |
| Control event | Anything that changes what should happen next; goes to the owner (§7.3) |
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

Once the round is accepted, the entry gateway becomes a **subscriber** of S
(§7.4) and relays S's events and the step's tokens to the PA. Nothing here
depends on the load balancer: it needs only unique node ids, the shared
database, direct peer addresses, and the same hash on every node. A balancer
that hashes on a session header just makes forwarding rarer.

---

## 7. The gateway tier: streams and dispatch

### 7.1 Connections

![One round end to end: PA's SSE reaches GW1 through the load balancer; GW1 submits the round to owner E3 over the control channel; E3 places the dispatch on GW1; GW1 streams from the supply agent, relays deltas straight into the PA's SSE, and sends control events and the result to E3; E3 records them fenced and sends Task events back to GW1.](assets/round-flow.svg)

| Leg | Protocol | Notes |
| --- | --- | --- |
| PA → load balancer → gateway | The PA's SSE, WebSocket or gRPC stream | An L4 balancer passes the connection through. An L7 balancer must not buffer responses and must allow idle gaps longer than the gateway's SSE heartbeat (`: ping` every 15–30 s) |
| Gateway ↔ executor | One long-lived, multiplexed, bidirectional channel per pair (e.g. gRPC over HTTP/2) | Carries rounds, control events, dispatch commands and results for thousands of sessions; keepalive pings detect a dead peer in seconds. Not SSE: it must go both ways and share flow control |
| Gateway → supply agent | A2A `SendStreamingMessage` / `SubscribeToTask`, or a Provider stream | One per dispatch, opened by the dispatch gateway |
| Bot → load balancer → gateway | Dial-in WebSocket | The socket is transport only (§7.6) |

### 7.2 Dispatch placement and results

When the owner starts a step, it chooses where the dispatch runs:

- **The entry gateway of the caller's stream**, by default. Then tokens go
  from the supply agent to the PA inside one process.
- **Any healthy gateway** when there is no caller stream (group members,
  background steps, a PA that disconnected). A subscriber on another gateway
  then dials the dispatch gateway for its deltas: one gateway-to-gateway hop.

The owner records **which gateway runs the dispatch** in the dispatch record,
inside its fenced write (item 3 owns the record). That fact must be durable:
a new owner needs it to supervise, cancel or collect the dispatch after a
takeover.

The dispatch gateway reports back to the owner, never to a fixed node:

- **Control events and the final result** go to S's current owner, looked
  up per message (hint cache + `NOT_OWNER`), each with a per-dispatch
  sequence number, and are **retried until the owner acknowledges** — an
  acknowledgement means the owner recorded it fenced. The owner drops
  repeats by `(dispatch, seq)`. If the owner changes mid-step, delivery just
  follows the lease.
- **Progress counts** every few seconds (no tokens) feed the stuck-dispatch
  watchdog.

If the dispatch gateway dies before the result is acknowledged, the owner
moves the dispatch (§7.6); the result is recovered from the agent's task, or
the step is re-run.

### 7.3 What a stream carries

An A2A stream carries four kinds of event: `Task` (the agent created a task
and names its id), `TaskStatusUpdateEvent` (the state changed:
`working`, `input-required`, `completed`…), `TaskArtifactUpdateEvent`
(a chunk of output, `append: true`) and `Message` (agents that reply without
a task). The dispatch gateway sorts each one by **who needs it**:

| Event | Example | Goes to | Why |
| --- | --- | --- | --- |
| Agent's `Task` | `taskId: ta-77` | **Owner** | Recorded on the dispatch, so the task can be found again after any crash |
| Artifact chunks (deltas) | "I found two options: …" | **Subscribers only**, plus the step buffer | Display only; the owner receives the assembled artifact at the end |
| `working` + progress text | "Searching flights…" | Subscribers; owner gets a liveness tick | Nothing to decide |
| `input-required` / `auth-required` | "Which one should I book?" | **Owner** | Changes the Task's state, starts the human-wait timer, routes the PA's answer back to `ta-77` |
| `completed` / `failed` | `final: true` | **Owner**, with the assembled output | The step is done; the owner decides what happens next |
| Bot-protocol coordination calls | `tool_call_end` of a coordination tool | **Owner** | BCS acts on it mid-run |

The PA sees **the owner's Task**, not the agent's. A downstream `completed`
ends one *step*: for a single agent the owner then completes its own Task,
but in a group session it starts the next member's turn and the PA's Task
stays `working`. So status and final events reach the PA from the owner
(§7.4), while deltas go straight through, with ids rewritten to the owner's
Task. **Rule of thumb: text is for the PA; anything that changes what should
happen next goes to the owner.** The event classifier is part of the
gateway–executor contract.

A subscriber writes an owner event that ends a step only after it has relayed
that step's last chunk, so the PA never sees "completed" before the text.

### 7.4 Subscriptions: every stream sees every state change

![Subscriptions: control events are written in the owner's fenced commit with a per-session event_seq; each subscriber keeps last_seq; when the owner crashes, the subscriber notices (channel break, roster, periodic re-assertion), re-subscribes to the new owner with last_seq, and the new owner replays from the log.](assets/subscriptions.svg)

The owner cannot guarantee it reaches every subscriber — it can crash right
after a commit, and a new owner does not know who was listening. So the
guarantee is built the other way round:

1. **Control events are durable.** The events the PA must see (state changes,
   interrupts, each step's final output, the Task's end) are written in the
   owner's fenced commit, numbered by a per-session `event_seq`. Publishing
   happens after the commit, so a crash in between loses nothing.
2. **Subscribers keep a cursor.** Each subscriber remembers the last
   `event_seq` it relayed, and always subscribes with it:
   `Subscribe(S, last_seq)`. The owner replays everything after `last_seq`
   from the log, then continues live. Subscribers drop repeats by
   `event_seq`.
3. **Subscribers re-sync themselves.** A gateway does not wait for a call of
   its own to fail. It re-subscribes when:
   - its channel to the owner breaks (immediately on a crash; within ~3 s on
     a dead host, through keepalive pings);
   - the owner goes stale in the roster;
   - **periodic re-assertion** (every 5 s): one batched message per
     gateway–executor pair, "I serve sessions […] at seq […]", answered with
     missed events or `NOT_OWNER(X)`. This catches silent cases such as a
     lease that moved while the gateway's view was stale.

   It finds the new owner the same way a round does (§6). A subscribe that
   reaches the preferred survivor of a claimable lease claims it on demand,
   so a waiting subscriber speeds takeover up.
4. **Planned handoff sends `MOVED`.** When an owner transfers a lease (§8), it
   tells every subscriber `MOVED(successor)`, and they re-subscribe at once.

> **Guarantee:** every committed control event reaches every live subscriber
> at least once and in order, however often ownership changes. The delay is
> bounded by takeover (~6–8 s after a crash, near zero on a planned handoff).

Subscribers are soft state, rebuilt by the gateways; only the event log and
the dispatch location are durable.

### 7.5 Reconnecting clients

A2A does not ask for frame-by-frame replay: a client that reconnects calls
`SubscribeToTask` and must get **one snapshot** — the current Task with its
status, history and every artifact so far — then live events. The snapshot is
assembled from two places:

| Part | Lives in | Durable? |
| --- | --- | --- |
| Status, history, interrupts, and artifacts of finished steps | The owner's event log and step results | Yes |
| The current step's output so far | The dispatch gateway's **step buffer** | No — memory |

The reconnect may land on any gateway (GW2):

1. GW2 asks the owner for the snapshot. It returns the durable part, the
   current `event_seq`, and "dispatch D runs on GW1".
2. GW2 asks GW1 for D's buffer from the start. GW1 sends the buffer and
   attaches GW2 as a live subscriber **in one step**, so no chunk falls in
   between.
3. GW2 sends the PA one snapshot, then live deltas from GW1 and control
   events from the owner (from `event_seq`).

**If GW1 died too,** the partial output survives at the supply agent: the
dispatch's new gateway re-subscribes to the agent's task, whose snapshot
includes the artifact so far, and the PA gets an `append: false`
replacement. An agent that can't be re-subscribed has the step re-run, and
the PA sees the partial text reset — the boundary we accept: the final
result is exact; intermediate output may reset.

A step buffer lives only while its dispatch is in flight and is dropped once
the owner has recorded the step's result (from then on the artifact is
durable). Exact replay by `Last-Event-ID` is an optional BCN extension: number
the buffered chunks and send `id: <event_seq>.<chunk_seq>`.

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
- **Bot sockets** drop; bots redial through the load balancer.

**Bot sockets.** A dial-in bot keeps one WebSocket to the gateway it reached.
That gateway is **transport only**: it forwards the bot's events to the
current owner of each run's session, looked up per event. Handing a session
off never touches the socket. "Which gateway holds bot X's socket?" is a
cluster-wide soft-state registry (item 2).

![Bot socket roles: before handoff the gateway holding the socket forwards events to owner A; after handoff it forwards each event to the new owner B; the bot sees nothing.](assets/bot-socket-roles.svg)

**Draining a gateway** (deploys of this tier are rare):

1. Readiness fails, so the load balancer sends no new connections; the
   roster says `draining`, so owners place no new dispatches here.
2. In-flight dispatches finish. A PA stream whose step has ended is asked to
   reconnect (HTTP/2 `GOAWAY` / `Connection: close`) and resumes on another
   gateway with a snapshot.
3. Bot sockets carry no new work; once their runs end, the gateway sends a
   "please reconnect" control frame and the bot redials elsewhere.
4. At the deadline, the gateway tells each owner that its remaining
   dispatches are abandoned, and the owners move them as after a crash.

---

## 8. Planned drain and deploy of executors

Deploy model: a group G of executors is restarted in place, one group at a
time. Executors hold no streams, so **draining one only moves leases**.

**Principle: move ownership before the process stops, and keep serving until
the move.** Draining never means refusing work.

![Executor drain timeline: stop signal; roster draining; sessions released or handed off cheapest first while subscribers are told MOVED; leftovers transferred at the deadline; roster row deleted; restart with same address and member_name.](assets/drain-timeline.svg)

### 8.1 The sequence for executor A

1. **Stop signal:** roster `state = draining` (gateways and peers stop placing
   on A), stop claiming. Keep serving every session A still owns.
2. **Move sessions, cheapest first** (a session that just got a round jumps
   the queue):
   - **cold** (nothing in memory) → release;
   - **everything else** → hand off (§8.2), including sessions whose step is
     still streaming: the stream lives on a gateway, and its result is
     delivered to the successor.
3. **Deadline** (25 s, inside the platform's stop timeout): transfer whatever
   is left to its successors.
4. Delete the roster row and exit. On restart: same address and
   `member_name`, new `node_id` → leftovers are claimable at once and the node
   gets its old placement back.

Successors must be outside G, or a session would move twice: a draining node
refuses a handoff and the next-ranked node is tried. If the deploy platform
can mark a whole group draining at once, double moves disappear entirely.

### 8.2 Handoff

![Handoff: A sends PREPARE; B loads S and replies READY; A pauses writes for S; A transfers the lease to B with token n+1; A sends COMMIT to B and MOVED to S's subscribers; B checks write_seq and drives S; later events from gateways go to B.](assets/handoff-sequence.svg)

1. **PREPARE** — A asks successor B to load S into memory (B is not owner yet).
2. **Pause** — A finishes its current write for S and holds new events for a
   few milliseconds.
3. **Transfer** — one database update: owner = B, token + 1.
4. **COMMIT** — A tells B; B starts driving, re-reading only what changed
   since PREPARE. A tells S's subscribers `MOVED(B)` (§7.4).
5. A forwards anything it held; later arrivals get `NOT_OWNER(B)`. Dispatch
   gateways deliver the next control event to B, because they look the owner
   up per message.

The gap for S is one database write plus one network round trip, and the
PA's stream does not notice. If anything fails midway, existing rules cover
it: B dies → ordinary takeover; the COMMIT is lost → B finds S anyway; a
`MOVED` is lost → periodic re-assertion; A writes after the transfer →
fenced out.

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
| Waiting on a dispatch (any kind of stream) | ✓ — the dispatch gateway delivers the result to whoever owns S |
| Waiting on a dial-in bot | ✓ — the socket's gateway forwards events to whoever owns S |
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
Meanwhile streams keep flowing: they live on gateways, and only state
progress waits for the takeover.

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
   (or a re-subscribing gateway) finds the owner dead and the preferred
   survivor claims it — a normal cold start.
3. **Safety-net sweep** every 30–60 s for anything missed.

After claiming: reload state from the database (exactly like resuming a cold
session — *failover is a cache miss*), check each in-flight dispatch's
gateway in the roster (a live gateway simply delivers to the new owner; a
dead one means the dispatch is moved, §7.6), and resume. Gateways
re-subscribe on their own (§7.4).

**Taking over earlier on evidence.** Because fencing makes a wrong guess
safe, evidence can replace waiting: a restart at the same address frees the
old run's leases at once; a process that panics marks its own roster row
dead on the way out (heartbeat set to NULL) and keeps its leases, so
survivors find them through the normal dead-node query (§9, step 1) at once —
pending work is never released. A node that is alive but silently unreachable gives no evidence —
for it, wait the claim threshold.

| Situation | PA's stream | Sessions resume after |
| --- | --- | --- |
| Executor restart in place | Keeps flowing | restart time + ≤ 1 s |
| Executor crash, preferred survivor claims | Keeps flowing | ~6–7 s (5 s claim threshold + ≤ 1 s roster poll + lazy reload) |
| Executor crash, worst case (second choice claims) | Keeps flowing | ~8 s (adds the 1 s non-preferred grace), assuming survivors are past their own recovery grace |
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
  reload from the database. (The gateway's step buffer is the one exception,
  and it is backed by the agent's own task, §7.5.)
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
| Subscription re-assertion | 5 s |
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
  - gateway → executor: `Submit(round)`, `Subscribe(S, last_seq)`,
    `Reassert([S, seq]…)`, `DispatchEvent(D, seq, event)`,
    `DispatchResult(D, seq, output)`, `Progress(D)`, `Abandoned(D)`;
  - executor → gateway: `Accepted(Task)`, `Event(S, event_seq, event)`,
    `NOT_OWNER(X)`, `MOVED(X)`, `StartDispatch(D, target, request)`,
    `CancelDispatch(D)`, `Ack(D, seq)`;
  - gateway ↔ gateway: `AttachBuffer(D)` for a subscriber on another gateway.

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
| D8 | Any node serves any request; the owning node runs its own dial-out streams; dial-in sockets land on any node (planes 1 and 3) | A gateway tier holds every long-lived connection; executors hold none | An executor crash or deploy no longer cuts streams, tokens never cross an executor, and executor drain becomes moving leases |
| D9 | — | Control events go to subscribers from a durable per-session log, read by cursor | Every stream sees every state change across owner changes, with no queue |

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
| A message queue on the request path (e.g. a consistent-hash exchange) | A third membership view, lost ordering on rebalance, a second lease mechanism, and it cannot join the lease-row transaction the durable fallback relies on |
| Every input and token through a cache's streams (queue-centric executor) | Storage operations grow with tokens, a queue hop before every round, and acknowledged-message durability rests on the cache's failover |
| Redis as the system of record | Asynchronous replication can lose acknowledged writes and reissue a token |
| Per-session lease renewal | O(sessions) writes per tick |
| Polling for new rounds | Adds up to 1 s to TTFT |

A queue still fits **off** the hot path: outbound webhook push to PAs (long
retries, dead letters) and feeds for audit or analytics, preferably as a
change stream from the database.

---

## 16. References

- M. Kleppmann, "How to do distributed locking" (2016) — fencing tokens
- M. Burrows, "The Chubby lock service" (OSDI 2006) — sessions, grace periods
- D. Thaler, C. Ravishankar, "Using name-based mappings to increase hit rates" (1998) — rendezvous hashing
- V. Mirrokni et al., "Consistent Hashing with Bounded Loads" (2017)
- J. Saltzer, D. Reed, D. Clark, "End-to-end arguments in system design" (1984)
- A2A specification — <https://github.com/a2aproject/A2A>
