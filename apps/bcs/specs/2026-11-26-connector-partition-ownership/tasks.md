# Tasks: Connector partition ownership

## Group A — store keys and owner records
- [x] A1 `KeyLayout::partition_owner`, `partition_held`, `partition_text`; tests (AC1).
- [x] A2 `CoordinationStore::with_partition_owners(pool)`; owner key scope in
      `shard.rs`; test that Worker and Connector owner records are separate.

## Group B — ports
- [x] B1 `DispatchClaimRequest.partition`, `ClaimOutcome::NotOwner`; update callers.
- [x] B2 `DispatchQueuePort::held_in`, `CommandFeedPort::assign` (defaults).
- [x] B3 `DistConnectorRuntimeService::accept_handoff` (default unsupported).

## Group C — Connector store
- [x] C1 Per-partition in-flight set; never removed on a lost race (AC5).
- [x] C2 Claim rule with ownership proof (AC4); `held_in`.
- [x] C3 Feed `assign` (AC3).
- [x] C4 Tests for C1–C3.

## Group D — Connector engine
- [x] D1 Config and ports.
- [x] D2 Safe-point yield in the actor.
- [x] D3 Ownership round, feed assignment, recovery of acquired partitions (AC2, AC6).
- [x] D4 Drain with handoff, incoming `accept_handoff` (AC7, AC8).
- [x] D5 Tests for D2–D4.

## Group E — delivery
- [x] E1 Facade `accept_handoff`; `connector_router`; `HttpWorkerPeer::connector`.
- [x] E2 Docs (crate docs, connector-runtime notes), file-size check, tests, clippy on touched crates.
