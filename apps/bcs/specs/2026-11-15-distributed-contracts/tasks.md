# Distributed runtime implementation plan

> Execute only the explicitly authorized phase. This change authorizes contract
> definitions and their validation; all runtime work below remains pending.

**Goal:** implement three independently deployable roles against replaceable
state, replay-log and notification backends.

**Architecture:** Gateway and Connector produce SessionInbox inputs. Worker
commits state and effects before publishing TaskFeed or DispatchBoard changes.
Repository ports own durable semantics; adapters own physical storage mapping.

**Tech stack:** Rust workspace, serde contracts, async-trait ports, existing
outbound A2A binding/client, independently injected persistence implementations.

## Dependency order

```text
D0 declarations (this change)
  -> S1 conformance fixtures -> S2 in-memory protocol model -> S3 persistent reference
  -> G1/G2/G3 and W1/W2/W3 and C1/C2 may proceed in parallel against S2
  -> W4/W5 + C3/C4 + G4
  -> I1 full path -> I2 fault/restart suite -> I3 capacity and rollout
```

Do not start runtime implementation until D0 is reviewed. Each task below is a
reviewable slice; its tests are written before its implementation. Run the named
test target, implement the smallest slice, rerun it, then review that slice's
diff before any authorized commit. Do not create placeholder production impls
or return fabricated success to make contracts compile.

## Shared work

| ID | Files / deliverable | Depends on | Acceptance |
| --- | --- | --- | --- |
| D0 | `crates/contracts/bcs-dist-model/`; `crates/service-api/bcs-service-api/src/{application,core,port}/` additions | Approved design | Typed IDs, scoped opaque cursors, atomic commit and replay outcomes compile; no runtime implementation |
| S1 | `crates/test-support/bcs-test-support/src/contract/distributed/` and future provider fixtures | D0 | Reusable failure schedules for every public port; explicit fixtures, no empty passing harness |
| S2 | `crates/services/bcs-dist-store/src/memory/`; `tests/conformance_memory.rs` | S1 | Concurrent admissions, stale fences, partial effects and schedule races pass deterministic interleavings |
| S3 | `crates/services/bcs-dist-store/src/sql/`; `tests/conformance_sql.rs` | S2 | Same semantic suite passes persistent restart; no network await inside DB transaction |
| S4 | Backend-specific log/notification drivers and composition | S2 | Replays and acknowledgement positions survive restart/rebalance; mixed-backend admission cannot lose an acknowledged input |
| S5 | Retention and compaction maintenance under store crate | S3 | Only published snapshot coverage is trimmed; replay/ownership tombstones survive required retention |

S3 is a minimal persistent reference, not a full second production backend.
Deployment-specific storage plugins are owned by the host repository and must
run the same suite. Backend guarantees must be verified before rollout.

## Gateway work

| ID | Files / deliverable | Depends on | Acceptance |
| --- | --- | --- | --- |
| G1 | `crates/application/bcs-app-dist-gateway/src/{identity,routing}.rs` | D0 | Deterministic sid/tid/input identity; tenant/principal authorization; target and interaction checks; repeat request selects same task |
| G2 | Gateway `src/admission.rs` plus `crates/services/bcs-dist-kernel/src/gateway/admission.rs`; `tests/admission.rs` | G1, S2 | Application calls core, core calls repo; new task creation and inbox admission; same content duplicates, changed content conflicts; no success before durable activation |
| G3 | Gateway `src/query.rs` plus kernel `src/gateway/query.rs`; `tests/query.rs` | S2 | Full query reconstructs one cut; queued and archived tasks supported; task listing includes running tasks and authorization scope |
| G4 | `src/subscription.rs`; `tests/subscription.rs` | G3, W4 | Snapshot@H then follow H; valid resume C without double-folding; Gap rebuild; no per-event task read |
| G5 | `src/cancel.rs`; `tests/cancel.rs` | G2, W5, C4 | Cancel receipt is distinct from remote completion; retries preserve request identity |
| G6 | `crates/adapters/http/bcs-dist-gateway-http/`; protocol tests | G2-G5 | Existing A2A binding mapped without leaking HTTP types into services; first snapshot, SSE IDs, errors and disconnect semantics verified |

## Worker work

| ID | Files / deliverable | Depends on | Acceptance |
| --- | --- | --- | --- |
| W1 | `crates/services/bcs-dist-worker/src/{ownership,recovery}.rs` | S2 | Atomic expected-epoch claim, per-session seal and actual activation of all pending sessions, including future due times; stale owner rejected |
| W2 | `src/{reader,session_actor,scheduler}.rs` | W1 | Bounded per-session buffers, parallel sessions, progress-only batches; token-before-work-check settlement; no starvation in due scan |
| W3 | `crates/services/bcs-dist-runner-fsm/`; reducer tests | D0 | Reuse frozen workflow definition; pure deterministic reduction; no clock/IO/random calls; task ordering and interactions |
| W4 | `src/{processor,committer,effects}.rs`; `tests/commit_replay.rs` | W2, W3 | Sealed-owner/expected-commit-sequence check and candidate write are atomic; fixed effect IDs; previous required effects gate next commit; partial event replay cannot skip gaps |
| W5 | `src/{results,cancellation}.rs`; `tests/finalization.rs` | W4 | Save result before terminal; crash before second commit recovers result reference; next task starts after required terminal effects |
| W6 | `src/{supervisor,quarantine}.rs`; fault tests | W4, C3 | Lost notifications repaired; recovery attempts deduplicated by attempt ID before dangerous replay, without advancing business sequence or overwriting outbox; unresolved dispatches remain supervised |
| W7 | Engine control port, `crates/application/bcs-app-dist-runtime/` admin facade, metrics and bounded cache eviction | W1-W6 | Engine implements port, application exposes service; controlled handoff, useful lag/fence/conflict metrics, eviction preserves recoverability |

## Connector work

| ID | Files / deliverable | Depends on | Acceptance |
| --- | --- | --- | --- |
| C1 | `crates/services/bcs-dist-connector/src/{claim,actor}.rs` | S2 | One actor per holder generation; OwnedByMe with missing actor recovers; command ack is reliable takeover, not remote completion |
| C2 | `crates/external-clients/bcs-a2a-http/` semantic adapter | D0 | Reuse existing binding/client; distinguish NotSent/Rejected/Unknown; stable request IDs; no blind retries |
| C3 | `src/{sender,recovery,receiver,normalizer}.rs` | C1, C2 | Persist send intent, query/resubscribe by capability, source sequence does not reset, stable final identity survives holder change |
| C4 | `src/{continue_input,cancel}.rs`; control tests | C3 | Dequeued input never re-enqueues on old effect replay; ambiguous send retains input; cancel accepted/confirmed/unknown remain distinct |
| C5 | `src/ingress.rs`; `tests/reporting.rs` | C3, S2 | Key events admitted durably; stable digest excludes holder/transport metadata; content conflicts not swallowed; transport failure is not Agent terminal |
| C6 | Engine control port and shared application runtime admin facade/metrics | C1-C5 | Stop new claims, preserve unsettled obligations, report ambiguous calls and pending cancellations; engine does not import application contracts |

## Integration and rollout

| ID | Deliverable | Depends on | Acceptance |
| --- | --- | --- | --- |
| I1 | Single-process role composition and local end-to-end scenario | G6, W7, C6, S2 | Two-node workflow, human reply, cancel and queued task complete through real role boundaries |
| I2 | Cross-process restart/partition/duplicate test scenarios | I1, S3/S4 | All crash windows in the contract matrix exercised against durable providers; no inferred exactly-once remote effects |
| I3 | Access-count, fairness, backpressure and rollout plan | I2, S5 | Bounded memory/DB/queue work; batch and failure costs measured; old/new runtime cannot own the same session |

## Validation commands and implementation status

Declaration checks: `cargo check --manifest-path apps/bcs/Cargo.toml -p bcs-dist-model -p bcs-service-api --all-targets`.
Contract-shape tests: `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-dist-model -p bcs-service-api`.
Architecture checks use `apps/bcs/scripts/ci/` and must not weaken existing gates.

Runtime suites in S1-I3 are pending: the absence of runtime implementations is
intentional. See [validation](validation.md) for commands actually executed.
