# 03 — Execution Correctness

- Parent: [BCN Cloud Orchestration for Personal Agents](README.md)
- Status: Draft for team review

## Problem

Workflow steps have real-world side effects: book the hotel, buy the ticket,
send the message. Three windows threaten correctness the moment execution is
distributed:

1. **At-least-once delivery.** Timeouts, retries, and redelivery exist on
   every channel (the Provider 2.0 protocol itself documents that ACK loss can
   duplicate delivery and promises only best-effort suppression). Without
   receiver-side dedup, a retry double-books.
2. **The lost-id window.** A node sends a dispatch and dies before recording
   the server-assigned task id. The new owner cannot `GetTask` an id nobody
   wrote down — yet the dispatch may or may not have arrived.
3. **Outcome limbo after takeover.** For any dispatch not in a terminal state,
   the new owner must distinguish "never arrived" / "in progress" / "completed,
   result lost in transit" — information that partially died with the old node
   under a push model.

Status quo: the engine A2A migration's own review established that **BCS
passes no idempotency key today** (`idempotency_key: None` at the A2A chat
call site), and the idempotency window designed there (`avernet.retryOnly`,
D14) is a **process-local** registry — correct for one node, broken for N,
since a retry landing on another node (or a failed-over probe) looks new.

## Design

### 1. Write-ahead dispatch intent, keyed by the client

The identifier recovery keys on must be **chosen by BCN and durably recorded
before the request leaves**; the server-assigned task id is a cached
convenience learned later.

```
idem_key = f(task_id, step_id, attempt)          # deterministic

1. INSERT dispatch record {run, step, attempt, idem_key, state=SENDING}
2. send A2A message, idem_key in metadata
3. on response: UPDATE {server_task_id, state=DISPATCHED}
...
n. on terminal event: UPDATE {state=COMPLETED|FAILED}
```

All writes carry the executor lease's fencing token (Doc 02). A crash at any
point leaves a durable record a successor can act on. This extends BCS's
existing persist-before-dispatch discipline one hop outward: nothing in
recovery depends on information that existed only in a dead node's memory.

### 2. Pull-based reconciliation: the agent holds queryable state

Principle: **reads are idempotent; pushes are not.** The supply agent (the
A2A server for the BCN→agent pairing) retains the Task and its artifacts,
queryable via `GetTask`. On takeover, for every dispatch record not terminal:

| State found | Probe result | Action |
| --- | --- | --- |
| `SENDING` | key unknown at agent | first delivery never arrived → send (safe) |
| `SENDING` | key known → existing task returned | id recovered; continue below |
| `DISPATCHED` | task `working` / `input-required` | subscribe and wait |
| `DISPATCHED` | task `completed` / `failed` | fetch result, advance — **no re-execution** |

Two probe styles, both required in the profile:

- **Idempotent resend**: same request, same key — the agent either recognizes
  the key (returns the existing task) or treats it as first delivery. Used
  inside the dedup window.
- **Probe-first ("retry-only")**: a flag meaning *execute only if you have
  seen this key; otherwise refuse and say so*. A refusal is proof of
  non-arrival. This is the engine-A2A design's D14 mechanism, adopted as-is;
  it exists because a bare resend outside the window is indistinguishable
  from a new request.

Corollaries: no inbound queue is needed inside BCN — the agent's task store
is the buffer, and it keeps accumulating results even while BCN is down
(results wait; no retry storm against a dead service). BCN's own half of the
bargain is persist-before-ack, so an acknowledged result is never re-asked.
An asynchronously delivered "response" is, on the wire, a new inbound
request; dedup applies to any inbound message regardless of semantic role, so
BCN keeps its own (shared, durable) seen-set for caller and agent retries.

### 3. The BCN A2A profile

Base A2A 1.0 provides client-generated `messageId`, server Tasks with
lifecycle states, `GetTask`/`SubscribeToTask`, and an open `metadata` +
extension-URI mechanism. It does **not** specify dedup semantics, key-based
lookup, or retention. Those become a named extension profile — not a protocol
fork; vanilla A2A traffic flows unchanged:

| Clause | Content |
| --- | --- |
| Key convention | `idem_key` in request metadata, derived from `(task_id, step_id, attempt)` |
| Dedup | same key ⇒ same task, atomically checked at execution admission; same key with conflicting payload ⇒ keep first, flag error |
| Probe semantics | idempotent-resend inside the window; retry-only flag honored with atomic known/unknown judgment |
| Retention floor | terminal task state **and** the key seen-set retained ≥ the agreed window; within it, "task not found" provably means "never arrived" |
| Window sizing | ≥ worst-case (lease TTL + takeover RTO + reconciliation sweep) with margin; one clock for both retentions |
| Declaration | extension URI in the AgentCard / registration capability flag (Doc 04) |
| Escape hatch | a probe landing **outside** the window is undecidable: no automatic resend of side-effecting steps — surface "outcome unknown, verify" to the caller for reconciliation |

Scope note: this profile is **this project's deliverable**. It builds on the
engine A2A migration (method surface, `retryOnly` judgment) but adds what that
design did not need single-node: durable/shared dedup state, the retention
contract, and cross-node probe correctness.

### 4. Side-effect classes scope the burden

Workflow YAML nodes gain a side-effect class:

- `read_only` / `idempotent` (search, fetch, summarize): blind resend is
  safe; **any vanilla A2A agent qualifies** — no profile required.
- `external_side_effect` (book, pay, send): dispatchable **only** to agents
  whose profile compliance is verified (Doc 04's tiering), or the step does
  not run.

This converts a blanket ecosystem demand into a targeted one on exactly the
steps where a duplicate costs real money, and gives the dispatch engine a
principled eligibility rule instead of a hope.

## Costs, stated honestly

- **Happy-path runtime cost ≈ 0**: the key rides in metadata; the write-ahead
  record extends an existing write; probes run only during recovery.
- **BCN-side**: columns + a recovery sweep; durable seen-set; shared window
  state. Small.
- **Agent-side**: a durable seen-set with atomic check-and-execute per
  adapter — real but bounded work, enumerated per category in Doc 04. The
  probe-vs-first-delivery race is genuinely subtle (the engine-A2A review
  spent two rounds on it); the conformance suite must cover it.
- **Ecosystem**: the profile must be adopted before an agent can serve
  side-effecting steps (Doc 04). The floor if an agent never complies is the
  escape hatch above: manual reconciliation instead of autonomous retry —
  priced, not assumed away.

## Rejected alternatives

- **Exactly-once delivery infrastructure** (transactional outbox through a
  broker to agents): cannot reach third-party agents and still ends at
  receiver-side dedup; the profile is that dedup, minus the broker.
- **Server-generated ids only**: leaves the lost-id window unresolvable by
  construction.
- **Blind resend with "most agents are probably idempotent"**: rejected;
  duplicate purchases are the one failure class the skeptics are right to
  veto on.
- **No profile, manual reconciliation always**: survives as the documented
  floor for non-compliant agents, rejected as the default — it caps exactly
  the autonomy the cloud track exists to provide.
