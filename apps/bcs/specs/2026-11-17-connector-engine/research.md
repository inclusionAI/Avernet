# Connector — research

Status: input to [spec.md](spec.md) and [plan.md](plan.md). Read against `dev` at
`7d644824` (contains #4020 contracts and #4070 `bcs-store-api`).

Sources, highest authority first: merged code in `dev` (type and trait shapes) →
Notion decisions page rows 1–31 → Gateway & Connector design §5.1 → Worker & TBase
v2.9 §6.10/§6.11/§6.14 → PR #4020 walkthrough §12/§16/§17 → PR #4070 walkthrough.

## 1. Mapping our design onto `bcs-store-api`

Read: `crates/plugin-api/bcs-store-api/{README.md,src/*}` and the conformance suite
`bcs-test-support::contract::plugin::store`. Everything the Connector needs exists.
Nothing below requires a trait change.

### 1.1 `disp` (JSON document, `JsonStorePlugin`, capability `AtomicJson`)

| Our step | Call | Condition | Notes |
| --- | --- | --- | --- |
| read with ts | `get_json(disp)` → `Versioned { value, version }` | — | `None` = no such call. Errors are never `None` |
| claim | `mutate_json(disp, Newer(Version((epoch + 1) × 10^9)), Patch([...]), Preserve)` | greater wins, equal rejected | `Patch`, not `Replace`: only `/execution/holder` (and `/execution/phase` when it was `created`) change, so a Worker write that landed between our read and the claim is kept. Two claimers write the same version; one gets `ConditionNotMet` |
| every other holder write | read → check holder is me → `mutate_json(disp, Newer(Version(read + 1)), Patch([...]), Preserve)` | compare-and-set by construction | Always `Newer(read + 1)`, never `Version(read)`: with `Version` the store picks the next version, which would break "ts = holder epoch × 10^9 + k" |
| rejected write | `ConditionNotMet` carries no version | — | re-read with `get_json`, re-check holder, merge, retry (bounded) |
| unknown write | `StoreError::OutcomeUnknown` | — | re-read and compare content: only the holder writes `/execution/*`, so "my fields are there and the holder is still me" proves the write landed. Mapped to the port's `StoreError::OutcomeUnknown` only when the re-read also fails |

Details checked in the source, beyond the walkthrough:

- Pointers are RFC 6901 (`/execution/phase`). `JsonEdit::Set` needs existing parents
  and can create an object field or replace an array element; it cannot append to an
  array. The Connector never appends (the Worker appends `pending_input`), so this is
  enough.
- A `Patch` on a missing document returns `ConditionNotMet`, not an error.
- A successful identical write still advances the version; a failed condition does
  not touch the expiry. All updates use `Expiry::Preserve`.
- Range guard: a claim is refused as `Invalid` when `(epoch + 1) × 10^9` overflows
  `u64` or when the read version is not below it; an update is refused when
  `read + 1` would reach the next epoch boundary (k < 10^9).

### 1.2 Command stream (`StreamStorePlugin`, capabilities `Stream` + `StreamGroups`)

| Our step | Call | Notes |
| --- | --- | --- |
| create group | `create_group(cmd:<pool>:p<n>, "connectors", GroupStart::Beginning)` | `AlreadyExists` is success; never resets the group |
| read | `read_group(key, "connectors", "<node>:<incarnation>", ReadOptions { max_entries, block })` | one blocking call per partition; one key per call (decision 23) |
| ack | `ack(key, "connectors", &[id])` | after the fenced claim decision; a second ack returns 0 |
| take over stuck hints | `claim_idle(key, "connectors", consumer, min_idle, start, limit)` | page with `next`; ids in `deleted` are already gone from the pending list and need no work |

Details checked in the source:

- "Cancelling a group read may leave pending delivery behind." So a group read is
  never cancelled or raced with a timeout on our side: one long-lived reader task
  per partition feeds a bounded channel, and `CommandFeedPort::receive` only drains
  the channel. Entries read but not yet handed out stay pending under our consumer
  and are recovered by `claim_idle` if we die.
- `read_group` timeout/disconnect is `OutcomeUnknown` (the pending list may have
  changed). Harmless: the stream is a hint, and those entries come back through
  `claim_idle`.
- No operation removes a dead consumer. Not needed.

### 1.3 Held index (`ZSetStorePlugin`, capability `ZSet`)

| Our step | Call |
| --- | --- |
| add before claim | `add(held:<conn>:<inc>, &[ZSetEntry { member: did, score: now_ms }], ZAddMode::Upsert)` |
| remove after ENDED, or when the claim did not acquire | `remove(held, &[did])` |
| list (supervisor, D3, stubbed) | `range(held, ZSetRange { min: Unbounded, max: Unbounded, after, limit })` |

### 1.4 Reporting (through the Gateway session's `InboxProducerPort` provider)

The Connector never writes `inbox`, `admit`, `active` or `wake` itself. For the
record, that provider uses `put_if_absent` + `expire` for `admit`, `append(Auto)` for
`inbox`/`wake`, `add(…, ZAddMode::Lower)` for `active`.

### 1.5 Capabilities required at startup

`AtomicJson`, `Stream`, `StreamGroups`, `ZSet`, `Kv`. `Kv` is used by the shared
admission and heartbeats, not by the Connector's own providers; it is still required
up front so a Connector cannot start against a store that cannot admit key events.
Missing capability → the constructor returns an error before any read.

### 1.6 Outside the trait (not designed around)

Blocking-connection limits (TBase question 8) and ordering across connections (part
of delayed D1).

## 2. Findings that differ from the brief or need a decision

1. **`check-deps.sh` does not limit who depends on `bcs-store-api`.** It only asserts
   that `bcs-store-api` itself depends on no `bcs-*` crate (DEP-6). The only
   enforcement of "store code lives in `*-store`" is R10
   (`check-store-boundaries.sh`), and R10 scans a fixed list of four directories
   (`bcs-bot`, `bcs-group`, `bcs-friend`, `bcs-relation`). A new service crate is not
   scanned unless it is added to that list (and to `tests/store_api_boundary.sh`).
   Plan: add `crates/services/bcs-dist-connector/src` to both.
2. **No in-memory store exists in `dev`** (task S2, `bcs-dist-store/src/memory`, is
   not started). The in-memory model of `bcs-store-api` lives under
   `bcs-test-support/tests/support/store/` and the README says it "is not a
   production or exported in-memory plugin". Provider tests need it. See plan §10.
3. **The Gateway session's shared PR is not open yet** (no open PR or remote branch
   with key/ts helpers on 2026-11-17). Provider work stays blocked on it.
4. **The claim needs liveness, and no `MembershipPort` provider exists.** Design
   §5.1 step 5: a Connector that reads a post "sees in `disp` that the holder is dead
   and claims it". `DispatchClaimRequest` has no takeover flag, so the liveness check
   sits inside the `DispatchQueuePort` provider. The Connector also has to heartbeat.
   Who implements the TBase `MembershipPort` is not assigned anywhere.
5. **`disp` needs two Connector-written facts the merged `DispatchExecution` has no
   field for**: which pending input a send was started for (so a new holder can tell
   "never sent" from "maybe sent"), and the `evidence` string of `finish` (the
   Worker's `ControlOutcome::SupersededTerminal` returns it). The stored layout of
   `disp` is shared with the Worker's `DispatchControlPort` provider and is not
   signed off (decisions page, action items).
6. **`AgentBinding` cannot be turned into a network call today.** It carries
   `agent`, `binding_version`, `credential_ref`. The existing client needs an
   interface (URL, binding, tenant), a route fingerprint, a bearer and negotiated
   extensions, which today come from `A2aRunRecord` + `ProviderCredentialRepoPort`.
   This is walkthrough §17; not guessed here.
7. **The payload of an `AgentEvent` is not specified per kind**, and neither is the
   mapping of A2A terminal states onto `Final` / `Error` (there is no kind for
   canceled or rejected). Both are read by the Worker's reducer.
8. **"The n-th input-required event of the call" is not derivable by a new holder
   from the stream alone.** It has to come from durable facts. Proposal in plan §5.3.
9. **MVP recovery of an ACCEPTED call whose holder died** rests on the Worker's
   deadline (its cancel or re-post reaches a live Connector, which takes the call
   over because the holder is dead). The Worker's "re-post if not ACCEPTED" does not
   cover ACCEPTED calls, and D3 is stubbed. So such a call stalls until its deadline.

## 3. Existing outbound A2A client (`crates/external-clients/bcs-a2a-http`)

- `BindingClient` (`binding_client.rs`, crate-private) is the transport seam:
  `send_message`, `send_streaming_message`, `get_task`, `cancel_task`,
  `subscribe_to_task` over REST, JSON-RPC, gRPC and custom bindings. No binding
  retries anywhere ("Never replay a side-effecting submission").
- `A2aHttpTransport::client(record)` builds a `BindingClient` from an
  `A2aRunRecord` (interface, credential kind, extensions, frozen route fingerprint).
- `delivery_error::initial_rejection` already separates a complete rejection (HTTP
  400/401/403/404/405/406/415/422; gRPC 3/5/7/12/16) from uncertain outcomes
  (408/409/429, all 5xx, timeouts). Connect failures surface as
  `A2AError::internal("… connection failed")` without a typed "never sent" marker,
  so NotSent needs an explicit classification at the request-building / connect step.
- A2A `messageId` is the natural carrier of the stable `RequestId`; the existing
  dedup extension (`DEDUP_EXTENSION`) is how an agent declares it de-duplicates.
- The crate may not import `bcs_service_api::core` or `::application` (LINT-1,
  DEP-4); `port` is allowed.

## 4. Access cost (per `apps/bcs/AGENTS.md`, "Database Access Cost")

Store round trips by the Connector's own providers, per call, normal path:

| Step | Round trips |
| --- | --- |
| receive | amortised: one blocking `read_group` per partition, batches of up to `max` |
| claim | 3: `ZADD held`, `get_json`, `mutate_json` (+ a liveness read only when a different holder is recorded) |
| ack | 1 |
| SENT, ACCEPTED, each confirm, each cancel observation, ENDED | 2 each (read + conditional write) |
| ENDED | + 1 `ZREM held` |
| poll while held | 1 `get_json` per call per poll interval |

- A call with no human reply and no cancel: about 3 + 1 + 2 + 2 + 3 = 11 round trips
  plus polling.
- Polling dominates: H held calls at a 1.5 s interval is H / 1.5 reads per second
  per Connector (1,000 calls → about 670 reads/s). It is one bounded read per call,
  no scan. The interval is configurable and jittered per call.
- Reporting cost is in the shared admission provider: one port call per reported
  event. With D1 stubbed ("always wake") an intermediate event is up to 3 writes
  there. Intermediate events are not batched in this plan (see plan §12).
- No lock or store write is held across an agent call: SENT is written and
  acknowledged before the network call starts.
- Sustained store failure: writes retry with capped backoff per call, and the event
  stream of that call is not read further while a report is pending (bounded
  buffer), so memory does not grow.
