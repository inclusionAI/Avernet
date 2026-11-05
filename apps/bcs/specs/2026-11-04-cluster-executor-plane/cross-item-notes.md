# Cluster Executor Plane — What Sibling Items Must Provide

[简体中文](cross-item-notes.zh-CN.md) · Part of the [cluster executor plane design](design.md)

The executor plane relies on a few properties it does not build itself.
They are listed here so each sibling item can design for them.

## Item 2 — the gateway tier (connections and relay)

The gateway tier (design §7) holds every long-lived connection. Item 2
builds it to this contract:

- **Hold every connection; hold no durable state.** PA streams, outbound
  A2A and Provider streams, and dial-in bot sockets all terminate on
  gateways. Executors keep none.
- **Classify every downstream event** (design §7.3): deltas go only to
  subscribers and the step buffer; control events (the agent's task id,
  `input-required` / `auth-required`, coordination tool calls, the final
  result with the assembled output) go to the session's owner. The
  classifier is part of the gateway–executor contract.
- **Deliver control events and results to the current owner**, looked up per
  message (hint cache + `NOT_OWNER`), each with a per-dispatch sequence
  number, retried until acknowledged. Never remember an owner for the life
  of a run. This is what lets a session move without touching any
  connection.
- **Be a subscriber with a cursor** (design §7.4): keep `last_seq` per
  session, subscribe with it, re-subscribe on a channel break, a stale owner
  or a `NOT_OWNER` / `MOVED`, and re-assert subscriptions every 5 s.
- **Keep a step buffer per in-flight dispatch** and serve it, attached to the
  live feed in one step, to any gateway rebuilding a snapshot (design §7.5).
- **Cluster-wide bot reachability:** "which gateway holds bot X's socket?"
  must be answerable from any node. This registry is soft state (rebuilt
  when bots reconnect, verified by forwarding), so a fast store such as
  Redis is fine.
- **Draining sockets:** a socket on a draining gateway takes no new work;
  once its in-flight runs end, the gateway sends a "please reconnect"
  control frame (like HTTP/2 `GOAWAY`) and the bot redials elsewhere.
- **Waiting, not failing:** work for a bot that is reconnecting waits as a
  durable delivery owned by the session's owner, with a bound, and the owner
  is woken when the bot is reachable again.
- **Bots should buffer events while disconnected** and resend them after
  reconnecting, so a socket drop does not lose a run's output.

## Item 3 — execution correctness

- **Use the fence step** in every dispatch-record transaction.
- **Record where each dispatch runs:** the dispatch record names its gateway,
  written fenced. When that gateway dies, the owner moves the dispatch to
  another gateway, which reconciles by the recorded task id (read or
  re-subscribe, never blindly resend).
- **Deduplicate dispatch reports** by `(dispatch, seq)`: a gateway retries
  control events and results until the owner acknowledges them.
- **Host the session event log** (design §12): control events for subscribers,
  numbered by `event_seq`, written in the same fenced commit as the state
  they describe. Extending the delivery ledger is preferred over a new table.
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
