# Tasks: distributed Connector runtime

Status: proposed; nothing started. Executes [plan](plan.md). Maps to C1–C6 of
`specs/2026-11-15-distributed-contracts/tasks.md`. Tests are written before
the code of each task; each group ends with its crate's tests green.

## Group 0 — Scaffolding

- [ ] 0.1 Add crates `bcs-dist-connector`, `bcs-dist-connector-store`,
  `bcs-app-dist-runtime` (manifests, `[lints] workspace = true`, empty `lib.rs`),
  workspace members/dependencies, and every crate inventory the architecture
  checks require. `cargo check --workspace` passes.

## Group 1 — Contract and shared module (additive)

- [ ] 1.1 `bcs_service_api::core::dist_connector`: types and
  `DistConnectorCoreService` with the call-order and rule docs (plan §2);
  contract-shape test; public-API baseline updated if required.
- [ ] 1.2 `bcs_dist_store::command`: `GROUP`, `CommandStreams`, `partition_of`;
  tests: stable known values, range, single partition.

## Group 2 — Rules (C3 normalizer, C5 identities)

- [ ] 2.1 `DistConnectorRules::{classify, request_id, key_identity, normalize}`;
  tests: identity equal across holder epochs and with/without remote event ids;
  n = confirmed + 1; digest ignores holder epoch, seq, event id, cursor.
- [ ] 2.2 `recovery`, `after_send`, `terminal`; table tests for every
  capability row, not-sent ×3, canceled/rejected mapping.

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
- [ ] 3.7 `ConnectorRecoveryPort` stubs (D3, D8) with tests.
- [ ] 3.8 Port harnesses for queue and held dispatch with the test-only Worker fixture.

## Group 4 — Engine (C1 actor, C3, C4, C5)

- [ ] 4.1 Port fakes for tests (queue/held, feed, inbox, agent client, blob, membership).
- [ ] 4.2 Main loop: receive → claim → ack order; one actor per generation;
  `OwnedByMe` with a missing actor → `resume_owned`; backpressure.
- [ ] 4.3 Actor send path: begin_send before the call; each `SendOutcome`.
- [ ] 4.4 Recovery by capability; unresolved call (D2 stub, logged, still polled).
- [ ] 4.5 Receiver + ingress: seq, admit/append, `Accepted` = `Duplicate`,
  conflict surfaced, retries, final snapshot by `query_by_task`, blob offload,
  final before finish, crash-between then takeover.
- [ ] 4.6 Continue-input: same call, confirm only on definite acceptance,
  prefix rule, ambiguous send retains the input.
- [ ] 4.7 Cancel: before send, after acceptance, unsupported, accepted vs
  confirmed vs unknown.
- [ ] 4.8 Lost holder stops without writes.

## Group 5 — A2A adapter (C2)

- [ ] 5.1 `DistAgentResolver`, `A2aDistAgentClient::start` / `send_input`
  with the classification table; fake-agent tests.
- [ ] 5.2 `query`, `subscribe`, `cancel`; event mapping; port harness.

## Group 6 — Admin (C6)

- [ ] 6.1 `DistRuntimeControlPort` on the engine: snapshot, drain (stop
  claiming, wait, D8 stub for leftovers); port harness.
- [ ] 6.2 `ConnectorRuntimeFacade` in `bcs-app-dist-runtime`; service harness.

## Group 7 — End to end and finish

- [ ] 7.1 `store_e2e`: engine + providers + `StoreInboxProducer` on `Model`.
- [ ] 7.2 File-size check, architecture checks, `cargo check --workspace
  --all-targets`, nextest for the affected crates.
- [ ] 7.3 `validation.md` with the commands actually run; `specs/README.md`
  entry; PR description (calls out the core contract, the shared `command`
  module and the stubs for the executor team).
