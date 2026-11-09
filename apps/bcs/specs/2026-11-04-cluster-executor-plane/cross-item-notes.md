# Cluster Executor Plane — What Sibling Items Must Provide

[简体中文](cross-item-notes.zh-CN.md) · Part of the [cluster executor plane design](design.md)

The executor plane relies on a few properties it does not build itself.
They are listed here so each sibling item can design for them.

## Item 2 — the gateway tier (connections and relay)

The gateway tier (design §7) holds every long-lived connection. Item 2
builds it to this contract:

- **Hold every connection; hold no durable state.** PA streams, outbound
  A2A streams terminate on gateways; every supply agent is an A2A agent
  that BCN dials out to. Executors keep none.
- **Send every downstream event to the session's owner** (design §7.2–7.3):
  deltas in 200 ms batches, published on the message queue to the delta
  channel of the session's bucket, without acknowledgement; control events (the
  agent's task id, `input-required` / `auth-required`, the final
  result with the assembled output) retried until
  acknowledged. Telling the two apart is part of the gateway–executor
  contract.
- **Look the owner up per control message** (hint cache + `NOT_OWNER`); never
  remember an owner for the life of a run. This is what lets a session move
  without touching any connection.
- **Read by session, never by owner** (design §7.4): read S's stream
  (`session:{S}`) from the last entry id relayed; start reading before
  sending `Submit`; drop entries carrying an older fencing token than the
  highest seen. On reconnect, build the snapshot from the stream's latest
  snapshot entry plus the entries after it (design §7.5). Fan-out decisions
  are the owner's; the gateway only relays what is appended.
- **Keep each in-flight dispatch's output** until the owner acknowledges the
  result, and hand it to a new owner on `FetchBuffer` (design §7.2); with no
  buffer, answer by querying the agent's task (`GetTask` or a
  `SubscribeToTask` snapshot).

## Item 3 — execution correctness

- **Use the fence step** in every dispatch-record transaction: lock the
  session's bucket row in `bcs_task_leases` (one row per bucket) and check
  the token. A session has no lease of its own.
- **Record where each dispatch runs:** the dispatch record names its gateway
  (any healthy one; the accepting gateway reports its id), written fenced. When that gateway dies, the owner moves the dispatch to
  another gateway, which reconciles by the recorded task id (read or
  re-subscribe, never blindly resend).
- **Deduplicate dispatch reports** by `(dispatch, seq)`: a gateway retries
  control events and results until the owner acknowledges them.
- **Reuse one delivery ledger.** Dispatch intent records should extend the
  existing per-delivery ledger rather than create a second one with
  overlapping meaning.
- **Record the agent's task id immediately:** dispatch side-effecting steps
  with streaming or `return_immediately = true`, and require them to be
  answered with a Task (a bare Message leaves nothing to query after a
  failover). A recorded Message response is terminal.
- **Make `messageId` the idempotency key** (deterministic per run, step and
  attempt), so even agents that only dedup on `messageId` benefit.
- **Size the dedup window from the longest a request can stay in flight,
  not from the claim threshold.** A paused or partitioned owner's request
  that was already sent can arrive long after a successor took over. The
  window must cover the maximum lifetime of one dispatch attempt (send
  timeout × retry budget + maximum network and queue delay) plus takeover
  (the claim threshold) plus reconciliation, and senders must enforce that
  lifetime: an attempt older than it is never sent or retried. The claim
  threshold is only one term.

## Item 4 — the BCN A2A profile

- Closing a connection must not cancel a task.
- `SubscribeToTask`'s snapshot must include every artifact produced so far:
  it is how a new dispatch gateway recovers a step's partial output.
- Agents should stream progress into task artifacts, so a successor that
  re-subscribes sees no gap.
- A retention floor for tasks and for the key seen-set.

## Item 5 — the personal-agent surface

- **BCN always answers `SendMessage` with a Task**; callers use
  `return_immediately = true` or streaming (a blocking call would stay open
  for the whole run).
- **The PA must reuse `messageId` when it retries.** BCN dedups on
  `(caller, session, messageId)` atomically in the shared store. Without a
  stable client key, exactly-once is impossible on this hop, and downstream
  dedup cannot help: a duplicate round becomes a new run with new keys.
- **Make money-spending steps approvals of server-issued proposals**
  ("approve P-123"), which are idempotent by construction.
- **Decide what a round arriving during an active run means:** input to the
  run, queued, or rejected as busy.
- **One dedup implementation for every transport** (gRPC, JSON-RPC, REST,
  SOFA): adapters only extract the key and caller, hash the normalized
  request for conflict detection, and map outcomes to native errors.
  Framework retries (gRPC hedging, SOFA failover) make concurrent and
  cross-node duplicates normal.
- **Reconnecting is snapshot plus continue:** after any break, the PA calls
  `SubscribeToTask` on any gateway and gets the current Task, then live
  events. Intermediate output may reset after a failure; the final result is
  exact. Frame-exact `Last-Event-ID` resume is an optional BCN extension.
- **Webhook push to PAs** runs off the hot path (long retries, dead letters),
  either through a message queue or an outbox written in the fenced commit.
- **Wording for "exactly once":** *at-least-once delivery on every hop;
  effectively-once side effects for profile-verified agents within the dedup
  window; outcomes that cannot be determined are surfaced, never guessed.*
