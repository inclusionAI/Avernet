# 01 — Interface and State Model

- Parent: [BCN Cloud Orchestration for Personal Agents](README.md)
- Status: Draft for team review

## Problem

What is the contract between a personal agent and BCN for a task that can span
months, and what state does BCN keep for it? Two risks pull in opposite
directions:

- Too stateless, and interactive refinement breaks: in one sitting a user
  iterates ("make it 5 days, cheaper hotel"), and round N+1 must resolve
  references to round N's output, while the supply agents should *revise their
  prior work* (the hotel expert's candidate list, held quotes) rather than be
  re-briefed cold.
- Too stateful, and the cloud accumulates months of personal context — the
  strongest privacy objection to the cloud approach — plus a months-long warm
  working set that complicates GC and HA.

## Decision 1 — BCN is a server the personal agent calls

Two postures are possible with today's building blocks:

- **A — request/response**: the personal agent (owning the clock) calls BCN;
  results are retrieved asynchronously. Exists today as async chat + run query
  and the OpenAPI V1 collaboration surface.
- **B — peer bot**: the personal agent registers as a bot and BCN pushes to it
  over a standing WebSocket or webhook.

We choose **A**, aligned with the in-flight A2A migration: the personal agent
speaks **A2A 1.0 (JSON-RPC over HTTP + SSE)** to BCN. `SendMessage` opens or
continues a task; `SubscribeToTask` streams a round; `GetTask` re-attaches
after any gap. The months-long *logical* session is carried by durable
identifiers (`task_id`, session id, A2A `contextId`) — never by a connection.

Consequences:

- A BCN round can itself run minutes–hours, so result retrieval must be
  resumable (re-subscribe with last-seen sequence, or poll) — never dependent
  on one connection surviving.
- The personal agent holds no standing socket BCN must track. Client
  connectivity drops out of the HA problem (Doc 02); the only
  connection-shaped clients left are supply agents, which already have
  reconnect machinery.
- Push notification to the personal agent (A2A push config) is an optional
  latency optimization; correctness never depends on it.
- **TBD (clock ownership):** if any use case requires BCN-side timers
  (continuous monitoring, scheduled resumption without a caller), posture A
  still works but a durable-timer store must be added. Out of scope until the
  personal-agent team confirms.

## Decision 2 — three conversation layers, with projection boundaries

| Layer | Conversation | Owner | BCN sees it? |
| --- | --- | --- | --- |
| **L1** | user ↔ personal agent | personal agent | **never, raw** — the personal agent decides what crosses down |
| **L2** | personal agent ↔ BCN: the delegation thread (requests, results, HITL relays, lifecycle acts) | BCN, as the caller-facing contract surface | yes — this is what makes "make *it* cheaper" resolvable |
| **L3** | BCN ↔ supply agents: group transcripts, run events, coordination traffic; plus each expert's own working context held **provider-side** per `session_id` | BCN + providers | yes (BCN part); provider-held part never enters BCN |

The L2/L3 boundary is a projection in both directions. Downward: BCN composes
step dispatches *from* the L2 request; the user's L1 history never crosses.
Upward: the personal agent receives results and `input-required` questions —
not the experts' raw event streams, which include other vendors' reasoning
traces and are not the caller's to read.

Granularity and volume differ by an order of magnitude: L2 is tens of
task-level messages; L3 is hundreds of step/message/event-level records per
run.

## Decision 3 — three lifetimes: run, episode, task session

| Unit | Lifetime | Scopes |
| --- | --- | --- |
| **Run** | minutes–hours | one workflow execution: state-machine position, checkpoints, dispatch records, recovery (Doc 03) |
| **Episode** | minutes–hours | a burst of active interaction; the only period state is **warm** |
| **Task session** | task lifetime | the durable L2 thread + L3 workspace in the shared DB |

**Warmth** is the hydrated in-memory working state on the task's assigned
node: assembled session context, the compiled workflow, run-state caches. The
rule that makes this compatible with HA:

> Node-local state is a **rebuildable cache**, never the only copy. Durable
> facts are written through to the shared DB before acknowledgement
> (write-back buffering of durable facts is prohibited — it converts a node
> failure into data loss). The trade affinity buys is read savings, not write
> savings.

Therefore GC of an idle episode, resumption of a months-old task, and failover
after a node death are the same operation — rehydrate from the DB and
continue. **Failover is a cache miss.** The recovery path is exercised
continuously in normal operation, not only in emergencies.

Intra-episode cache affinity is what placement hashing optimizes (Doc 02):
round 2 lands seconds later on the node where round 1 is still warm. Affinity
at months scale is explicitly *not* a goal.

## Decision 4 — retention is a policy knob, split by layer

- **L2 (thread of record)** survives for the task's lifetime: what was asked,
  what was delivered, what was approved. It guarantees *resumability*.
- **L3 (working state)** is compactable/expirable on its own clock, including
  signalling providers to drop session context. Retaining it buys *revision
  quality*: with L3 live, "change the hotel" resumes the expert's held state;
  with L3 expired, experts re-brief from the L2 record — a graceful quality
  degradation, never a correctness loss.

The personal-agent-facing API therefore gains **session lifecycle controls**:
continue (default on each round), close ("task done — drop working state"),
and expire-policy configuration. "Forget this task's working data" is an API
call, not an architecture change.

Privacy posture, stated for review: BCN retains **task working state, not the
user's conversational life**. L1 never enters BCN; L2 holds only
delegation-relevant context the personal agent chose to send; L3 is bounded by
the retention policy above.

## What this requires (delta)

- A2A server surface on BCN for personal-agent callers (rides the existing
  A2A migration direction; the profile extensions are Doc 03's scope).
- Task-session model binding `(user_id, task_id)` → BCN session/group ids,
  with the L2 projection persisted as the caller-facing record.
- Session lifecycle API (continue / close / expire-policy).
- Episode hydration/eviction managed by the executor plane (Doc 02); no new
  storage — the durable forms all exist in current stores.

## Rejected alternatives

- **Fully stateless rounds** (each delegation self-contained): breaks
  interactive refinement and discards expert working state; rejected.
- **Months-long warm sessions** (task pinned to a node for its lifetime):
  warmth has no value at that timescale, inflates the privacy surface, and
  turns GC into a correctness concern; rejected in favor of
  durable-session/episode-warmth.
- **Personal agent as peer bot (posture B)**: reintroduces a standing client
  connection into the HA problem and requires the personal agent to be
  addressable or hold a socket; rejected as the backbone (may return later as
  an optional push channel).
