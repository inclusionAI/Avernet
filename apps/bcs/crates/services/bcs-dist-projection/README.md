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
not a deletion. No implicit JSON merge is performed. Thinking is excluded from
display, persistent accounting and publication cadence. A persisted Thinking unit is corrupt input.

`ArtifactUpdate` is shared by Show and Artifact events. Its Payload may be a Ref
for durable transport; projection accepts only resolved Inline values. The
caller must read and verify immutable references before projection. Complete
snapshots are externalized as one Payload outside their inner display encoding.

`project_candidate` takes the confirmed snapshot and cumulative usage, returning
a new candidate without changing either input. The Worker commits these together
with consumed and outbox. A failed candidate is discarded; an unknown commit
keeps the same candidate. `fold_committed` applies the identical transform to
already committed events without charging cumulative usage again. It still
checks the frozen format, identity and count limits. The reader
selects events after exact snapshot coverage and deduplicates event identities;
AppendText itself is not idempotent. Invalid committed input is a recovery error,
not permission to terminate a new task as though it produced that output.

## Frozen budgets and cost

The [phase-one profile](../../contracts/bcs-dist-model/src/output.rs) freezes
64 KiB inline, 1 MiB checkpoint and 256 KiB outbox frames. Normal candidates
reserve at least 64 KiB in both checkpoint and outbox for finalization. The
profile also bounds actions, units, queued tasks, dispatches, identities,
reference metadata and private Runner state. Smaller valid metadata/count
profiles may be selected before admission; tasks retain their frozen profile.
`validate_task_limits` checks the copy between TaskSnapshot and checkpoint
progress. It does not validate ownership or authorize a write.

Output content has no per-unit, cumulative or full DisplaySnapshot byte quota.
Large values and snapshots use the existing immutable Blob references so the
physical checkpoint/outbox frames remain bounded. Cumulative usage counts the
UTF-8 JSON encoding of complete Upsert values and AppendText deltas, including
quotation/escaping. It is diagnostic, saturates at `u64::MAX`, and gives no refund
on replacement. `charged_bytes` is the candidate's logical payload count;
publication's 64 KiB threshold counts complete encoded output events.

Limits validation establishes the profile's numeric relationships and terminal
reserve. It does **not** prove that a host's private wrapper, maximum checkpoint,
reference metadata and bounded finalization candidates fit. Runtime assembly
must measure those encodings and reject an incompatible profile/provider before
starting. It must also enforce queue/dispatch/Runner/outbox limits; this pure
projection enforces only its own format, metadata and display-count bounds.

Projection makes zero database/network calls. It validates snapshot metadata,
clones the resolved display and processes at most 32 changes in the initial
profile. Exact JSON payload counting does not allocate an encoded copy.
Identity lookup is bounded by 128 units. Payload memory and CPU cost grow with
actual content; this is not a bounded-memory or throughput guarantee. Read-side
recovery chunks event pages to the frozen action bound. Full snapshot persistence
still has cumulative O(n²) cost for growing output; this transform does not claim
immutable chunking or eliminate that IO.

## Compatibility and verification

These changes refine the unpublished distributed v1 declarations. They require
a fresh phase-one namespace: old implicit Artifact content, unbound RunnerState,
or records without frozen profiles are not silently migrated/defaulted. Existing
HTTP protocols and deployed legacy coordination formats are unchanged. Runtime
integration and provider durability are separate from this pure service's tests.

```sh
cargo +1.94.0 test --manifest-path apps/bcs/Cargo.toml -p bcs-dist-projection -p bcs-dist-model --locked --offline
cargo +1.94.0 clippy --manifest-path apps/bcs/Cargo.toml -p bcs-dist-projection --all-targets --locked --offline -- -D warnings
```

Tests cover candidate atomicity, identity/order, null and type errors, escaped
UTF-8 bytes, cumulative versus restore accounting, metadata/count limits, output
beyond the former byte quotas and profile immutability. See the
[runtime SPEC](../../../specs/2026-11-18-worker-runtime-phase1/spec.md) for the
remaining commit, publication, archival and recovery integration requirements.
