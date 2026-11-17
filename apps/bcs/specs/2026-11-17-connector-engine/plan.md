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
| `crates/services/bcs-dist-connector-store` | new; depends on `bcs-dist-store` (Gateway plan, #4112) for `layout`, `version`, `error`, `capability` | TBase providers for the three dispatch ports, held index | provider part of C1 |
| `crates/services/bcs-dist-store` | one new module `membership`, only if §13 item 5 is decided that way | TBase `MembershipPort` provider (§10.1) | — |
| `crates/application/bcs-app-dist-runtime` | new, or add a module if the executor team created it first | admin facade | C6 |
| `crates/service-api/bcs-service-api` | **(decide)** additive only | Connector core contract | — |

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

Config: `partitions` is required and has no default (§10). Defaults for the rest:
`pool = a2a`, `max_active_calls = 1000`, `claim_concurrency = 16`,
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
- `Accepted` and `Duplicate` are both success. The Gateway plan (#4112 §3, C2)
  proposes removing `Duplicate` from the merged contract (decision 24); if that
  lands first, only `Accepted` is matched. `ContentConflict` cannot happen with
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

The flip side is a known limit (§13): two **different** input-required events with
no confirmed reply in between (the agent asks Q1, then re-asks a revised Q2) also
get the same `input_id`. Because the digest leaves the payload out (§5.2), the
second is "accepted" and dropped: the Worker and the PA only see Q1. A later fix
needs a durable per-call count of reported input-required events in `/connector`.

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
`AuthRequired` is a placeholder that `rules.report` replaces. The proposed payload
per kind, with one JSON sample each, is in
[research §5](research.md#5-proposed-agentevent-payload-per-kind); the executor side
signs it off (§13, E4).

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

Blocked until the Gateway plan's PR 1 (#4112 §2: `bcs-dist-store` with `layout`,
`version`, `error`, `capability`, and the exported test model) is in `dev`. No key
format or ts arithmetic is written here: keys come from `layout` (`disp`, `cmd`,
`held`), versions from `version::claim(new_holder_epoch)` and `version::next(read)`,
error mapping from `error`, the startup check from `capability`.

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
- `JsonEdit::Set` needs existing parents, and the Worker creates `disp` without a
  `connector` object. So the Connector never sets a path below `/connector`: it
  always writes the **whole** `/connector` object, built from the value it just
  read (absent = empty). `/connector` is Connector-only and every write is a read →
  `Newer(read + 1)`, so nothing can be lost between the read and the write. This
  needs nothing from the executor side beyond the layout sign-off.

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
- Partitions: the partition count is a **required** config value with no default,
  and it must be the same number on the Worker and on every Connector. The Worker
  picks a partition when it posts and each Connector reads all of them, so a
  Connector configured with fewer partitions would never see some hints (only the
  Worker's deadline re-post would rescue those calls). Construction fails when the
  value is missing or zero. Who owns the number and how a `did` maps to a partition
  are questions for the executor side (§13).

Tests need (a) an in-memory `bcs-store-api` model and (b) something that plays the
Worker on `disp` (create, cancel, push input, notify). (b) is a small test-only
writer in this crate's `tests/`. For (a): the conformance model exported from
`bcs-test-support` by the Gateway plan's PR 1, the same one the Gateway tests use.
No `#[path]` include; provider work starts after that PR merges anyway.

### 10.1 Membership provider (only if §13 item 5 is decided that way)

The claim needs liveness and the Connector needs a heartbeat, and the Gateway does
not use membership, so nobody builds a `MembershipPort` provider today. If the
owner confirms the executor team is not building one, this work adds it as module
`membership` in `bcs-dist-store` (one `pub mod` line there), so the Worker can
reuse it. Keys (`hb`, `member`, `members`) come from `layout`. It follows
decision 15:

| Method | Store calls | Rule |
| --- | --- | --- |
| `register` | member record `mutate_json(Newer(1), Replace, Persistent)`; `zset.add(members:<role>, InsertOnly)`; `kv.set(hb a, After(ttl))`, `kv.set(hb b, After(ttl))` | an identical retry is fine; an existing record with another role is `Invalid` |
| `renew` | `kv.expire(hb a, ttl)`, `kv.expire(hb b, ttl)` | `expire` only extends a key that still exists, so a lost lease is never revived. Both `false` → `Lost`. One `false` → the instance is still alive by the two-copy rule; that copy is set again |
| `set_draining` | the same check as `renew`, then a patch of the member record's state | `Lost` when both copies are gone |
| `liveness` | `kv.get(hb a)`, `kv.get(hb b)` (two calls, the copies are in different slots) | both missing → `Dead`; one present → `Alive`; any read error → `Unknown`. `Unknown` instead of `Dead` until this process has renewed its own heartbeat for one full TTL |
| `members` | `zset.range(members:<role>)`, paged | — |

Defaults: renew every 1 s, TTL 3 s. Cost: 2 writes per second per instance; 2 reads
per liveness check, and the claim checks liveness only when `disp` names another
holder.

## 11. Delayed contracts (stubs)

Each returns the port-level `StoreError::NotApplied { code: "need_contract_not_implemented" }`,
has a doc comment naming the §16 item, and a test asserting the error.

| Item | Method | Where | Caller meanwhile |
| --- | --- | --- | --- |
| D2 | `report_unresolved(call, Uncertain \| NotSent) -> Result<AdmitOutcome, StoreError>` | `bcs-dist-connector/src/delayed.rs` | logs; leaves the call in its phase (§6) |
| D3 | `repost_held(dead: &Instance, page) -> Result<Page<Did>, StoreError>` | store crate `delayed.rs` | not called by a loop in the MVP; the held index is still maintained |
| D8 | `release(did, holder: &Holder) -> Result<(), StoreError>` | store crate `delayed.rs` | `drain` calls it for each leftover and logs |

D3 and D8 are storage steps, so the engine reaches them through a small port,
`DispatchRecoveryPort` **(decide)**. Recommended: local to `bcs-dist-connector`
until D3 and D8 are final, implemented by the store crate's stubs through a thin
wrapper in the composition; it moves to
`bcs_service_api::port::repo::distributed::dispatch` once the contracts are settled.
D5 and D7 need no code here.

## 12. Order of work and pull requests

| # | PR | Needs | Contains |
| --- | --- | --- | --- |
| 1 | `docs(bcs): specify the distributed Connector` | — | this folder |
| 2 | `feat(bcs): add the Connector core contract and engine` | plan approved; §13 items 1, 3, 6, 7 | §2–§8, §11 (D2), tests with fakes |
| 3 | `feat(bcs): adapt the A2A client to the distributed agent port` | plan approved; §13 item 2 for the resolver only | §9 |
| 4 | `feat(bcs): add TBase providers for the dispatch board` | Gateway plan's PR 1 in `dev`; §13 items 4 and 5; executor answers E1 and E2 | §10, §11 (D3, D8), R10 list; §10.1 as its own PR if item 5 says so |
| 5 | `feat(bcs): expose Connector health and drain` | 2 | admin facade, composition |

2 and 3 are independent and need no TBase. Each PR: `cargo nextest run` for the
touched crates, `scripts/ci/arch-check.sh`, line counts, no global `cargo fmt`.

Left out on purpose: batching intermediate events (design proposal, about 200 ms
per call) because it changes the payload the Worker reads; storing a replay cursor
for `resume_events`; a supervisor loop (D3); SSRF policy (decision 19).

## 13. Decisions needed from the owner

Nothing in this table is chosen yet. "Reviewed" marks where the PR review agreed
with the recommendation; the decision is still the owner's.

| # | Question | Recommendation |
| --- | --- | --- |
| 1 | SENT policy (§6) | As written: resend only with `idempotent_start` / `idempotent_input`; else ask the agent when `query_by_request`; else unresolved. A definite "not sent" is retried 3 times, then unresolved |
| 2 | What resolves `CredentialRef`, what `BindingVersion` means, and where the agent's interface (URL, binding) comes from (§9) | Not guessed. The adapter takes an injected resolver; its implementation waits for the answer |
| 3 | May `bcs-service-api` get the additive, Connector-only core contract `core/dist_connector.rs` (§2)? And where does `DispatchRecoveryPort` (§11) live? | Core contract: yes, next to `dist_gateway.rs` / `dist_runner.rs`; no existing type changes. `DispatchRecoveryPort`: local to `bcs-dist-connector` for now, because it only fronts the D3/D8 stubs and an undecided contract in the shared API invites the executor team to build against it. Reviewed |
| 4 | Stored `disp` layout and the two Connector-owned fields (§10) | `DispatchView` JSON plus a `connector` sibling object that the Connector always writes whole. Needs executor sign-off (E3). Alternative: add the two fields to `DispatchExecution` (a contract change) |
| 5 | Who builds the TBase `MembershipPort` provider? The Gateway does not use membership (#4112 has no `MembershipPort`), so it will not come from there | This work builds it as module `membership` in `bcs-dist-store` (§10.1), following decision 15, **after** the owner confirms the executor team is not already building one. Reviewed |
| 6 | Key-event `business_digest` without the payload (§5.2), and n = watermark + 1 (§5.3) | As written, with the limit below. Reviewed |
| 7 | A2A terminal states and the payload per event kind (§5.4, samples in [research §5](research.md#5-proposed-agentevent-payload-per-kind)): canceled and rejected as `Error` with the state in the payload | As written; the executor side signs off the payload table (E4). Reviewed |

Settled by the review, no decision left: the shared helpers crate is
`bcs-dist-store`, and provider tests use the conformance model exported from
`bcs-test-support` by the Gateway plan's PR 1 (§10).

Questions for the executor side (to be asked through the owner):

| # | Question | Why the Connector needs it |
| --- | --- | --- |
| E1 | Who owns the command-stream partition count, and how does a `did` map to a partition? | The count must be identical on the Worker and every Connector (§10) |
| E2 | At a call's deadline, does the Worker produce a cancel or a re-post for a call that is already ACCEPTED? The design only says it re-posts calls that are not ACCEPTED; decision 29 (d) says its timeout decides retry, fail or cancel | Until D3 exists this is the only way an ACCEPTED call with a dead holder is picked up again (research §2 item 9) |
| E3 | Sign-off of the stored `disp` layout: `DispatchView` JSON, path writes only after creation, and a Connector-owned `connector` object the Worker never writes | Both providers write the same document (§10) |
| E4 | Sign-off of the `AgentEvent` payload per kind ([research §5](research.md#5-proposed-agentevent-payload-per-kind)), including "first terminal wins" when a final and an error are both admitted for one call | The reducer reads exactly that |

Known limits to accept or object to:

- Until D3 exists, an ACCEPTED call whose holder died waits for the Worker's
  deadline, and only if E2 is answered "yes" (research §2 item 9).
- Two different input-required events with no confirmed reply in between: the
  second is dropped (§5.3).
- An unresolved call occupies one of `max_active_calls` until the Worker cancels it.
- Polling costs one read per held call per 1.5 s (research §4).

## 14. Notion

Nothing is edited now. After approval I would propose, for your go-ahead: a
decisions-page row for whichever of §13 items 1, 4, 6, 7 are settled; closing design
§15 item 4 with the SENT policy; and a note on the walkthrough §12 that the service
contract exists and where.
