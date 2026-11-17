# Plan: distributed Connector runtime

Status: proposed; awaiting review. Implements [spec](spec.md); store mapping in
[research](research.md). Baseline `dev` = `5c3e30ea`.

## 1. Shape

```text
bcs-service-api (additive)
  core::dist_connector        DistConnectorCoreService: pure rules, no IO          [contract, §2]

services/bcs-dist-connector  (new)                                                 [engine, §3]
  rules      DistConnectorRules: the DistConnectorCoreService implementation
  engine     main loop: receive → claim → ack → spawn actor; drain; snapshot
  actor      one per holder generation: send / recover / stream / control tick / finish
  ingress    events → InboxProducerPort (admit | append_stream), blob offload
  control    pending_input and cancel
  uses ports: DispatchQueuePort, HeldDispatchPort, CommandFeedPort, InboxProducerPort,
              DistAgentClientPort, MembershipPort, BlobStorePort
  implements: DistRuntimeControlPort

services/bcs-dist-connector-store  (new, the only new user of bcs-store-api)      [providers, §4]
  doc        stored disp document (DispatchView + connector object)
  queue      StoreDispatchQueue: DispatchQueuePort (claim, resume_owned, notifications)
  held       StoreHeldDispatch: HeldDispatchPort
  feed       StoreCommandFeed: CommandFeedPort (consumer group)
  held_index add before claim, remove after ENDED
  recovery   ConnectorRecoveryPort (local trait) + stub provider: repost_held (D3), release (D8)
  membership PendingMembership: MembershipPort stub (provider pending from the executor team)

services/bcs-dist-store (shared, additive)                                         [§5]
  command    CommandStreams { partitions }, partition_of(did), GROUP

external-clients/bcs-a2a-http (additive)                                           [adapter, §6]
  dist_agent A2aDistAgentClient: DistAgentClientPort; DistAgentResolver (injected trait)

application/bcs-app-dist-runtime (new)                                             [admin, §7]
  ConnectorRuntimeFacade: DistConnectorRuntimeService over DistRuntimeControlPort
```

Nothing is registered in the `bcs` bootstrap binary; composition is a later
task (I1). Why a sibling store crate instead of new modules in
`bcs-dist-store`: the Gateway session edits that crate at the same time
(task-feed provider, removal of `Duplicate`), the Connector providers are used
by nobody else, and the split keeps the only shared edit to one new file.

## 2. Core contract: `bcs_service_api::core::dist_connector` (additive)

A pure rules trait, like `DistRunnerCoreService`: no storage, network, clock or
randomness. The module doc states the order of one call (spec B1–B6) as the
contract the engine follows. **For the executor team to review.**

```rust
pub enum EventClass { Key, Intermediate }

pub struct CallContext<'a> {            // what the rules may look at
    pub spec: &'a DispatchSpec,
    pub holder_epoch: HolderEpoch,
    pub confirmed_input_through: Option<InputSeq>,
}

pub enum Report {                        // result of normalizing one remote event
    Admit(InputEnvelope),                // key event → InboxProducerPort::admit
    Append(AppendAgentEvent),            // intermediate → InboxProducerPort::append_stream
}

pub enum RecoveryStep {                  // what a (new) holder does first
    Send,                                // phase claimed: nothing was sent
    Resend,                              // possibly sent, agent declares the send idempotent
    QueryByRequest,                      // possibly sent, agent can be asked by request id
    Subscribe,                           // accepted: follow the remote task's events
    PollByTask,                          // accepted, no subscribe: poll the remote task
    Unresolved { code: String },         // may not resend and cannot find out (D2)
}

pub enum SendVerdict {                   // after one send attempt
    Accepted, Completed, Rejected,
    Retry,                               // definite not-sent, attempts left
    Recover(RecoveryStep),               // outcome unknown → SENT policy
    Unresolved { code: String },
}

pub trait DistConnectorCoreService: Send + Sync {
    fn classify(&self, kind: &AgentEventKind) -> EventClass;
    /// Stable request id of a send: start = "<did>:start", input = "<did>:in:<seq>".
    fn request_id(&self, did: &Did, operation: SendKind) -> RequestId;
    /// Stable identity of a key event, without the holder epoch (decision 29 b).
    fn key_identity(&self, ctx: &CallContext<'_>, kind: KeyKind) -> KeyIdentity; // { input_id, interaction }
    /// (did, holder_epoch, seq) + identity + digest; `seq` is supplied by the caller.
    fn normalize(&self, ctx: &CallContext<'_>, seq: InputSeq, event: RemoteEvent)
        -> Result<Report, ConnectorRuleError>;
    fn recovery(&self, view: &DispatchView, sending: Option<&SendKind>) -> RecoveryStep;
    fn after_send(&self, caps: &DownstreamCapabilities, kind: &SendKind,
        outcome: SendClass, not_sent_attempts: u32) -> SendVerdict;
    /// final → Completed; error → Canceled when the payload state is "canceled", else Failed.
    fn terminal(&self, kind: &AgentEventKind, payload: &Payload) -> Option<FinalDispatchOutcome>;
}
```

Rules fixed by the trait docs:

| Rule | Value |
| --- | --- |
| `input_id` | `ae:<hex of digest_fields(did, "final")>`; same with `"error"`; `(did, "input_required" \| "auth_required", n)` with n = `confirmed_input_through + 1` (1 when none). `InteractionId` = the same value with prefix `ai:` |
| `business_digest` | `digest_json({did, kind, payload})`: no holder epoch, seq, remote event id or cursor |
| `seq` | starts at 1 after each claim (also after `resume_owned`), +1 per reported event, key and intermediate share one counter |
| SENT policy (start) | `idempotent_start` → `Resend`; else `query_by_request` → `QueryByRequest`; else `Unresolved` |
| SENT policy (input) | `idempotent_input` → `Resend`; else `Unresolved` (the input stays pending and unconfirmed; later inputs wait) |
| definite not-sent | retried 3 times (`NOT_SENT_RETRIES`), then `Unresolved` |
| accepted | `subscribe` → `Subscribe`; else `query_by_task` → `PollByTask`; else `Unresolved` |
| A2A `canceled` / `rejected` | `AgentEventKind::Error`, payload `{"state":"canceled" \| "rejected", …}` |
| payloads | delta/thinking/tool/snapshot: the adapter's JSON of the A2A event; final: the remote task snapshot (see §3.3); error: `{"state", "code"?, "message"?}` |

Known limit (tracked, per the 2026-11-17 review): two different input-required
events before a confirmed reply share one `input_id`.

## 3. Engine: `crates/services/bcs-dist-connector`

### 3.1 Main loop (`engine.rs`)

```text
start: membership.register (need_contract → warn once, continue) ; queue.notifications(pool, me)
loop until draining:
  free = max_held − actors ; if free == 0 → wait for an actor to end
  deliveries = feed.receive(free, 1 s)
  for each delivery:
     claim(did)  Acquired(h)   → spawn actor(h)
                 OwnedByMe     → actor alive ? poke it (refresh now)
                                             : resume_owned(previous) → spawn actor
                 HeldByOther | Ended | NotFound → nothing
                 Err           → no ack (redelivered by idle takeover); log
     ack(delivery)             // only after the decision above
```

- One actor per `(did, holder generation)`, kept in a map; a finished or lost
  actor removes itself. `resume_owned` is called only after the old actor's
  task has been joined.
- Backpressure: the loop does not read hints while `max_held` actors run.
- `drain(grace)`: stop reading and claiming, call `membership.set_draining`
  (stub tolerated), wait up to `grace` for actors to end, then for each
  leftover call `recovery.release` (D8 stub → logged) and return
  `DeadlineReached { remaining }`. Actors are not aborted: their calls stay
  held and recoverable.
- `snapshot`: `active_work` = actors, `pending_effects` = unresolved calls +
  pending cancels; `ready` = feed open and not draining.

### 3.2 Actor (`actor.rs`, `sender.rs`, `receiver.rs`)

```text
step = rules.recovery(view, connector.sending)
Send / Resend:
   refresh ; cancel already requested and nothing sent → report canceled, finish(Canceled)
   begin_send(Start{request_id})                         // durable before the network call
   client.start(target, StableSendRequest{request_id, context_id: None, payload})
     Accepted{remote, events}  → record_acceptance → returned cancel intent? run cancel → stream
     Completed{result}         → report final(result) → finish(Completed)
     Rejected{code}            → report error{state: rejected, code} → finish(Failed)
     NotSent                   → backoff, retry ≤ 3, then unresolved
     OutcomeUnknown            → rules.after_send → Resend | QueryByRequest | unresolved
QueryByRequest:  Found → record_acceptance → stream ; NotFound | Unsupported → unresolved ;
                 Unavailable → backoff and ask again
Subscribe:       client.subscribe(remote, None) ; read error → backoff, resubscribe
PollByTask:      client.query(ByTask) every poll interval until terminal
stream: for each RemoteEvent → rules.normalize(seq += 1) → ingress
        terminal key event admitted → finish(outcome)   // final before finish (B5)
unresolved: report_unresolved (D2 stub → log) ; keep the control tick ; never finish
every 1–2 s (jittered) and on a poke: control tick (§3.4) ; HolderError::Lost → stop, write nothing
```

A transport failure never becomes an agent event (B11). The actor enforces no
deadline (B13).

### 3.3 Reporting (`ingress.rs`)

- `Report::Admit` → `InboxProducerPort::admit(AdmitRequest { input, create_session: None, create_task: None })`.
  `Accepted` and `Duplicate` are the same success. `ContentConflict` is logged
  at error level and counted; for a terminal event the call is still finished
  (a terminal is already admitted under that identity). `Quarantined` stops
  the actor without finishing. Store errors are retried with backoff under the
  same `input_id` while the actor still holds the call.
- `Report::Append` → `append_stream`; an error is retried once, then the event
  is dropped with a warning (intermediate events are best-effort).
- Final payload: when the agent declares `query_by_task`, the actor reads the
  remote task once (`query(ByTask)`) on the terminal status and reports that
  snapshot, so a later holder re-reading the task produces the same content and
  the same digest. Otherwise the terminal event's own payload.
- Payloads larger than `max_inline_bytes` (engine config) are stored with
  `BlobStorePort::put_immutable` under `disp/<did>/<input_id | epoch-seq>` and
  reported as `Payload::Ref`. A request `Payload::Ref` is loaded with
  `BlobStorePort::get` before the send.
- No batching of intermediate events (design proposal, not MVP).

### 3.4 Replies and cancels (`control.rs`)

Per tick, from `held.refresh()` (which hides pending inputs at or below
`confirmed_input_through`; removing them is the Worker's job, D5):

- Next pending input (`seq == confirmed + 1`), phase accepted, nothing being
  sent: `begin_send(Input{seq, request_id})` → `client.send_input(target, remote, …)`.
  `Accepted` → `confirm_input(seq, evidence)`, follow the returned event stream
  (or resubscribe). `Rejected` → not confirmed, logged, unresolved input.
  `NotSent` → retry ≤ 3. `OutcomeUnknown` → `idempotent_input` ? resend : unresolved input.
- Cancel intent present and no final cancel observation:
  nothing sent yet → report canceled, `finish(Canceled)`;
  no `cancel` capability → `record_cancel(Rejected { code: "cancel_unsupported" })`;
  else `record_cancel(Sending)` → `client.cancel(target, remote, intent.request_id)` →
  `record_cancel(mapped)`. `ConfirmedCanceled` → report canceled, `finish(Canceled)`.
  `RequestAccepted` / `AlreadyFinished` → keep reading until the terminal event.
  `OutcomeUnknown` → retried on later ticks.

### 3.5 Delayed-contract stubs in the engine

| Method | Item | Returns | Caller |
| --- | --- | --- | --- |
| `UnresolvedReporter::report(did, UnresolvedKind)` | D2 | `RuntimeError { code: "need_contract_not_implemented" }` | logs, leaves the phase |

### 3.6 Access cost (per held call)

| Step | Reads | Writes |
| --- | --- | --- |
| claim | 1 `disp` | 1 held-index, 1 `disp` |
| begin_send, record_acceptance, confirm_input, record_cancel | 1 each | 1 each |
| finish | 1 | 1 `disp`, 1 held-index |
| control tick (1–2 s) | 1 projection (`/control`, `/execution`) | 0 |
| intermediate event | 0 | 3 (inbox, active, wake) via the shared producer |
| key event | 1–2 | 4 via the shared producer |

A normal call is 5 `disp` reads + 5 `disp` writes plus ticks. Idle cost is
`held / 1.5` projection reads per second per Connector, bounded by `max_held`.
Conflict retries are bounded (5) and re-read first. No scan grows with history:
the feed reads at most `free` hints, idle takeover pages by `limit`.

## 4. Providers: `crates/services/bcs-dist-connector-store`

### 4.1 Claim (`queue.rs`)

```text
held_index.add(me, did, now)                         // before the claim
loop (≤ 3):
  doc, v = get_json(disp)            none → NotFound
  phase ended                        → Ended
  holder.instance == me              → OwnedByMe(holder)
  holder is someone else             → membership.liveness(holder.instance)
                                         Dead → continue ; Alive | Unknown | Err → HeldByOther(holder)
  e = max(holder.epoch, band_of(v)) + 1
  mutate_json(disp, Newer(version::claim(e)),
              Patch[Set /execution {holder: {me, e}, phase: created→claimed else kept, rest kept},
                    Set /connector {v:1, claim_request, sending: kept, finish_evidence: kept}],
              Preserve)
     Applied          → Acquired(StoreHeldDispatch)
     ConditionNotMet  → loop (re-read, re-decide)
     OutcomeUnknown   → StoreError::OutcomeUnknown (not acked; the retry finds OwnedByMe)
outcome is not Acquired / OwnedByMe / unknown → held_index.remove(me, did)
```

`PendingMembership` (the only `MembershipPort` available until the executor
team's provider exists) fails every method with
`need_contract_not_implemented`, so a call held by another instance is
`HeldByOther`. The takeover branch itself is implemented and tested with a
fake `MembershipPort` that reports `Dead`.

`resume_owned`: same write with the same instance and epoch + 1, only when the
stored holder equals `previous`; otherwise the outcome the document shows.

### 4.2 Holder writes (`held.rs`)

One helper for every write: project `/control`, `/execution`, `/connector`
with the version → holder must equal mine (instance and epoch), else
`HolderError::Lost` → compute the new `execution` / `connector` →
`mutate_json(Newer(version::next(read)), Patch[Set /execution, Set /connector], Preserve)`.
`ConditionNotMet` → re-read and recompute (≤ 5, then
`NotApplied { code: "disp_write_contended" }`). `OutcomeUnknown` → re-read and
compare content: applied, or retry.

| Method | Precondition (else `InvalidTransition`) | Effect | Repeat |
| --- | --- | --- | --- |
| `begin_send(Start)` | phase claimed | phase sending; `connector.sending = start` | same request while sending → same permit |
| `begin_send(Input{seq})` | phase accepted; `seq == confirmed + 1`; input pending | `connector.sending = input` | same |
| `record_acceptance` | phase sending or accepted | phase accepted; `remote`; `sending` cleared; returns `control.cancel` from the same read | same remote → no write |
| `confirm_input(seq)` | `seq == confirmed + 1` | `confirmed_input_through = seq`; `sending` cleared | `seq ≤ confirmed` → `AlreadyApplied` |
| `record_cancel(obs)` | not after `ConfirmedCanceled` / `AlreadyFinished` | `cancellation = obs` | equal → `AlreadyApplied` |
| `finish(outcome, evidence)` | phase not ended | phase ended; `outcome`; `finish_evidence`; then held-index remove | same outcome → `AlreadyApplied`; other → `ContentConflict` |
| `refresh` | — | view from the read, pending inputs ≤ confirmed hidden | — |

The Connector never writes `/spec` or `/control`.

### 4.3 Command feed (`feed.rs`)

- `notifications(pool, me)`: for every partition `0..CommandStreams::partitions`,
  `create_group(key, GROUP, GroupStart::Beginning)`. Consumer =
  `<me.node>:<me.incarnation>`; the incarnation is the process start time in
  epoch ms, set by whoever builds the `Instance`.
- One in-flight `read_group` per partition (one key per blocking call), kept
  alive across `receive` calls so a registered delivery is never dropped.
  `receive(max, timeout)` returns as soon as any partition yields entries or
  the timeout passes.
- Every `reclaim_every` (default 10 s): `claim_idle(key, GROUP, consumer, min_idle = 30 s, …)`
  per partition, paged, at most `max` entries.
- `CommandDelivery { did, backend, consumer_generation: consumer, opaque: "p<n>:<major>-<minor>" }`.
  `ack` parses `opaque` and calls `ack(key, GROUP, [id])`; zero newly acked is success.
- An entry without a readable `did` is acked and logged. `GroupMissing` →
  recreate the group and continue.

### 4.4 Recovery port (local, both stubs)

```rust
#[async_trait]
pub trait ConnectorRecoveryPort: Send + Sync {
    /// D3 (walkthrough §16, Gap 5): re-post the held dispatches of a dead instance.
    async fn repost_held(&self, dead: &Instance) -> Result<u64, StoreError>;
    /// D8 (walkthrough §16): write the call back to unclaimed and re-notify.
    async fn release(&self, did: &Did, holder: &Holder) -> Result<(), StoreError>;
}
```

Both return `error::need_contract()`. The held index itself is real.

## 5. Shared crate change: `bcs_dist_store::command` (new file only)

```rust
pub const GROUP: &str = "connectors";
pub struct CommandStreams { partitions: NonZeroU32 }   // from configuration; no Default
impl CommandStreams {
    pub fn new(partitions: NonZeroU32) -> Self;
    pub fn partitions(&self) -> u32;
    /// First 8 bytes (big-endian) of sha256(did) mod partitions: stable across processes and languages.
    pub fn partition_of(&self, did: &Did) -> u32;
}
```

The Worker's `notify` uses the same function with the same configured count.
Changing the count needs a coordinated restart (hints only; `disp` and the
Worker's deadline re-post cover the switch). `lib.rs` gains one `pub mod` line.

## 6. A2A adapter: `bcs-a2a-http::dist_agent` (C2)

Inside the existing crate, because it needs the private binding dispatch and
the guarded client with auth headers.

```rust
pub struct ResolvedAgent { pub interface: A2aInterface, pub bearer: Option<String>, pub extensions: Vec<String> }
#[async_trait]
pub trait DistAgentResolver: Send + Sync {
    /// Delayed (walkthrough §17): what resolves CredentialRef and what BindingVersion means.
    async fn resolve(&self, target: &AgentBinding) -> Result<ResolvedAgent, String>;
}
pub struct A2aDistAgentClient { /* resolver, OutboundUrlGuard, Option<GrpcMeshConfig> */ }
```

No resolver implementation ships; tests use a fixed one.

| Port call | Existing client call | Mapping |
| --- | --- | --- |
| `start` | `send_streaming_message` (`messageId` = request id, `contextId`) | first `Task` event → `Accepted { remote, events }`; standalone agent `Message` → `Completed` |
| `send_input` | same, plus `taskId` / `contextId` of the remote task | as above |
| `query(ByTask)` | `get_task` | `Found { remote, status, snapshot }`; task-not-found → `NotFound`; other errors → `Unavailable` |
| `query(ByRequest)` | none in A2A | `Unsupported` |
| `subscribe` | `subscribe_to_task` | event stream; `after` is ignored (the client exposes no cursor) |
| `cancel` | `cancel_task` | terminal `canceled` → `ConfirmedCanceled`; other terminal → `AlreadyFinished`; non-terminal → `RequestAccepted`; not-cancelable → one `get_task` to tell a finished task from a refusal |

Send outcome classification, reusing the existing `initial_rejection` rule:

| Failure | Outcome |
| --- | --- |
| resolver, URL guard, credential or client construction fails | `NotSent` (nothing left the process) |
| HTTP 400/401/403/404/405/406/415/422, gRPC 3/5/7/12/16 | `Rejected` |
| everything else once the request was handed to the transport (timeout, interrupted, 408/409/429/5xx, JSON-RPC errors, stream ended before the first event) | `OutcomeUnknown` |

The existing client discards whether a failure happened before the request was
written, so a refused connection is `OutcomeUnknown`, not `NotSent`. That is
safe (never a blind resend) but sends more calls to the SENT policy than
necessary; narrowing it means touching the shared low-level clients and is left
as a follow-up. No retry inside the adapter.

Event mapping: status `working`/`submitted` and artifact updates → `Delta`
(`Snapshot` for a full `Task`); `input-required` → `InputRequired`;
`auth-required` → `AuthRequired`; `completed` → `Final`; `failed`, `canceled`,
`rejected` → `Error` with the state in the payload. The interaction id in the
adapter's `AgentEventKind` is a placeholder; the rules replace it (§2).
`event_id` and `cursor` are `None` (the SSE decoder drops `id:` lines).

New dependency: `bcs-a2a-http` → `bcs-dist-model`.

## 7. Admin (C6)

- Engine implements `DistRuntimeControlPort` (`snapshot`, `drain`).
- `crates/application/bcs-app-dist-runtime`: `ConnectorRuntimeFacade`
  implements `DistConnectorRuntimeService` over `Arc<dyn DistRuntimeControlPort>`,
  translating the port result into the application result. Task W7 names the
  same crate for the Worker's facade; whoever lands second adds a file.

## 8. Tests

| Level | Where | What |
| --- | --- | --- |
| rules | `bcs-dist-connector/tests/rules.rs` | identities stable across holder epochs and without event ids; digest excludes holder/transport data; every SENT-policy row; not-sent ×3; terminal mapping |
| providers | `bcs-dist-connector-store/tests/` on `Model` + recording wrapper | claim race (one winner); `OwnedByMe`; takeover with a `Dead` fake; `PendingMembership` → `HeldByOther`; stale holder → `Lost`; every write with a lost response and with a definite failure; Worker write between read and write → merged retry; held index order; feed: group from the beginning, one consumer per entry, idle takeover, double ack, kept-alive reads; partition function |
| port harnesses | same | `dispatch_queue_port_contract_tests`, `command_feed_port_contract_tests`, `held_dispatch_port_contract_tests` (Worker side played by a test-only fixture that writes `control`) |
| engine | `bcs-dist-connector/tests/` with port fakes | the walkthrough §12.7 order; ack after the decision; final before finish; crash between admit and finish then takeover; SENT recovery per capability; reply continues the same call; cancel before / after acceptance; lost holder stops silently; drain; `dist_runtime_control_port_contract_tests` |
| end to end | `bcs-dist-connector/tests/store_e2e.rs` | engine + real providers + `StoreInboxProducer` on `Model`: one call from hint to ENDED, inbox entries checked |
| adapter | `bcs-a2a-http/tests/dist_agent.rs` with an axum fake agent | each outcome class; stable `messageId`; `dist_agent_client_port_contract_tests` |
| stubs | each crate | D2, D3, D8, membership return `need_contract_not_implemented` |

Commands: `cargo nextest run -p bcs-dist-connector -p bcs-dist-connector-store -p bcs-dist-store -p bcs-a2a-http -p bcs-app-dist-runtime -p bcs-service-api`,
`cargo check --workspace --all-targets`, `scripts/ci/arch-check.sh`.

## 9. Repository rules that apply

Filled from the architecture survey; see the end of this file.

## 10. Risks and open points

1. `PendingMembership` and the takeover branch: the stub lives in our store
   crate until the executor team's provider replaces it.
2. A connection refused before the request is written is `OutcomeUnknown` (§6).
3. `query(ByRequest)` has no A2A equivalent, so `QueryByRequest` resolves only
   with a custom binding; plain A2A agents without `idempotent_start` end
   unresolved after an unknown send.
4. `Quarantined` on a report stops the actor without finishing; D1 stubs the
   quarantine read today, so this path cannot occur yet.
5. `bcs-app-dist-runtime` may collide with the Worker's W7 crate of the same name.
6. One PR is about six small crates' worth of code; if review asks for a
   split, the order is contract + shared module → providers → engine → adapter.
