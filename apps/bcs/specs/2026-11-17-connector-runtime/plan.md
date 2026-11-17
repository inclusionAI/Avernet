# Plan: distributed Connector runtime

Status: proposed; awaiting review. Implements [spec](spec.md); store mapping in
[research](research.md). Baseline `dev` = `5c3e30ea`.

This replaces the plan of the closed PR #4111 (written before `bcs-store-api`
and `bcs-dist-store` were in `dev`). The choices reviewed there on 2026-11-17
are kept as decided and marked "reviewed"; they are not reopened.

## 1. Shape

```text
bcs-service-api (additive)
  core::dist_connector        DistConnectorCoreService: pure rules, no IO          [contract, §2]

services/bcs-dist-connector  (new)                                                 [engine, §3]
  rules      DistConnectorRules: the DistConnectorCoreService implementation
  identity   request ids, input ids, interaction ids, digests
  engine     main loop: receive → claim → ack → spawn actor; drain; snapshot
  actor      one per holder generation: send / recover / stream / control tick / finish
  control    pending_input and cancel
  delayed    report_unresolved (D2); HeldCallRecovery (local trait for D3/D8)
  report     events → InboxProducerPort (admit | append_stream), blob offload
  uses ports: DispatchQueuePort, HeldDispatchPort, CommandFeedPort, InboxProducerPort,
              DistAgentClientPort, MembershipPort, BlobStorePort
  implements: DistRuntimeControlPort

services/bcs-dist-connector-store  (new, the only new user of bcs-store-api)      [providers, §4]
  doc        stored disp document (DispatchView + connector object)
  queue      StoreDispatchQueue: DispatchQueuePort (claim, resume_owned, notifications)
  held       StoreHeldDispatch: HeldDispatchPort
  feed       StoreCommandFeed: CommandFeedPort (consumer group)
  held_index add before claim, remove after ENDED
  delayed    stub bodies of repost_held (D3) and release (D8)
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
randomness. It is a new file plus one `mod` line; no existing type changes.
**For the executor team to review.** Shape as reviewed on 2026-11-17:

```rust
/// Report position of one held call. A new cursor is made after every claim.
pub struct ReportCursor { pub holder_epoch: HolderEpoch, pub last_seq: InputSeq /* 0 = none yet */ }

pub enum ResumeStep {
    Send,                 // created / claimed, no cancel: begin_send(Start) then start
    EndCanceledUnsent,    // created / claimed with a cancel intent: nothing was sent
    ResolveStart,         // sending: the start may have reached the agent (SENT policy)
    Reattach,             // accepted: remote task id known
    Nothing,              // ended
}
pub enum UncertainSend { Resend, AskAgent, Unresolved }
pub enum Reattach { Subscribe { query_first: bool }, Poll, Unresolved }
pub enum Report { Stream(AppendAgentEvent), Key(AdmitRequest) }

pub trait DistConnectorCoreService: Send + Sync {
    fn resume_step(&self, view: &DispatchView) -> ResumeStep;
    fn start_request_id(&self, did: &Did) -> RequestId;
    fn input_request_id(&self, did: &Did, seq: InputSeq) -> RequestId;
    fn uncertain_start(&self, capabilities: &DownstreamCapabilities) -> UncertainSend;
    fn uncertain_input(&self, capabilities: &DownstreamCapabilities) -> UncertainSend;
    fn reattach(&self, capabilities: &DownstreamCapabilities) -> Reattach;
    /// Decision 29 (a)(b): assigns seq, and for key events the stable input id,
    /// interaction id and digest. Never reads a clock or the holder for identity.
    fn report(&self, view: &DispatchView, cursor: &mut ReportCursor, kind: AgentEventKind,
              remote_event_id: Option<String>, payload: Payload) -> Report;
    /// The definitive end a key event implies, if any.
    fn end_of(&self, kind: &AgentEventKind, payload: &Payload) -> Option<FinalDispatchOutcome>;
    /// Next pending input to send: lowest seq above the confirmed watermark, or none.
    fn next_input<'a>(&self, view: &'a DispatchView) -> Option<&'a PendingInput>;
}
```

The step order of one call is part of the contract (module doc) and is asserted
by tests against recording fakes:

```text
receive → claim → ack
  → actor:  begin_send(Start) → start → record_acceptance
            → report events (append_stream | admit)
            → on poll: begin_send(Input) → send_input → confirm_input
                       cancel intent → record_cancel(Sending) → cancel → record_cancel(observed)
            → admit final/error → finish
```

Rules fixed by the trait docs:

| Rule | Value |
| --- | --- |
| request id (A2A `messageId`) | start: derived from the `did`; input: from `did` and `seq`. Same value on every attempt and for every holder |
| `input_id` | `ae:` + sha256(did, "final"); `ae:` + sha256(did, "error"); `ae:` + sha256(did, kind, n) for input-required / auth-required, n = `confirmed_input_through + 1` (1 when none). Fields are length-prefixed before hashing |
| `InteractionId` | `ia:` + the same hash |
| `business_digest` | sha256 over the same identity fields plus the terminal state. The payload is **not** part of it: a new holder re-reports from `tasks/get`, whose JSON differs from the streamed event; the first admitted report wins |
| `seq` | 1 after each claim (also after `resume_owned`), +1 per reported event; key and intermediate events share the counter |
| SENT policy | start: `idempotent_start` → `Resend`; else `query_by_request` → `AskAgent`; else `Unresolved`. Input: `idempotent_input` → `Resend`; else `Unresolved` |
| re-attach | `subscribe` → `Subscribe { query_first: query_by_task }`; else `query_by_task` → `Poll`; else `Unresolved` |
| end of call | `Final` → `Completed`; `Error` with payload state `canceled` → `Canceled`; other `Error` → `Failed` |

Event payload per kind (assumed signed off by the executor team; samples in
[research §9](research.md#9-agentevent-payload-per-kind)):

| Kind | A2A source | Payload |
| --- | --- | --- |
| `delta` | artifact update with output parts | `artifact`, `append`, `last_chunk` |
| `thinking` | status update in state working that carries a message | `message` |
| `tool` | a record of the negotiated tools extension | `tool` |
| `snapshot` | a whole non-terminal task (first event of a subscribe, or a poll) | `task` |
| `input_required` / `auth_required` | status update or task in that state | `state`, `message` |
| `final` | state completed, or a direct message answer | `state`, optional `message`, optional `artifacts` |
| `error` | state failed, rejected, canceled; a definite rejection of the send; a cancel before anything was sent | `state`, `code`, optional `message` |

Known limit (tracked): two different input-required events before a confirmed
reply share one `input_id`; the second is dropped.

## 3. Engine: `crates/services/bcs-dist-connector`

### 3.1 Main loop (`engine.rs`)

```text
start: membership.register(me, Connector, 3 s) ; renew every 1 s      // provider pending: need_contract → warn once, continue
       Renewal::Lost → stop claiming, ready = false (actors continue; disp fences them)
       queue.notifications(pool, me)
loop until draining:
  free = max_active_calls − actors ; if free == 0 → wait for an actor to end
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
- Backpressure: the loop does not read hints while `max_active_calls` actors run.
- `drain(grace)`: stop reading and claiming, call `membership.set_draining`
  (stub tolerated), wait up to `grace` for actors to end, then for each
  leftover call `recovery.release` (D8 stub → logged) and return
  `DeadlineReached { remaining }`. Actors are not aborted: their calls stay
  held and recoverable.
- Config: `partitions` required, no default (§5). Defaults: `pool = a2a`,
  `max_active_calls = 1000`, `poll_interval = 1.5 s ± 20 %`,
  `max_inline_bytes = 65536`, backoff 0.2 s → 5 s, `not_sent_retries = 3`.
- `snapshot`: `active_work` = actors, `pending_effects` = unresolved calls +
  pending cancels; `ready` = feed open and not draining.

### 3.2 Actor (`actor.rs`, `sender.rs`)

On start the actor asks `rules.resume_step(held.view())`:

| Step | Actor does |
| --- | --- |
| `Send` | `begin_send(Start { request_id })`, load the request (`BlobStorePort::get` when it is a reference), `client.start(...)`, then the table below |
| `EndCanceledUnsent` | admit the error event (state canceled), `finish(Canceled, "canceled_before_send")` |
| `ResolveStart` | `rules.uncertain_start`: `Resend` → send again with the same request id, with backoff; `AskAgent` → `query(ByRequest)`: `Found` → `record_acceptance`, continue; anything else → unresolved; `Unresolved` → unresolved |
| `Reattach` | `rules.reattach`: `Subscribe { query_first }` → optional `query(ByTask)` reported as a `Snapshot` (its key state under the stable `input_id`), then `subscribe(remote, None)`; `Poll` → `query(ByTask)` every poll interval; `Unresolved` → unresolved |
| `Nothing` | exit |

| `SendOutcome` of `start` | Actor does |
| --- | --- |
| `Accepted { remote, events }` | `record_acceptance` (re-reads `disp`; a returned cancel intent is carried out at once), then consume `events`, or re-attach when there is no stream |
| `Completed { result }` | admit final, `finish(Completed, "sync_result")` |
| `Rejected { code }` | admit error (state rejected), `finish(Failed, "rejected:<code>")` |
| `NotSent { code }` | retry up to 3 times with backoff; then unresolved (not sent) |
| `OutcomeUnknown { code }` | as `ResolveStart` |

Then one select loop until the call ends or the holder is lost:

- **remote event** → `rules.report(...)` → `append_stream` or `admit`, one at a
  time, in order; the stream is not read further while a report is pending.
- **poll tick (1.5 s ± 20 %) or poke** → `held.refresh()`: `HolderError::Lost` →
  stop now, drop the stream, write nothing more; a store error → try again next
  tick; cancel intent → §3.4; `rules.next_input(view)` → §3.4.
- **stream ended or failed without a terminal event** → not an agent result
  (B11); re-attach.

Unresolved (uncertain or not sent): call the D2 stub and log its error; leave
`disp` in its phase; never `finish`; keep polling so a cancel is still seen
(not sent → `finish(Canceled)`; uncertain → `record_cancel(OutcomeUnknown)`, no
finish); count it in `pending_effects`. The actor enforces no deadline (B13).

### 3.3 Reporting (`report.rs`)

- Key events → `InboxProducerPort::admit(AdmitRequest { input, create_session: None, create_task: None })`.
  The match handles `ContentConflict` and `Quarantined` explicitly and treats
  every other `Ok(_)` as success, with a comment saying why: the code, tests
  and fakes never name the duplicate variant, so they compile before and after
  the Gateway PR (#4132) removes it. `ContentConflict` cannot happen with
  the payload-free digest; if it does it is logged as a bug and the call is not
  ended on it. `Quarantined` is logged; a terminal event still ends the call.
  `NotApplied` / `OutcomeUnknown` → retry the same envelope with backoff;
  `finish` is never called before the admit succeeded.
- Intermediate events → `append_stream`; on an error retry the same event with
  the same seq, with backoff (the Worker drops a repeat by `(holder_epoch, seq)`).
- A payload whose inline JSON exceeds `max_inline_bytes` is written with
  `BlobStorePort::put_immutable` (key `bcn/agent-event/<did>/<sha256>`) and
  reported as `Payload::Ref`.
- No batching of intermediate events (design proposal, not MVP).

### 3.4 Replies and cancels (`control.rs`)

Per tick, from `held.refresh()` (which hides pending inputs at or below
`confirmed_input_through`; removing them is the Worker's job, D5):

- Next pending input (`seq == confirmed + 1`), phase accepted, nothing being
  sent: `begin_send(Input{seq, request_id})` → `client.send_input(target, remote, …)`.
  `Accepted` → `confirm_input(seq, evidence)`, follow the returned event stream
  (or re-attach). `Completed` → confirm, admit final, `finish(Completed)`.
  `Rejected` → no confirm; D2 stub (not delivered); later inputs wait.
  `NotSent` → retry ≤ 3, then as `Rejected`. `OutcomeUnknown` →
  `idempotent_input` ? resend : D2 stub (uncertain); later inputs wait.
- Cancel intent present and no final cancel observation:
  nothing sent yet → report canceled, `finish(Canceled)`;
  no `cancel` capability → `record_cancel(Rejected { code: "cancel_unsupported" })`;
  else `record_cancel(Sending)` → `client.cancel(target, remote, intent.request_id)` →
  `record_cancel(mapped)`. `ConfirmedCanceled` → report canceled, `finish(Canceled)`.
  `AlreadyFinished(outcome)` → query the task, admit its terminal event,
  `finish(outcome)`. `RequestAccepted` → keep consuming until the agent ends.
  `OutcomeUnknown` → retried on later ticks.

### 3.5 Delayed-contract stubs in the engine

| Method | Item | Returns | Caller |
| --- | --- | --- | --- |
| `report_unresolved(call, Uncertain \| NotSent) -> Result<AdmitOutcome, StoreError>` (`delayed.rs`) | D2 | `StoreError::NotApplied { code: "need_contract_not_implemented" }` | logs, leaves the phase |
| `HeldCallRecovery::{repost_held, release}` (trait local to this crate) | D3, D8 | same; the stub bodies live in the store crate (§4.4) | `drain` calls `release` per leftover and logs; nothing calls `repost_held` in the MVP |

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
`held / 1.5` projection reads per second per Connector, bounded by `max_active_calls`.
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
                    Set /connector {input_sending: kept, finish_evidence: kept}],
              Preserve)
     Applied          → Acquired(StoreHeldDispatch)
     ConditionNotMet  → loop (re-read, re-decide)
     OutcomeUnknown   → re-read: holder is {me, e} → Acquired; else re-decide;
                        re-read fails → StoreError::OutcomeUnknown (not acked)
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
| `begin_send(Start)` | phase claimed | phase sending | already sending → fenced read only, same permit |
| `begin_send(Input{seq})` | phase accepted; `seq == confirmed + 1`; input pending | `connector.input_sending = seq` | same |
| `record_acceptance` | phase sending or accepted | phase accepted; `remote`; returns `control.cancel` from the same read | same remote → no write |
| `confirm_input(seq)` | `seq == confirmed + 1` | `confirmed_input_through = seq` | `seq ≤ confirmed` → `AlreadyApplied` |
| `record_cancel(obs)` | forward only: `not_sent` → `sending` → an observed result | `cancellation = obs` | equal → `AlreadyApplied` |
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

### 4.4 Delayed-contract stubs (`delayed.rs`)

`repost_held(dead: &Instance, page) -> Result<Page<Did>, StoreError>` (D3) and
`release(did, holder: &Holder) -> Result<(), StoreError>` (D8), both returning
`error::need_contract()`, each with a doc comment naming its §16 item and a
test. The trait the engine calls (`HeldCallRecovery`) stays local to
`bcs-dist-connector`; a thin wrapper joins the two at composition. The held
index itself is real.

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
| resolver, URL guard, credential or client construction fails | `NotSent` |
| the connection could not be established: DNS failure, connection refused, connect timeout (reqwest `is_connect()`; for gRPC a connect error raised before the request is sent) | `NotSent` |
| HTTP 400/401/403/404/405/406/415/422, gRPC 3/5/7/12/16 | `Rejected` |
| everything else once the request was handed to the transport (timeout, reset or close after the request was read, 408/409/429/5xx, JSON-RPC errors, stream ended before the first event) | `OutcomeUnknown` |

Why the second row is in this PR: a plain A2A agent declares neither
`idempotent_start` nor `query_by_request`, so without it a brief network
failure on the first send would leave the call unresolved for good. With it,
that failure goes through the 3-retry path.

Change to the shared low-level clients, kept narrow: today
`client_json.rs`, `jsonrpc.rs` and `subscribe.rs` turn every send error into
`A2AError::internal(<fixed message>)` with `map_err(|_| …)`, which drops the
"nothing was written" fact. The send step keeps that fact by returning a
distinct fixed message for a connect-phase error (`A2A connection not
established`), chosen from `reqwest::Error::is_connect()`; `grpc_client.rs`
does the same for a transport connect error. The adapter matches on that
marker, the same way `initial_rejection` matches its own `A2A HTTP ` /
`A2A gRPC ` markers. Existing delivery code is unaffected: it treats any
message outside its rejection table as uncertain, so the new message stays
uncertain there. Because this code is linked into the `bcs` binary, the
changed lines are covered by tests in this PR (unit tests per binding, plus
the adapter tests below) and by the existing delivery suites.

Adapter tests for this table: refused port → `NotSent`; unresolvable host →
`NotSent`; server closes after reading the request → `OutcomeUnknown`; each
rejection status → `Rejected`. No retry inside the adapter.

Event mapping (same table as §2):

| A2A event | `AgentEventKind` |
| --- | --- |
| artifact update with output parts | `Delta` |
| status update in state working that carries a message (other than tool records) | `Thinking` |
| a record of the negotiated tools extension | `Tool` |
| a whole non-terminal `Task` (first event of a subscribe, or a poll) | `Snapshot` |
| status update `submitted` / `working` without content | nothing |
| `input-required` | `InputRequired` |
| `auth-required` | `AuthRequired` |
| `completed`, or a direct message answer | `Final` |
| `failed`, `canceled`, `rejected` | `Error`, with the state in the payload |

The interaction id in the adapter's `AgentEventKind` is a placeholder; the
rules replace it (§2). `event_id` and `cursor` are `None` (the SSE decoder
drops `id:` lines).

New dependency: `bcs-a2a-http` → `bcs-dist-model` (optional, with the `dist-agent` feature, §9).

## 7. Admin (C6)

- Engine implements `DistRuntimeControlPort` (`snapshot`, `drain`).
- `crates/application/bcs-app-dist-runtime`: `ConnectorRuntimeFacade`
  implements `DistConnectorRuntimeService` over `Arc<dyn DistRuntimeControlPort>`,
  translating the port result into the application result. Task W7 names the
  same crate for the Worker's facade; whoever lands second adds a file.

## 8. Tests

| Level | Where | What |
| --- | --- | --- |
| rules | `bcs-dist-connector/tests/rules.rs` | fixed vectors for which `identity.rs` and `bcs_dist_store::identity` (a dev-dependency) produce the same bytes, so the two copies cannot drift; identities stable across holder epochs and without event ids; digest excludes holder/transport data; every SENT-policy row; not-sent ×3; terminal mapping |
| providers | `bcs-dist-connector-store/tests/` on `Model` + recording wrapper | claim race (one winner); `OwnedByMe`; takeover with a `Dead` fake; `PendingMembership` → `HeldByOther`; stale holder → `Lost`; every write with a lost response and with a definite failure; Worker write between read and write → merged retry; held index order; feed: group from the beginning, one consumer per entry, idle takeover, double ack, kept-alive reads; partition function |
| port harnesses | same | `dispatch_queue_port_contract_tests`, `command_feed_port_contract_tests`, `held_dispatch_port_contract_tests` (Worker side played by a test-only fixture that writes `control`) |
| engine | `bcs-dist-connector/tests/` with port fakes | the §2 step order; ack after the decision; final before finish; crash between admit and finish then takeover; SENT recovery per capability; reply continues the same call; cancel before / after acceptance; lost holder stops silently; drain; `dist_runtime_control_port_contract_tests` |
| end to end | `bcs-dist-connector/tests/store_e2e.rs` | engine + real providers + `StoreInboxProducer` on `Model`: one call from hint to ENDED, inbox entries checked |
| adapter | `bcs-a2a-http/tests/dist_agent.rs` with an axum fake agent | each outcome class, including refused port → `NotSent` and close-after-read → `OutcomeUnknown`; the event mapping table; stable `messageId`; `dist_agent_client_port_contract_tests` |
| stubs | each crate | D2, D3, D8, membership return `need_contract_not_implemented` |

Commands: `cargo nextest run -p bcs-dist-connector -p bcs-dist-connector-store -p bcs-dist-store -p bcs-a2a-http -p bcs-app-dist-runtime -p bcs-service-api`,
`cargo check --workspace --all-targets`, `scripts/ci/arch-check.sh`.

## 9. Repository rules that apply

| Rule | What this change does |
| --- | --- |
| Workspace members and `[workspace.dependencies]` are explicit lists; CI builds `--locked` | one line each per new crate; `Cargo.lock` committed |
| LINT-4: traits under `core/` end in `Service`; CLAUDE.md: `*CoreService` | `DistConnectorCoreService`; no other `pub trait` in the file |
| LINT-2: a public name under `core/` may not appear as a word under `port/` | new type names checked against `port/` before commit (`Report`, `ResumeStep`, …) |
| LINT-1: `services/*` may not import `application::`; `external-clients/*` only `port::` / types | engine implements `DistRuntimeControlPort` only; the facade lives in the application crate; the adapter imports `port::dist_agent` |
| `r4_default_impl_inventory`: no default async bodies in `core/` | the trait has none (it is sync) |
| API-1: a public-API diff of `bcs-service-api` needs a version change | `6.1.0` → `6.2.0` (additive) and a "Version 6.2.0" section in its `CONTEXT.md`. The Gateway PR (#4132) removes `AdmitOutcome::Duplicate` and bumps to `7.0.0`; whichever lands second rebases: if the Gateway lands first, this becomes `7.1.0` with its own `CONTEXT.md` section |
| R25.1: every `pub trait …(CoreService\|Service\|Port\|…)` under `service-api`, `plugin-api`, `services` needs `<snake>_contract_tests` in `bcs-test-support` | add `dist_connector_core_service_contract_tests`, exercised by a test. The engine-local D3/D8 trait is named `HeldCallRecovery` (no `Port` suffix): it only fronts two stubs and `bcs-test-support` must not depend on the engine |
| R25.2: every `impl …Port/CoreService for T` under `crates/services` needs a `tests/conformance_*.rs` naming `T` and the harness | `conformance_rules.rs`, `conformance_runtime_control.rs` (engine); `conformance_dispatch_board.rs` (store). `PendingMembership` carries `CONFORMANCE_WAIVED` with the reason (a stub cannot pass a provider harness) |
| R10: store plugins only in `*-store` crates; the script scans a fixed list | add `crates/services/bcs-dist-connector/src` to `check-store-boundaries.sh` and `tests/store_api_boundary.sh` |
| Ports only in a service crate; concrete stores chosen at composition | engine normal deps: `bcs-service-api`, `bcs-dist-model` (+ async/runtime crates). Hashing is a small local `identity.rs` using the same length-prefixed sha256 as `bcs_dist_store::identity`; the store crates are dev-dependencies for the end-to-end test |
| DEP-8: no unused dependencies (`cargo machete`) | manifests list only what is used |
| LINT-3: no `std::env::var` in services | configuration is passed in as structs |
| Workspace lints: `unsafe_code`, `unused_qualifications` deny; clippy `unwrap_used`, `expect_used` deny | `[lints] workspace = true`; tests opt out locally as existing tests do |
| Unit-test gate on PRs to `dev`: 100 % pass, changed-line coverage > 85 %, nextest kills a test after 90 s | loop/actor tests use paused time and finish in milliseconds; every branch of §3–§6 has a test |
| Singlebox E2E changed-line gate (> 65 %) counts code linked into the `bcs` binary | the adapter sits behind a cargo feature `dist-agent`, off by default, so the binary does not contain it. The connect-marker change in the low-level clients (§6) is linked: it is a few lines per binding on paths the existing delivery E2E already runs. To verify on the first CI run |
| 1,000-line file limit | modules split as in §1; checked with `wc -l` before each push |
| No global formatter | only new files and the touched lines |
| PR to `dev` runs the unit and singlebox gates; architecture checks run on `main` | `BCS_BASE_REF=origin/dev bash scripts/ci/arch-check.sh` is run locally; checks that already fail on `dev` (LINT-1, LINT-4, DEP-1, DEP-6 outliers) are reported, not touched |
| PR description | repository template, plus the BCS architecture fields the `main` gate reads (contract direction, affected consumers) because `bcs-service-api` changes |
| Knowledge | `specs/README.md` entry; crate rows in `apps/bcs/README.md` "Crate Layout"; `validation.md` with the commands actually run |

## 10. Risks and open points

1. `PendingMembership` and the takeover branch: the stub lives in our store
   crate until the executor team's provider replaces it.
2. Telling "could not connect" from "failed mid-request" needs a small change
   in the shared low-level A2A clients (§6). It is linked into the `bcs`
   binary, so its changed lines must be covered here.
3. `query(ByRequest)` has no A2A equivalent, so `AskAgent` resolves only with
   a custom binding. A plain A2A agent without `idempotent_start` is unresolved
   only when a send fails **after** the request was handed to the transport;
   connect failures are retried (§6).
4. Until D3 exists, an ACCEPTED call whose holder died is picked up again only
   if the Worker's deadline produces a cancel or a re-post for an ACCEPTED
   call. The design text only describes a re-post for calls that are not
   ACCEPTED. Question for the executor team.
5. `bcs-app-dist-runtime` may collide with the Worker's W7 crate of the same name.
6. One PR is about six small crates' worth of code; if review asks for a
   split, the order is contract + shared module → providers → engine → adapter.
