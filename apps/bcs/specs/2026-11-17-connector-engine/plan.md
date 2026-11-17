# Connector — technical plan

Status: draft for review. **Stop point: nothing is implemented until this plan is
approved.** Spec: [spec.md](spec.md). Evidence: [research.md](research.md).

Items marked **(decide)** need an answer from the owner; they are collected in §13
with a recommendation each. Everything else is a choice made here and open to
change.

## 1. Shape

```text
bcs-app-dist-runtime (application)      DistConnectorRuntimeService: snapshot, drain
        │ calls DistRuntimeControlPort
bcs-dist-connector (service crate)      rules (pure) + engine (loop, actors) — no storage plugin
        │ uses ports only
        ├─ DispatchQueuePort / CommandFeedPort / HeldDispatchPort ── bcs-dist-connector-store (store crate)
        │                                                              └─ bcs-store-api ── TBase plugin (internal)
        ├─ InboxProducerPort ── shared admission provider (Gateway session)
        ├─ DistAgentClientPort ── bcs-a2a-http (adapter over the existing client)
        ├─ MembershipPort, BlobStorePort ── providers not owned here
```

| Crate | New or changed | Holds | Task |
| --- | --- | --- | --- |
| `crates/services/bcs-dist-connector` | new | pure rules, engine, per-call actor, reporting, delayed stubs | C1, C3, C4, C5, C6 (engine part) |
| `crates/external-clients/bcs-a2a-http` | changed (new module `dist_agent/`) | `DistAgentClientPort` adapter | C2 |
| `crates/services/bcs-dist-connector-store` | new | TBase providers for the three dispatch ports, held index | provider part of C1 |
| `crates/application/bcs-app-dist-runtime` | new, or add a module if the executor team created it first | admin facade | C6 |
| `crates/service-api/bcs-service-api` | **(decide)** additive only | Connector core contract, recovery port | — |

Workspace `Cargo.toml`: one `members` line and one `[workspace.dependencies]` line
per new crate, nothing else.

## 2. Connector core service contract (first deliverable)

Like the Worker's `DistRunnerCoreService`, the contract is a **pure** trait: the
rules, with no IO. The engine (§3) does the IO in the order the rules give. This
keeps the BCS rule that a core contract does not call external ports, and makes
every rule testable without a store or an agent.

Proposed home **(decide)**: `bcs_service_api::core::dist_connector`, next to
`dist_gateway.rs` and `dist_runner.rs`. It is a new file plus one `mod` line and
touches no existing type. Fallback if `bcs-service-api` must stay untouched: the
same trait inside `bcs-dist-connector`.

```rust
/// Report position of one held call. A new cursor is made after every claim.
pub struct ReportCursor { pub holder_epoch: HolderEpoch, pub last_seq: InputSeq /* 0 = none yet */ }

pub enum ResumeStep {
    Send,                 // created / claimed, no cancel: begin_send(Start) then start
    EndCanceledUnsent,    // created / claimed with a cancel intent: nothing was sent
    ResolveStart,         // sending: the start may have reached the agent (§6)
    Reattach,             // accepted: remote task id known (§7)
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
    /// The definitive end a key event implies, if any (final → completed, …).
    fn end_of(&self, kind: &AgentEventKind, payload: &Payload) -> Option<FinalDispatchOutcome>;
    /// Next pending input to send: lowest seq above the confirmed watermark, or none.
    fn next_input<'a>(&self, view: &'a DispatchView) -> Option<&'a PendingInput>;
}
```

The step order for one call is part of the contract and is asserted by tests
against recording fakes:

```text
receive → claim → ack
  → actor:  begin_send(Start) → start → record_acceptance
            → report events (append_stream | admit)
            → on poll: begin_send(Input) → send_input → confirm_input
                       cancel intent → record_cancel(Sending) → cancel → record_cancel(observed)
            → admit final/error → finish
```

## 3. Engine (`bcs-dist-connector`)

```text
src/lib.rs
    config.rs      ConnectorConfig
    rules.rs       DistConnectorCoreService impl
    identity.rs    request ids, input ids, interaction ids, digests
    engine.rs      ConnectorEngine: run loop, actor registry, drain; impl DistRuntimeControlPort
    actor.rs       one held call: state, select loop
    sender.rs      start, SENT policy, re-attach
    control.rs     poll disp: pending inputs, cancel
    report.rs      append_stream / admit with retry, blob offload
    delayed.rs     D2 / D3 / D8 stubs
tests/             recording fakes of the ports, scripted agent, scenarios
```

Main loop:

```text
feed = queue.notifications(pool, me)
loop while not draining:
    wait for free capacity (max_active_calls)          # at capacity: stop reading hints, others take them
    for delivery in feed.receive(max, 1 s):            # claims run with bounded concurrency
        match queue.claim({ did, me, request_id }):
            Acquired(held)        → spawn actor
            OwnedByMe(holder)     → actor running: nudge it to refresh now
                                    no actor:      resume_owned({ did, previous: holder }) → spawn actor
            HeldByOther | Ended | NotFound → nothing
            Err(store error)      → no ack; the hint stays pending and comes back through claim_idle
        feed.ack(delivery)
    reap finished actors: a panicked actor → resume_owned → new actor
```

- Membership: register as `RuntimeRole::Connector`, renew every second (TTL 3 s).
  `Renewal::Lost` → stop claiming, `ready = false`; running actors continue and are
  fenced by `disp` if someone takes their calls. The process must restart with a new
  incarnation.
- `Instance.incarnation` = process start time in epoch ms; the consumer name is
  `<node>:<incarnation>`.
- Drain: stop the loop and the feed readers, `set_draining`, wait until no actor
  runs or the grace period ends, then `Drained` / `DeadlineReached { remaining }`.
  For each call left, call the D8 release stub and log its error.
- Snapshot: `active_work` = running actors; `pending_effects` = unresolved calls
  (§6) plus cancels whose outcome is unknown.

Config (defaults): `pool = a2a`, `max_active_calls = 1000`, `claim_concurrency = 16`,
`poll_interval = 1.5 s ± 20 %`, `max_inline_bytes = 65536`, safe-retry backoff
0.2 s → 5 s, `not_sent_retries = 3`.

## 4. The per-call actor

On start the actor asks `rules.resume_step(held.view())`:

| Step | Actor does |
| --- | --- |
| `Send` | `begin_send(Start { request_id })`, load the request (`BlobStorePort::get` when it is a reference), `client.start(...)`, then the table below |
| `EndCanceledUnsent` | admit the error event (state canceled), `finish(Canceled, "canceled_before_send")` |
| `ResolveStart` | §6 |
| `Reattach` | §7 |
| `Nothing` | exit |

Outcome of `start`:

| `SendOutcome` | Actor does |
| --- | --- |
| `Accepted { remote, events }` | `record_acceptance` (this re-reads `disp`; a returned cancel intent is carried out at once), then consume `events`, or re-attach (§7) when there is no stream |
| `Completed { result }` | admit final, `finish(Completed, "sync_result")` |
| `Rejected { code }` | admit error, `finish(Failed, "rejected:<code>")` |
| `NotSent { code }` | retry up to `not_sent_retries` with backoff (safe: nothing left the process); then unresolved, not sent (§6) |
| `OutcomeUnknown { code }` | §6 |

Then one select loop until the call ends or the holder is lost:

- **remote event** → `rules.report(...)` → `append_stream` or `admit` (§5). Events of
  one call are reported one at a time, in order; the stream is not read further
  while a report is pending.
- **poll tick or nudge** → `held.refresh()`:
  - `HolderError::Lost` → stop now, drop the stream, report nothing more, remove the
    `did` from the held index.
  - a store error → keep going, try again next tick.
  - cancel intent not yet acted on → cancel flow (§8).
  - `rules.next_input(view)` → input flow (§8).
- **stream ended or failed without a terminal event** → not an agent result (R10);
  re-attach (§7).

## 5. Reporting

### 5.1 Intermediate events (delta, thinking, tool, snapshot)

`InboxProducerPort::append_stream(AppendAgentEvent { sid, tid, event })` with
`event = { did, holder_epoch, seq, kind, remote_event_id, payload }`. On `NotApplied`
or `OutcomeUnknown`: retry the same event with the same seq, with backoff. A
repeat is dropped by the Worker by `(holder_epoch, seq)`.

### 5.2 Key events (input required, auth required, final, error)

`InboxProducerPort::admit(AdmitRequest { input, create_session: None, create_task: None })`.

| Field | Value |
| --- | --- |
| `input_id` (final) | `ae:` + sha256(did, "final") |
| `input_id` (error) | `ae:` + sha256(did, "error") |
| `input_id` (input / auth required) | `ae:` + sha256(did, kind, n) |
| `InteractionId` | `ia:` + the same hash |
| `business_digest` **(decide)** | sha256 over the same identity fields plus the terminal state; the payload is not part of it |
| `body` | `InputBody::Agent { event }`, the event carrying `(did, holder_epoch, seq)` |

- Why the digest leaves the payload out: a new holder re-reports a key event from
  `tasks/get`, whose JSON differs from the streamed event the former holder saw.
  With a payload digest the second report would be a content conflict instead of
  "same digest = accepted" (decision 24). The first admitted report wins.
- `Accepted` and `Duplicate` are both success. `ContentConflict` cannot happen with
  this digest; if it does, it is logged as a bug and the call is not ended on it.
  `Quarantined` is logged; a terminal event still ends the call.
- `NotApplied` / `OutcomeUnknown`: retry the same envelope. `finish` is never called
  before the admit succeeded.

### 5.3 The "n" of input / auth required **(decide)**

`n = confirmed_input_through + 1` at the time of the event (1 before any reply was
confirmed). Reason: a new holder must compute the same n as the former one, and the
confirmed-input watermark is the only durable count in `disp`. Consequence: an agent
that repeats its input-required status without a reply in between produces the same
`input_id` (one input), which is the wanted result.

### 5.4 Mapping A2A states **(decide)**

| Remote | `AgentEventKind` | End of call |
| --- | --- | --- |
| artifact update / message part | `Delta` (text), `Tool`, `Thinking` | — |
| task snapshot (first event of a subscribe, or a poll) | `Snapshot` | — |
| `input-required` | `InputRequired` | — |
| `auth-required` | `AuthRequired` | — |
| `completed` | `Final` | `Completed` |
| `failed`, `rejected` | `Error` | `Failed` |
| `canceled` | `Error`, payload state `canceled` | `Canceled` |

The adapter (§9) sets the kind; the interaction id it puts in `InputRequired` /
`AuthRequired` is a placeholder that `rules.report` replaces. Payloads are the
normalized A2A JSON of the event (parts, artifact id and append flag for deltas;
the task status and artifacts for terminal events). The exact payload schema per
kind is the Worker's to confirm.

### 5.5 Large content

A payload whose inline JSON exceeds `max_inline_bytes` is written with
`BlobStorePort::put_immutable` (key `bcn/agent-event/<did>/<sha256>`) and reported as
`Payload::Ref`.

## 6. SENT policy **(decide)**

Applies to a start whose outcome is unknown, and to a call found in phase `sending`
after a takeover or resume. Same rule for an input, with `idempotent_input`.

| Capability | Action |
| --- | --- |
| `idempotent_start` | resend with the same request id (A2A `messageId`), with backoff |
| else `query_by_request` | `query(ByRequest(request_id))`: `Found` → `record_acceptance`, continue; anything else → unresolved |
| else | unresolved |

Unresolved (uncertain or not sent):

1. call the D2 report stub; it returns `need_contract_not_implemented`; log it;
2. leave `disp` in its phase (`sending`); never `finish`;
3. keep the actor polling `disp`, so a cancel from the Worker's deadline is still
   seen. On that cancel: not sent → `finish(Canceled)`; uncertain →
   `record_cancel(OutcomeUnknown)`, no finish (unknown is never promoted to an end);
4. count it in `pending_effects`.

A re-post by the Worker reaches some Connector: the holder gets `OwnedByMe` and is
nudged; another Connector gets `HeldByOther` and drops it. Neither sends again.

## 7. Re-attach (takeover, resume, or a stream that broke)

| Capabilities | Action |
| --- | --- |
| `subscribe` | `query(ByTask)` first when `query_by_task` (report it as a `Snapshot`; report its key state under the stable `input_id`), then `subscribe(remote, None)` and consume |
| only `query_by_task` | poll `query(ByTask)` every poll interval: report a `Snapshot` when it changed, key states under the stable `input_id` |
| neither | unresolved (§6 steps 1–4) |

No replay cursor is stored in `disp`, so `subscribe` is always called with
`after = None`; `resume_events` is not used in the MVP. Events between the former
holder's last report and the new subscription are covered by the snapshot, and the
Worker drops the former holder's intermediate events by epoch.

## 8. Control: cancel and human reply

Cancel (intent present, observation still `NotSent`):

| Situation | Action |
| --- | --- |
| nothing sent yet | admit error (state canceled), `finish(Canceled)` |
| remote task known, `cancel` capability | `record_cancel(Sending)` → `client.cancel(remote, intent.request_id)` → `record_cancel(observed)`; `ConfirmedCanceled` → admit error (state canceled), `finish(Canceled)`; `AlreadyFinished(outcome)` → query the task, admit its terminal event, `finish(outcome)`; `RequestAccepted` → keep consuming until the agent ends |
| remote task known, no `cancel` capability | `record_cancel(Rejected { code: "cancel_unsupported" })`, keep running |
| send unresolved | §6 step 3 |

Human reply (lowest pending seq above the watermark, one at a time):
`begin_send(Input { seq, request_id })` → `client.send_input(remote, …)` →

| `SendOutcome` | Action |
| --- | --- |
| `Accepted` | `confirm_input(seq, evidence)`; consume the new stream if one came back |
| `Completed` | `confirm_input`, admit final, `finish(Completed)` |
| `Rejected` | no confirm; D2 stub (not delivered); stop sending later inputs |
| `NotSent` | bounded retry, then as `Rejected` |
| `OutcomeUnknown` | `idempotent_input` → resend; else D2 stub (uncertain); stop sending later inputs |

Removing delivered entries from `pending_input` is the Worker's (D5).

## 9. A2A client adapter (C2, `bcs-a2a-http/src/dist_agent/`)

`DistAgentA2aClient` implements `DistAgentClientPort` over the crate's existing
`BindingClient`. It adds no retry.

| Port method | A2A call | Notes |
| --- | --- | --- |
| `start` | `SendStreamingMessage` (or `SendMessage` when the card has no streaming) | `messageId = request_id` |
| `send_input` | the same, with `taskId` / `contextId` of the remote task | `messageId = request_id` |
| `query(ByTask)` | `GetTask` | `ByRequest` → `Unsupported` (no standard A2A method) |
| `subscribe` | `SubscribeToTask` | `after` is ignored unless the binding supports replay |
| `cancel` | `CancelTask` | task state in the answer decides `ConfirmedCanceled` / `RequestAccepted` / `AlreadyFinished` |

Outcome classification for a send:

| What happened | Outcome |
| --- | --- |
| target not resolvable, credential missing, request cannot be built, DNS or connect failure before any byte of the request is written | `NotSent` |
| complete rejection: HTTP 400/401/403/404/405/406/415/422, gRPC 3/5/7/12/16, or an A2A protocol error answer (reuses `delivery_error::initial_rejection`'s table) | `Rejected` |
| first stream event or response carries a task | `Accepted { remote, events }` |
| response is a direct message, no task | `Completed { result }` |
| anything else: timeout, reset, 408/409/429, 5xx, malformed answer | `OutcomeUnknown` |

- `NotSent` needs a typed marker at the connect step; today a connect failure and a
  mid-request failure both become `A2AError::internal`. Where the transport cannot
  prove nothing was written, the outcome is `OutcomeUnknown`.
- Event normalization: `StreamResponse` → `RemoteEvent { remote, event_id, cursor:
  None, kind, payload }` per §5.4. Stream read failures become `AgentReadError`
  with a machine code only (no remote text).
- Target resolution **(decide)**: `AgentBinding { agent, binding_version,
  credential_ref }` does not say where the agent is or how to authenticate. The
  adapter takes an injected resolver,
  `resolve(&AgentBinding) -> Result<ResolvedAgent { interface, bearer, extensions, route_fingerprint }, code>`,
  defined in the adapter module. No implementation of it is written until the two
  walkthrough §17 follow-ups are answered; tests use a fixed resolver.
- Tests: a local axum agent with scripted timing, drops and rejections, one case
  per row above, plus stable `messageId` across a resend.

## 10. TBase providers (`bcs-dist-connector-store`)

Blocked until the Gateway session's shared key and ts helpers are in `dev`. No key
format or ts arithmetic is written here; both are imported.

```text
src/lib.rs          TbaseDispatchBoard::new(json, zset, stream, membership, config) -> Result (capability check)
    disp_doc.rs     DispatchView <-> stored JSON, pointers, holder check
    claim.rs        DispatchQueuePort
    held.rs         HeldDispatchPort
    feed.rs         CommandFeedPort (reader task per partition, claim_idle tick)
    held_index.rs   add / remove / list
    delayed.rs      D3 repost, D8 release (stubs)
tests/              conformance against the in-memory store model + a test-only Worker-side writer
```

Stored `disp` **(decide)**: the JSON of the merged `DispatchView`
(`{ spec, control, execution }`), plus one Connector-owned sibling object:

```json
{ "spec": {…}, "control": {…}, "execution": {…},
  "connector": { "input_sending": 2, "finish_evidence": "sync_result" } }
```

- `input_sending`: highest pending-input seq a send was started for. A later holder
  treats seq ≤ this and above the watermark as "maybe sent" (§6), anything above as
  "never sent".
- `finish_evidence`: the string given to `finish`, for the Worker's
  `SupersededTerminal`.
- Both sides write by path, so neither overwrites the other's fields.

`claim(request)`:

```text
held_index.add(did)                                   # before the claim
doc = get_json(disp)            None → remove from index, NotFound
phase ended                     → remove from index, Ended
holder is me (node + incarnation) → OwnedByMe(holder)
holder is someone else          → membership.liveness: Dead → continue
                                   Alive | Unknown → remove from index, HeldByOther(holder)
epoch' = holder epoch + 1  (1 when there was no holder)
mutate_json(Newer(epoch' × 10^9), Patch[ /execution/holder = {me, epoch'},
                                         /execution/phase = "claimed" only if it was "created" ], Preserve)
    Applied          → Acquired(handle)
    ConditionNotMet  → re-read and classify (someone else won)
    OutcomeUnknown   → re-read: holder is {me, epoch'} → Acquired; else classify;
                       re-read fails → StoreError::OutcomeUnknown
```

- A takeover keeps the phase: `sending` stays `sending` (§6), `accepted` stays
  `accepted` with its remote task id (§7).
- `resume_owned`: requires the stored holder to equal `previous`; then the same
  write with epoch + 1 and the same instance.
- `request_id` is not stored: `{instance, epoch}` already identifies the claim.

Every `HeldDispatchPort` write (bounded retries):

```text
doc = get_json(disp)             missing or holder != {me, my epoch} → HolderError::Lost
check the transition             → InvalidTransition { code } / AlreadyApplied (no write)
mutate_json(Newer(read + 1), Patch[...], Preserve)
    ConditionNotMet → loop (re-read, re-check, merge)
    OutcomeUnknown  → re-read; my fields present and holder still me → applied; else loop
```

| Method | Allowed from | Writes |
| --- | --- | --- |
| `begin_send(Start)` | `claimed`; `sending` (no write, fenced read only) | phase = `sending` |
| `begin_send(Input { seq })` | `accepted`, seq = watermark + 1 | `connector.input_sending = seq` |
| `record_acceptance` | `sending`; `accepted` with the same remote (already applied) | phase = `accepted`, remote; returns `control.cancel` from the same read |
| `confirm_input(seq)` | seq = watermark + 1; lower → already applied; higher → `input_gap` | `confirmed_input_through = seq` |
| `record_cancel` | forward only: `not_sent` → `sending` → an observed result | `cancellation` |
| `finish` | `claimed`, `sending`, `accepted`; `ended` with the same outcome → already applied | phase = `ended`, outcome, evidence; then `held_index.remove(did)` |

`CommandFeedPort`:

- `notifications(pool, me)`: for each partition `create_group(key, "connectors",
  Beginning)`; start one reader task per partition (`read_group`, block 1 s) feeding
  one bounded channel; start a `claim_idle` tick (every 10 s, `min_idle` 30 s,
  paged). A group read is never cancelled.
- `receive(max, timeout)`: drain up to `max` from the channel, waiting at most
  `timeout`.
- `ack`: `ack(key, "connectors", [id])`; `opaque` = `<partition>:<major>-<minor>`.
- An entry without a `did` field is acked and logged.
- Partitions: `partitions = 1` by default (`p0`); how the Worker picks a partition
  for a `did` is the Worker's.

Tests need (a) an in-memory `bcs-store-api` model and (b) something that plays the
Worker on `disp` (create, cancel, push input, notify). (b) is a small test-only
writer in this crate's `tests/`. For (a) **(decide)**: reuse the conformance model
that lives in `bcs-test-support/tests/support/store/` by a `#[path]` include, or
have the Gateway session's shared PR export it from `bcs-test-support`.

## 11. Delayed contracts (stubs)

Each returns the port-level `StoreError::NotApplied { code: "need_contract_not_implemented" }`,
has a doc comment naming the §16 item, and a test asserting the error.

| Item | Method | Where | Caller meanwhile |
| --- | --- | --- | --- |
| D2 | `report_unresolved(call, Uncertain \| NotSent) -> Result<AdmitOutcome, StoreError>` | `bcs-dist-connector/src/delayed.rs` | logs; leaves the call in its phase (§6) |
| D3 | `repost_held(dead: &Instance, page) -> Result<Page<Did>, StoreError>` | store crate `delayed.rs` | not called by a loop in the MVP; the held index is still maintained |
| D8 | `release(did, holder: &Holder) -> Result<(), StoreError>` | store crate `delayed.rs` | `drain` calls it for each leftover and logs |

D3 and D8 are storage steps, so the engine reaches them through a small port,
`DispatchRecoveryPort` **(decide)**: in
`bcs_service_api::port::repo::distributed::dispatch` (additive), or local to
`bcs-dist-connector` until the contracts are final. D5 and D7 need no code here.

## 12. Order of work and pull requests

| # | PR | Needs | Contains |
| --- | --- | --- | --- |
| 1 | `docs(bcs): specify the distributed Connector` | — | this folder |
| 2 | `feat(bcs): add the Connector core contract and engine` | plan approved; §13 items 1, 3, 6, 7 | §2–§8, §11 (D2), tests with fakes |
| 3 | `feat(bcs): adapt the A2A client to the distributed agent port` | plan approved; §13 item 2 for the resolver only | §9 |
| 4 | `feat(bcs): add TBase providers for the dispatch board` | Gateway shared PR in `dev`; §13 items 4, 5, 8 | §10, §11 (D3, D8), R10 list |
| 5 | `feat(bcs): expose Connector health and drain` | 2 | admin facade, composition |

2 and 3 are independent and need no TBase. Each PR: `cargo nextest run` for the
touched crates, `scripts/ci/arch-check.sh`, line counts, no global `cargo fmt`.

Left out on purpose: batching intermediate events (design proposal, about 200 ms
per call) because it changes the payload the Worker reads; storing a replay cursor
for `resume_events`; a supervisor loop (D3); SSRF policy (decision 19).

## 13. Decisions needed from the owner

| # | Question | Recommendation |
| --- | --- | --- |
| 1 | SENT policy (§6) | As written: resend only with `idempotent_start` / `idempotent_input`; else ask the agent when `query_by_request`; else unresolved. A definite "not sent" is retried 3 times, then unresolved |
| 2 | What resolves `CredentialRef`, what `BindingVersion` means, and where the agent's interface (URL, binding) comes from (§9) | Not guessed. The adapter takes an injected resolver; its implementation waits for the answer |
| 3 | May `bcs-service-api` get two additive, Connector-only items: `core/dist_connector.rs` (§2) and `DispatchRecoveryPort` (§11)? | Yes: it is where the Gateway and Worker contracts live, and no existing type changes. Otherwise both stay local to `bcs-dist-connector` |
| 4 | Stored `disp` layout and the two Connector-owned fields (§10). Needs the executor side's agreement, since their provider creates the document | `DispatchView` JSON plus a `connector` sibling object. Alternative: add the two fields to `DispatchExecution` (a contract change) |
| 5 | Who implements the TBase `MembershipPort` (heartbeats A/B, member record, liveness; decision 15's "judge others only after 3 s of own renewals")? The claim and the Connector's own heartbeat need it | The Gateway session's shared module, since all three roles use it; I code against the trait. I can take it if that is preferred |
| 6 | Key-event `business_digest` without the payload (§5.2), and n = watermark + 1 (§5.3) | As written |
| 7 | Mapping of A2A terminal states and the payload per event kind (§5.4): canceled and rejected as `Error`; payload = normalized A2A JSON | As written, to be confirmed with the executor side because their reducer reads it |
| 8 | In-memory `bcs-store-api` model for provider tests (§10), and the name of the crate holding the shared key/ts helpers | Export the model from `bcs-test-support` in the shared PR; until then a `#[path]` include |

Known limits to accept or object to:

- Until D3 exists, an ACCEPTED call whose holder died waits for the Worker's
  deadline (research §2 item 9).
- An unresolved call occupies one of `max_active_calls` until the Worker cancels it.
- Polling costs one read per held call per 1.5 s (research §4).

## 14. Notion

Nothing is edited now. After approval I would propose, for your go-ahead: a
decisions-page row for whichever of §13 items 1, 4, 6, 7 are settled; closing design
§15 item 4 with the SENT policy; and a note on the walkthrough §12 that the service
contract exists and where.
