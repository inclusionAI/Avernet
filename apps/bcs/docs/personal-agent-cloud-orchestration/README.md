# BCN Cloud Orchestration for Personal Agents

- Date: 2026-10-31
- Status: **Draft for team review**
- Scope: the **Cloud track** of the BCN ↔ personal-agent integration — BCN runs
  multi-agent workflows server-side on behalf of a personal agent, with high
  availability. The sibling **Edge track** (BCN CLI executing workflow YAML
  locally against shared storage) is referenced where the two tracks share
  contracts, but is not designed here.
- Audience: this doc is the 10-minute onboarding. Each hard problem has its own
  deep dive:
  - [01 — Interface and state model](01-interface-and-state-model.md)
  - [02 — Availability architecture](02-availability-architecture.md)
  - [03 — Execution correctness](03-execution-correctness.md)
  - [04 — Supply-agent compliance](04-supply-agent-compliance.md)

## 1. Context

A personal agent takes long-lived requests from its user ("schedule 3 travels
for me this year and send me the plan 2 months before each") and delegates the
multi-expert parts to BCN: finding supply agents (flight booking, hotels,
tickets), selecting or creating a workflow, and running it to completion.

A **task** here is the personal agent's long-running unit of work, identified
by `(user_id, task_id)`. Over its lifetime (possibly months) the personal agent
returns to BCN many times for the same task — including rapid multi-round
refinement while the user is actively chatting ("make it 5 days and find a
cheaper hotel").

Working assumption (**TBD, to be confirmed with the personal-agent team**):
**the personal agent owns the clock**. BCN acts when called and never needs to
wake itself up months later. If any use case requires BCN-side timers or
continuous autonomous work (e.g. "watch prices all year and act within
minutes"), a durable-timer design must be added; nothing below precludes it,
but it is deliberately out of scope until confirmed.

## 2. The problems — stated at full strength

The cloud approach is contested, and for good reasons. The design must answer
each of these, not argue around them:

1. **Availability coupling.** Cloud execution puts BCN nodes, the shared
   database, and (today) BCSFuse in the critical path of a personal agent's
   task. A design where a server-node failure fails or loses a user's task is
   unacceptable; "retry from scratch" is unacceptable for tasks with side
   effects already performed.
2. **Non-idempotent side effects.** Supply agents book hotels and buy tickets.
   Any retry, failover, or redelivery can double-execute a purchase unless
   idempotency is engineered end-to-end — and the current A2A call path does
   not yet carry an idempotency key.
3. **Privacy and retention.** Server-side execution persists task content and
   intermediate results. For a *personal* agent this is a product-level
   concern: what does the cloud hold, for how long, and who controls it?
4. **Operational cost.** Multi-node BCS + HA storage + failure detection, for
   a workload that is low-rate and naturally partitioned per user. The Edge
   track's counter-argument — "the client is already a durable executor" —
   deserves a straight answer.
5. **Heterogeneous supply agents.** Any correctness contract we need from
   supply agents must be implemented across four in-house integration paths
   and an open ecosystem of external providers we do not control.

Why Cloud at all, then: (a) tasks that outlive the device being online —
long workflows, A2A callbacks, work shared across the user's devices; (b) one
recovery implementation instead of one per client platform; (c) server-side
enforcement (friendship/visibility, outbound-URL policy, congestion control)
that a purely local executor can bypass. The Edge track remains the degraded
path when the cloud control plane is unreachable.

## 3. The approach — one page

**Interface.** BCN is a server the personal agent calls (A2A 1.0 JSON-RPC +
SSE, resumable Tasks). The months-long *logical* session is carried by durable
identifiers, never by connections. No standing client connection exists, so
client connectivity is out of the HA problem entirely. → [Doc 01](01-interface-and-state-model.md)

**State model — three lifetimes.**

| Unit | Lifetime | What it scopes |
| --- | --- | --- |
| **Run** | minutes–hours | one workflow execution: checkpoints, dispatches, recovery |
| **Episode** | minutes–hours | a burst of active interaction; the only time state is *warm* (hydrated in memory on one node) |
| **Task session** | task lifetime | the durable thread of record and collaboration workspace, in the shared DB |

Durable state is written through to the shared database on every round
(write-before-ack); node-local state is strictly a rebuildable cache. GC of an
idle episode and failover after a node death are the same operation:
**failover is a cache miss.** → [Doc 01](01-interface-and-state-model.md)

**Placement and ownership.** `hash(user_id, task_id)` routes rounds of the
same task to the same node for intra-episode cache affinity (a latency
optimization). Correctness — exactly one node drives a task at a time — comes
from **fenced, expiring leases in the shared DB**, not from ring agreement.
Hash = placement; lease = ownership. → [Doc 02](02-availability-architecture.md)

**Execution correctness.** Every outbound dispatch is written ahead with a
client-generated idempotency key `(run_id, step_id, attempt)` before the
request leaves. Recovery is pull-based: the supply agent holds queryable Task
state (A2A `GetTask`), so a takeover node resolves "dispatched, outcome
unknown" by reading, not re-sending. The guarantees ride a named **BCN A2A
profile** (idempotency keys, dedup, probe semantics, retention floors) built on
A2A's extension mechanism. This extension work is **in this project's scope**
(it builds on, but is not delivered by, the in-flight engine A2A migration).
→ [Doc 03](03-execution-correctness.md)

**Supply agents.** Four in-house adapter implementations cover every in-house
agent (bcs-bridge; the OpenClaw and DeepSeek Harness channel plugins; the
engine adapter). External providers are onboarded by **capability tiering**:
non-compliant agents still serve read-only/idempotent steps; side-effecting
steps require verified profile compliance. BCN records, verifies, and enforces
the tier at dispatch time. We support both in-house and external providers at
launch. → [Doc 04](04-supply-agent-compliance.md)

**Availability.** Four planes — API (any node serves any request), executor
(leases + takeover), connection (node-pinned agent sockets + a
connection-location registry), storage (managed MySQL/Redis HA; BCSFuse out of
the execution critical path). Headline invariant: **a node failure never fails
or loses a task.** → [Doc 02](02-availability-architecture.md)

## 4. What exists vs. what is net-new

BCS already provides, battle-tested on one node: durable run state with
write-before-dispatch, an immutable per-run workflow snapshot, restart
recovery, a persistent delivery queue with per-session FIFO and congestion
control, MySQL/Redis plugins, and a `LeaderElectionPort` seam.

Net-new for this project: node identity/membership and failure detection;
hash-based placement; fenced task leases and takeover; the connection-location
registry; durable (instead of process-local) idempotency/dedup state; the BCN
A2A profile and its conformance suite; capability tiering and dispatch gating;
session lifecycle controls on the personal-agent-facing API.

## 5. Open questions

| # | Question | Status |
| --- | --- | --- |
| 1 | Clock ownership: does the personal agent own all timers? | **TBD** — assumed yes; owner: personal-agent team sync |
| 2 | Takeover RTO target (drives heartbeat/lease sizing) | open — proposal in Doc 02 |
| 3 | Retention defaults for task sessions (L2 vs L3 clocks) | open — proposal in Doc 01 |
| 4 | Workflow search path: can BCSFuse results be cached so it never blocks execution? | open |
