# Distributed task output projection

`OutputProjectionService` implements the pure
[`OutputProjectionCoreService`](../../service-api/bcs-service-api/src/core/dist_projection.rs).
Worker candidates, event folding and TaskFeed restore inject the same contract;
they do not import each other's storage or runtime implementation. This crate has
no storage, transport, clock, random IDs, background tasks or default Runner.

## Encoding and state

[`DisplaySnapshot`](../../contracts/bcs-dist-model/src/output.rs) format 1 is an
ordered list of resolved display units identified by `(node, artifact_id, class)`.
Upsert replaces the complete JSON value and preserves the unit's first position.
AppendText creates a missing text unit or appends to an existing string; a
non-string target/delta rejects the entire candidate. Null remains a JSON value,
not a deletion. No implicit JSON merge is performed. Thinking is bounded as an
individual event but excluded from display, persistent quota and publication
cadence. A persisted Thinking unit is corrupt input.

`ArtifactUpdate` is shared by Show and Artifact events. Its Payload may be a Ref
for durable transport; projection accepts only resolved Inline values. The
caller must read and verify immutable references before projection. Complete
snapshots are externalized as one Payload outside their inner display encoding.

`project_candidate` takes the confirmed snapshot and cumulative usage, returning
a new candidate without changing either input. The Worker commits these together
with consumed and outbox. A failed candidate is discarded; an unknown commit
keeps the same candidate. `fold_committed` applies the identical transform to
already committed events without charging cumulative usage again. It still
checks the frozen format, unit, count and complete snapshot limits. The reader
selects events after exact snapshot coverage and deduplicates event identities;
AppendText itself is not idempotent. Invalid committed input is a recovery error,
not permission to terminate a new task as though it produced that output.

## Frozen budgets and cost

The [phase-one profile](../../contracts/bcs-dist-model/src/output.rs) freezes
the 1 MiB unit, 8 MiB cumulative output, 8 MiB full snapshot, 64 KiB inline,
1 MiB checkpoint and 256 KiB outbox limits. Normal candidates reserve at least
64 KiB in both checkpoint and outbox for finalization. The profile also bounds
actions, units, queued tasks, dispatches, identities, reference metadata and
private Runner state. Smaller valid profiles may be selected before admission;
existing tasks retain their complete frozen profile. `validate_task_limits`
checks the profile copied between initial TaskSnapshot and checkpoint progress.
It does not validate ownership or authorize a write.

Cumulative output charges the UTF-8 JSON encoding of each resolved complete
Upsert value or AppendText delta, including string quotation/escaping. It never
refunds replacement. Unit bytes count the current resolved value. Snapshot bytes
include the entire public encoding with identities, array order and format.
`charged_bytes` is the candidate's logical payload charge, not an event-size
estimate: publication's 64 KiB threshold uses complete encoded output events.

Limits validation establishes the profile's numeric relationships and terminal
reserve. It does **not** prove that a host's private wrapper, maximum checkpoint,
reference metadata and bounded finalization candidates fit. Runtime assembly
must measure those encodings and reject an incompatible profile/provider before
starting. It must also enforce queue/dispatch/Runner/outbox limits; this pure
projection enforces only its own input and display bounds.

Projection makes zero database/network calls. It validates and clones one
bounded snapshot, then processes at most 32 changes (initial profile). It counts
JSON through a bounded writer without allocating an encoded copy. Snapshot size
is adjusted by exact changed-value bytes and newly inserted unit metadata, so
each action does not re-encode the entire snapshot. Identity lookup is bounded
by 128 units. Read-side recovery chunks larger event pages to the frozen action
bound. Full snapshot persistence still has cumulative O(n²) cost for growing
output; this transform does not claim immutable chunking or eliminate that IO.

## Compatibility and verification

These changes refine the unpublished distributed v1 declarations. They require
a fresh phase-one namespace: old implicit Artifact content, unbound RunnerState,
or records without frozen quotas are not silently migrated/defaulted. Existing
HTTP protocols and deployed legacy coordination formats are unchanged. Runtime
integration and provider durability are separate from this pure service's tests.

```sh
cargo +1.94.1 test --manifest-path apps/bcs/Cargo.toml -p bcs-dist-projection -p bcs-dist-model --locked --offline
cargo +1.94.1 clippy --manifest-path apps/bcs/Cargo.toml -p bcs-dist-projection --all-targets --locked --offline -- -D warnings
```

Tests cover candidate atomicity, identity/order, null and type errors, escaped
UTF-8 bytes, cumulative versus restore accounting, metadata/count limits, a
maximum legal unit workload and profile immutability. See the
[runtime SPEC](../../../specs/2026-11-18-worker-runtime-phase1/spec.md) for the
remaining commit, publication, archival and recovery integration requirements.
