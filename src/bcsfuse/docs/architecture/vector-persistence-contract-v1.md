# Incremental Vector Persistence Contract v1

This document defines version 1 of the optional incremental change-feed
contract for replaceable vector persistence providers.

The Python authority is:

```python
IncrementalVectorPersistenceBackend.load_changes_since(
    checkpoint: float,
) -> VectorChangeSet
```

`IncrementalVectorPersistenceBackend` extends the baseline
`VectorPersistenceBackend`. Providers that implement only the baseline
contract remain compatible with full index rebuilds, but do not support
incremental synchronization.

## Change-set semantics

`VectorChangeSet` contains:

- `upserts`: the current complete `VectorPoint` values changed after the
  requested checkpoint.
- `deleted_ids`: durable tombstones for IDs deleted after the checkpoint.
- `checkpoint`: a non-decreasing durable watermark covering every mutation
  returned in the change set.

The provider must not advance `checkpoint` past a committed mutation that it
did not return. An empty result returns a checkpoint greater than or equal to
the requested checkpoint.

Providers may replay mutations at the checkpoint boundary to avoid losing
same-timestamp writes. Consumers therefore apply changes idempotently by vector
ID. Within one change set, deletions take precedence when an ID appears in both
`upserts` and `deleted_ids`.

## Ordering and deletion retention

Providers return mutations in a stable order. Timestamp-backed providers use
the durable modification time followed by vector ID as the tie breaker.

Deletion must leave a tombstone visible to incremental readers for every
supported consumer-lag window. Physically deleting a record before all readers
can observe its ID violates this contract and can leave stale vectors in local
indexes.

## Failure behavior

Storage, decoding, and consistency failures propagate to the caller. A
provider must not return a partial change set with a checkpoint that skips the
failed mutation. Consumers advance their local checkpoint only after applying
the returned upserts and deletions successfully.

## Compatibility guidance

- Existing providers may continue to implement only
  `VectorPersistenceBackend`; callers must use a full rebuild for them.
- Incremental providers must satisfy both protocols and return
  `VectorChangeSet` exactly as defined above.
- Adding optional metadata to a `VectorPoint.payload` is compatible.
- Changing checkpoint type, replay guarantees, deletion precedence, or
  tombstone retention is incompatible and requires a new contract version.

The public MySQL implementation and the injected-backend tests under
`tests/contract/test_mysql_vector_persistence_backend_contract.py` and
`tests/unit/infra/test_qdrant_durable_vector_store.py` are the reference
compatibility coverage for v1.
