# Cluster Executor Plane — Notes for Sibling Items

[简体中文](cross-item-notes.zh-CN.md) · Part of the [cluster executor plane design](design.md)

Recorded so they are not lost; none are built by item 1.

**Item 2 (connections and forwarding)**

- Relay each bot event to the session's **current** owner, looked up per
  event (hint + `NOT_OWNER`), never cached for the life of a run.
- A draining state for sockets; a "please reconnect" control frame (like
  HTTP/2 `GOAWAY`) supported by the bot plugins.
- Cluster-wide bot reachability ("where is X connected?"), replacing today's
  process-local `is_available`.
- A delivery wait state for "bot reconnecting" (wait, bounded) instead of
  today's failure after the startup grace.
- Wake the owners that have queued deliveries when a bot reconnects
  (peer channel), to keep poll latency out of TTFT.
- The registry may live in Redis (soft state).
- Plugin behavior on reconnect differs: the DeepSeek Harness plugin buffers
  events while disconnected (up to 1,000 events / 16 MiB) and flushes after
  reconnect; no equivalent buffer was found in the OpenClaw plugin
  (unverified end to end).
- Tracking issue [inclusionAI/Avernet#3506](https://github.com/inclusionAI/Avernet/issues/3506)
  (owner nudge) is superseded by D6's forwarding ([design §9](design.md#9-routing-a-round-to-its-owner-d6)).

**Item 3 (execution correctness)**

- `bcs_message_deliveries` already has most of Doc 03's dispatch record
  (`run_id`, `idempotency_key`, `attempt_no`, per-attempt `request_id`,
  `may_have_been_sent`, a never-auto-resent `unknown` state, CAS versions).
  Decide explicitly whether to extend it to the state-machine flow or build
  `bcs_dispatch_records`, to avoid two overlapping ledgers.
- Dispatch side-effecting steps with streaming or `return_immediately =
  true`, so the task id is recorded at once; require side-effecting steps to
  be answered with a Task (a Message response leaves nothing to query).
- Consider setting A2A `messageId` = the deterministic `idem_key`, so vanilla
  agents that dedup by `messageId` (the spec's MAY) benefit for free.
- Dispatch records must record the response kind; a recorded Message is
  terminal.
- The dedup retention window formula includes "lease TTL"; with D5 that is
  the claim threshold — size it from the same configuration.
- All dispatch-record writes use the fence builder.

**Item 4 (BCN A2A profile)**

- State that closing a connection does not cancel a task.
- Ask agents to stream progress into task artifacts, so a successor's
  re-subscribe does not leave a token gap.
- Retention floor for tasks and the key seen-set.

**Item 5 (PA-facing surface)**

- BCN always answers `SendMessage` with a Task; callers use
  `return_immediately = true` or streaming (a blocking call would be held
  open for the whole run).
- Require the PA to **reuse `messageId` on retry**; dedup on
  `(principal, session_id, messageId)`, atomically, in the shared store.
  Without a stable client key, exactly-once is impossible on this hop, and
  downstream dedup cannot help (a duplicate round becomes a new run with new
  keys).
- Structure money-spending steps as approvals of server-issued proposals
  ("approve P-123"), which are idempotent by construction.
- Define what a round arriving during an active run means (input to the run,
  queued, or rejected as busy).
- Transports (gRPC, JSON-RPC, REST, SOFA): one protocol-neutral dedup
  implementation; thin adapters extract the key and principal, hash the
  normalized request for conflict detection, and map outcomes to native
  errors. Framework retries (gRPC hedging, SOFA failover) make concurrent
  and cross-node duplicates normal. SOFA is a custom binding we must specify.
- Wording for "exactly once": *at-least-once delivery on every hop;
  effectively-once side effects for profile-verified agents within the dedup
  retention window; outcomes that cannot be determined are surfaced, never
  guessed.*
