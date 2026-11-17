# Tasks: distributed Connector runtime

Status: proposed; nothing started. Executes [plan](plan.md). Maps to C1–C6 of
`specs/2026-11-15-distributed-contracts/tasks.md`. Tests are written before
the code of each task; each group ends with its crate's tests green.

## Group 0 — Scaffolding

- [ ] 0.1 Add crates `bcs-dist-connector`, `bcs-dist-connector-store`,
  `bcs-app-dist-runtime` (manifests, `[lints] workspace = true`, empty `lib.rs`),
  workspace members/dependencies, `Cargo.lock`, the R10 scan list, the
  `dist-agent` feature of `bcs-a2a-http`. `cargo check --workspace` passes.

## Group 1 — Contract and shared module (additive)

- [ ] 1.1 `bcs_service_api::core::dist_connector`: types and
  `DistConnectorCoreService` with the call-order and rule docs (plan §2);
  `dist_connector_core_service_contract_tests` harness in `bcs-test-support`;
  `bcs-service-api` 6.2.0 and its `CONTEXT.md` section; no name clash with `port/`.
- [ ] 1.2 `bcs_dist_store::command`: `GROUP`, `CommandStreams`, `partition_of`;
  tests: stable known values, range, single partition.

## Group 2 — Rules (C3 normalizer, C5 identities)

- [ ] 2.1 `identity.rs` and `DistConnectorRules::{start_request_id, input_request_id, report, next_input}`;
  tests: identity equal across holder epochs and with/without remote event ids;
  n = confirmed + 1; digest ignores holder epoch, seq, event id and payload;
  seq from the cursor.
- [ ] 2.2 `resume_step`, `uncertain_start`, `uncertain_input`, `reattach`,
  `end_of`; table tests for every phase and capability row and the
  canceled/rejected mapping; `conformance_rules.rs`.

## Group 3 — Providers (C1 storage side)

- [ ] 3.1 `doc`: stored `disp` document codec with the `connector` object;
  schema-version rejection; a Worker-created document without `connector` decodes.
- [ ] 3.2 `held_index` + `StoreDispatchQueue::claim`: race, `OwnedByMe`,
  `Ended`, `NotFound`, add-before-claim, cleanup, lost claim response.
- [ ] 3.3 `PendingMembership` stub + takeover branch: `HeldByOther` with the
  stub (asserts the code), takeover at epoch + 1 with a `Dead` fake, `Unknown`
  is not `Dead`.
- [ ] 3.4 `resume_owned`: advances the generation; refuses a changed holder.
- [ ] 3.5 `StoreHeldDispatch`: every method of plan §4.2, stale holder → `Lost`,
  Worker write in between, lost response, definite failure, bounded contention.
- [ ] 3.6 `StoreCommandFeed`: plan §4.3; port harness.
- [ ] 3.7 `delayed.rs`: `repost_held` (D3) and `release` (D8) stubs with tests.
- [ ] 3.8 `conformance_dispatch_board.rs`: port harnesses for queue, feed and
  held dispatch with the test-only Worker fixture.

## Group 4 — Engine (C1 actor, C3, C4, C5)

- [ ] 4.1 Port fakes for tests (queue/held, feed, inbox, agent client, blob, membership).
- [ ] 4.2 Main loop: receive → claim → ack order; one actor per generation;
  `OwnedByMe` with a missing actor → `resume_owned`; backpressure.
- [ ] 4.3 Actor send path: begin_send before the call; each `SendOutcome`.
- [ ] 4.4 Recovery by capability; unresolved call (D2 stub, logged, still polled).
- [ ] 4.5 Receiver + ingress: seq, admit/append, `Accepted` = `Duplicate`,
  conflict surfaced, retries with the same seq, blob offload,
  final before finish, crash-between then takeover.
- [ ] 4.6 Continue-input: same call, confirm only on definite acceptance,
  prefix rule, ambiguous send retains the input.
- [ ] 4.7 Cancel: before send, after acceptance, unsupported, accepted vs
  confirmed vs unknown.
- [ ] 4.8 Lost holder stops without writes.

## Group 5 — A2A adapter (C2)

- [ ] 5.1 `DistAgentResolver`, `A2aDistAgentClient::start` / `send_input`
  with the classification table; fake-agent tests.
- [ ] 5.2 `query`, `subscribe`, `cancel`; event mapping;
  `conformance_dist_agent_client.rs`.

## Group 6 — Admin (C6)

- [ ] 6.1 `DistRuntimeControlPort` on the engine: snapshot, drain (stop
  claiming, wait, D8 stub for leftovers); `conformance_runtime_control.rs`.
- [ ] 6.2 `ConnectorRuntimeFacade` in `bcs-app-dist-runtime`; service harness.

## Group 7 — End to end and finish

- [ ] 7.1 `store_e2e`: engine + providers + `StoreInboxProducer` on `Model`.
- [ ] 7.2 File-size check, `BCS_BASE_REF=origin/dev scripts/ci/arch-check.sh`
  (pre-existing failures listed, not fixed), changed-line coverage, `cargo check --workspace
  --all-targets`, nextest for the affected crates.
- [ ] 7.3 `validation.md` with the commands actually run; `specs/README.md`
  entry; `apps/bcs/README.md` crate rows; PR description (calls out the core contract, the shared `command`
  module and the stubs for the executor team).
