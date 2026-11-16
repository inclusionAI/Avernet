# Distributed domain model

Status: additive contract definitions, not an active runtime or deployed format.
Crate version: `0.1.0`. This crate depends only on serde and serde_json.

## Ownership and navigation

| Module | Owns |
| --- | --- |
| [ids](src/ids.rs) | Scoped business identities, logical output/effect IDs, opaque replay positions, immutable payload references |
| [session](src/session.rs) | Frozen session specification, participant bindings, declared downstream capabilities and limits |
| [input](src/input.rs) | Logical input envelope, Agent events, admission receipt |
| [task](src/task.rs) | Task state, interactions, persisted snapshot, reconstructed view, output events and final archive |
| [dispatch](src/dispatch.rs) | Immutable dispatch specification, separately owned control and execution state |
| [runner](src/runner.rs) | Serializable reducer state and deadlines shared with checkpoint persistence |

Checkpoint, commit receipts, dispatch recheck versions, `ShardOwner` identity
and session-scoped `SessionOwnership` identities live
beside their [repository contracts](../../service-api/bcs-service-api/src/port/repo/distributed/README.md).
Workflow structure reuses `bcs_domain::collaboration::StateMachineDefinition`
at the Service API boundary; this crate stores its immutable reference and digest.

## Identity and serialization rules

- `InputId` identifies a logical submission. The same scoped identity with
  changed business content is a conflict. Backend offsets are not input IDs.
- `EventId = (CommitSeq, nonzero index)` is a logical ordered output identity.
  It is allocated before checkpoint commit and retained on replay. It is not
  wall-clock time or a physical queue position.
- `EffectId` includes session, source commit and nonzero action index. Effects
  retain their original source commit after ownership changes.
- `InboxPos` and `TaskCursor` contain backend/generation/scope and opaque tokens.
  They deliberately do not implement ordering. Only the provider interprets
  positions. Old tokens are not portable across a backend migration without an
  explicit translation or restore boundary.
- `TaskSnapshot.snapshot_event_id` is exact persisted coverage;
  `TaskView.covered_through` is the cut after reconstruction. These are distinct
  from the newest event known to exist.
- Pending dispatch input sequence and holder event sequence are separate
  namespaces. Removing a pending input must not remove its deduplication proof.
- `RequestAccepted`, `ConfirmedCanceled` and `OutcomeUnknown` remain distinct.
  Unknown is not a successful or failed terminal result.

Serializable top-level records carry schema/format versions; nested enums use
explicit stable tags. Readers must reject unsupported versions and unknown
variants rather than guess. Serde decoding alone does not validate scope, digest,
monotonicity, size limits or a supported version. Those checks belong to the
owning service/provider. Credentials are references, never secret values.

## Compatibility and checks

No existing records are migrated and no old wire API is replaced. This initial
format is not yet frozen for deployment. Subsequent persisted changes require an
explicit version/reader/migration decision before a provider writes them.

`tests/persistence_shape.rs` checks representative persisted identities/tags.
The `InboxPos` compile-fail example prevents accidental business ordering of
backend positions. These checks do not prove provider durability or concurrency.

See [change spec](../../../specs/2026-11-15-distributed-contracts/spec.md) and
[ordered tasks](../../../specs/2026-11-15-distributed-contracts/tasks.md).
