# Validation: distributed Connector runtime

Baseline `dev` = `5c3e30ea`. Run in `apps/bcs` on 2026-11-17 with Rust 1.94.0.
`cargo nextest`, `cargo llvm-cov`, `cargo clippy`, `cargo machete` and
`cargo public-api` are not installed in this environment; see "Not run".

## Delivery status

Implemented and tested: the core contract, the engine, the store providers,
the A2A adapter and the admin facade. Not integrated: nothing is registered in
the `bcs` binary (composition is task I1), and no real TBase store, membership
provider or agent resolver exists yet. Not deployed.

## Commands and results

| Command | Result |
| --- | --- |
| `cargo check --workspace --all-targets` | finished; only warnings that exist on `dev` |
| `cargo test -p bcs-dist-connector` | 72 passed, 0 failed |
| `cargo test -p bcs-dist-connector-store` | 48 passed, 0 failed |
| `cargo test -p bcs-a2a-http` (includes the `dist-agent` feature through its own dev-dependency) | 111 passed, 0 failed (17 new) |
| `scripts/e2e-test/custom_binding_story.py` with and without `--grpc-peer-binary` (plain debug build of `bcs-caller-e2e` and `bcs-a2a-grpc-peer`) | both pass; the gRPC variant now ends with a send while the mesh peer is down |
| `cargo check -p bcs` (the binary, without the `dist-agent` feature) | pass |
| `cargo test -p bcs-service-api -p bcs-test-support -p bcs-dist-store -p bcs-dist-model -p bcs-app-dist-runtime` | 413 passed, 0 failed |
| the three new suites repeated five times | no failure, no timeout |
| `scripts/ci/check-store-boundaries.sh` (R10, now also scans `bcs-dist-connector/src`) | PASS |
| `scripts/ci/tests/store_api_boundary.sh` | PASS |
| `scripts/ci/check-port-purity.sh` (LINT-2) | PASS |
| `scripts/ci/check-forbidden-symbols.sh` (LINT-3) | PASS |
| `BCS_BASE_REF=origin/dev scripts/ci/check-baseline-not-growing.sh` | PASS (1/1, 1/1, 9/9) |
| `scripts/ci/check-import-rules.sh` (LINT-1) | fails on `dev` already; no hit in the new or changed crates |
| `scripts/ci/check-trait-naming.sh` (LINT-4) | fails on `dev` already (11 older traits); `DistConnectorCoreService` conforms |
| `scripts/ci/check-r25-conformance.sh` | fails on `dev` already; no finding names a new trait or implementation. One finding is in the shared crate and predates this change: `StoreInboxProducer` has no `conformance_*.rs` entry |
| `scripts/ci/check-deps.sh` | DEP-1 / DEP-6 fail on `dev` already (`bcs-protocol → bcs-domain`, `bcs-service-api → bcs-config-api, bcs-storage-api`); DEP-8 skipped (no `cargo machete`) |
| largest new file | 707 lines (`bcs-dist-connector-store/tests/common/mod.rs`); limit 1,000 |

## Acceptance (spec)

| Criterion | Where it is tested |
| --- | --- |
| B1 one holder generation, one winner | `claim.rs`: `two_claimers_one_winner`, `only_a_dead_holder_is_taken_over` |
| B2 ack after the claim decision | `lifecycle.rs`: `the_hint_is_acked_only_after_the_claim_decision`, `hints_for_calls_with_nothing_to_do_are_acked_and_dropped` |
| B3, B4, B5 intent, acceptance, final before finish | `lifecycle.rs`: `a_call_runs_from_hint_to_end_in_the_contract_order`; `recovery.rs`: `a_crash_between_final_and_finish_is_completed_by_the_next_holder` |
| B6 held index | `claim.rs`: first claim, lost race, failures; `held.rs`: `one_call_from_send_to_end` |
| B7–B11 reporting | `rules.rs`; `lifecycle.rs`; `recovery.rs`: `a_takeover_of_an_accepted_call_reattaches_and_restarts_seq_at_one` |
| B12 replies | `control.rs`: reply, second round, unknown, idempotent, rejected, former holder's input |
| B13, B14 cancels and polling | `control.rs`: confirmed, accepted, unknown, unsupported, already finished, before send, on the wire, re-notification |
| B15–B19 recovery | `recovery.rs`; `held.rs`: stale holder; `claim.rs`: takeover tests |
| B20 drain | `runtime.rs`: `drain_stops_claiming_and_leaves_running_calls_held`, `drain_returns_as_soon_as_held_calls_end` |
| B21 capabilities | `claim.rs`: `startup_fails_without_a_required_capability` |
| every `disp` write with a lost response and a definite failure | `held.rs`: `every_write_survives_a_lost_response`, `every_write_reports_a_definite_failure`, `a_write_that_did_not_land_is_made_again`; `claim.rs`: the claim equivalents |
| port harnesses | `conformance_dispatch_board.rs`, `conformance_rules.rs`, `conformance_runtime_control.rs`, `conformance_dist_agent_client.rs`, `conformance_connector_runtime.rs` |
| stubs return `need_contract_not_implemented` | D2 `recovery.rs`; D3, D8 `stubs.rs`; membership `claim.rs` |

## Deviations from the plan

1. **Engine tests run on the real providers.** The plan had hand-written port
   fakes for the engine plus one end-to-end test. All engine tests instead use
   the real store providers and the shared admission provider on the in-memory
   store model, with a scripted agent. They assert the stored `disp` document
   and the inbox, which is stronger and needed less test-only code.
2. **One addition to the core contract:** the constant `INPUT_MAYBE_SENT`. A
   new holder cannot see the Connector-owned `input_sending` field through the
   merged `HeldDispatchPort`, so the provider answers `begin_send(Input)` with
   `InvalidTransition { code: "input_maybe_sent" }` when a former holder
   recorded intent for that input, and the engine then applies
   `uncertain_input`.
3. **A takeover writes only `/execution/holder` by path** (plus `/connector`
   when the Worker's document has none), then reads the document back. Writing
   the whole `/execution` from the claimant's earlier read could undo what a
   slow former holder wrote in between (for example its `finish`).
4. **The held index is listed right before the claim write**, not before the
   first read, and is unlisted when the claim finds nothing to hold. Still
   "add before the claim, remove after ENDED".
5. **Membership and D3/D8 stubs:** `PendingMembership` and the stub bodies are
   in `bcs-dist-connector-store`; the engine-local trait is `HeldCallRecovery`.
   As planned.
6. **A call the agent offers no way to follow** (no subscribe, no task query)
   is not "unresolved": replies and cancels are still carried out; it is
   reported through the D2 stub and counted as a pending effect.
7. **A call waiting on a question keeps looking at its remote task** (by task
   query, or a re-subscribe every few ticks), because the agent can move on
   without a reply through the Connector.
8. **Claims are sequential** (no `claim_concurrency`), and the poll interval is
   spread ±20 % deterministically per call instead of randomly.

## Review round 1 (2026-11-17)

Fixed after the first automated review round, each with a test: a resume whose
answer was lost; the state returned by a request-id lookup; the polled state
after a confirmed reply; backoff and bounds for cancel and input resends;
membership renewal during drain; an unknown claim write no longer unlists the
call; the request is loaded before send intent; a replaced actor is joined;
permanent store errors stop the actor instead of retrying forever. Adapter:
JSON-RPC errors are rejections only for request-refusal codes on the initial
response; an empty task id is not accepted; a repeated tool record produces no
event; the credential is redacted in debug output. The singlebox changed-line
gate failed on the gRPC connect-refused lines; the gRPC binding story now
exercises them.

## Review round 3 (2026-11-17)

Fixed, each with a test unless noted: a cancel answered "already finished"
reads the task a bounded number of times (per-call backoff) and then ends on
the cancel answer; a definite "no such task" is not asked again and a lagging
read is reported once; a task read ends the call by its status, also when the
snapshot is stored by reference; an interface resolved with the `Poll` update
mode is sent to with the unary operation and `returnImmediately`; the feed
backoff sleeps only until the next membership renewal is due (no new test: the
existing feed-outage test covers the path, not the timing).

## Review round 4 (2026-11-17)

Fixed, each with a test: a resume still owed from a failure is dropped once the
call has a running actor, so it can no longer stop that actor; a task read
whose snapshot is stored by reference is reported as
`{"state": <word>, "task_ref": <blob ref>}`, so the event itself carries the
state the rules and the Worker read (this replaces the round-3 "end by status"
shortcut beside the rules).

## Review round 5 (2026-11-17)

Fixed with a test: a rejection of a resend, after a start whose outcome was
unknown, leaves the call unresolved instead of failing it; the rejection says
nothing about the earlier send.

## Rebase onto the Worker and Gateway changes (2026-11-19)

`dev` gained the Gateway (`bcs-service-api` 7.0.0), the Worker coordination
provider and the Worker's facade. Adapted here:

- `bcs-service-api` is 7.1.0 for the Connector core contract (planned as
  6.2.0; the plan foresaw this case).
- `MembershipPort` changed: `register` takes a control address, `retire` is
  new, `members` returns `MemberRecord`. The engine passes the new
  `ConnectorConfig::control_address` (empty until a control endpoint is
  wired). `retire` is not called by the Connector yet.
- A real membership provider now exists (`bcs_dist_store::coordination`).
  `PendingMembership` stays as the stub a Connector runs with when the
  composition injects none; this change does not wire the real one.
- `bcs-app-dist-runtime` now holds the Worker's facade in `lib.rs`; the
  Connector's stays in `connector.rs` beside it.
- The shared inbox provider needs a document store; the test store wrapper
  implements it.

Tests after the rebase: `bcs-dist-connector` 77, `bcs-dist-connector-store`
48, and the suites of `bcs-a2a-http`, `bcs-dist-store`,
`bcs-app-dist-runtime`, `bcs-service-api`, `bcs-test-support` pass;
`cargo check --locked --workspace` passes.

## Review round 10 (2026-11-19)

Fixed with tests: only the "no provider wired" stub answer lets the Connector
run unregistered. A refused registration (for example the empty default
control address against the real provider) fails the start; any other
registration failure is asked again before anything is claimed.

## Review round 11 (2026-11-19)

Fixed with a test: the registration retries end with the registration's
lifetime (`membership_lost`), since an applied registration nobody renewed has
expired by then; a registration confirmed after a retry is renewed at once; a
drain is checked before every attempt.

## Not run, and why

- `cargo nextest` and the coverage gates (changed-line > 85 %, singlebox E2E):
  the tools are not installed here. CI runs them on the pull request.
- `cargo clippy`: not installed. Non-test code was written without `unwrap`,
  `expect` or panics, and reviewed for it.
- `cargo machete` (DEP-8) and `cargo public-api` (API-1): not installed. The
  manifests were checked by hand; `bcs-service-api` goes from 6.1.0 to 6.2.0.
- No test against a real TBase store, a real membership provider or a real
  agent: none exists in this repository.

## Known limits

- Until D3 exists, an ACCEPTED call whose holder died is picked up again only
  if the Worker re-posts or cancels it at its deadline.
- Two different input-required events before a confirmed reply share one
  identity; the second is dropped.
- `query(ByRequest)` has no A2A equivalent: a plain A2A agent without
  `idempotent_start` is unresolved after a send that failed once the request
  was handed to the transport. A connection that was never established is
  retried.
- A new actor (takeover or resume) that reads the task right after a reply was
  confirmed, before the agent updated its status, reports the answered
  question again under the next question round; the agent's real next question
  then shares that identity and is dropped. Telling the two apart needs a
  stored marker of the answered question, which is a change to a merged
  contract (`HeldDispatchPort`); deferred to a follow-up by the owner.
- An unresolved, blind or conflicted call occupies one of `max_active_calls`
  until the Worker cancels it or another holder takes it.
