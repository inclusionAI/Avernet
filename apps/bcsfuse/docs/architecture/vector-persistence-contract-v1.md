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
contract do not support incremental synchronization.

## Bounded rebuild reads

`QdrantMySQLVectorStore.rebuild_from_backend()` requires the optional
`BatchedVectorPersistenceBackend.iter_batches(batch_size)` contract. It yields
non-empty pages of at most `batch_size` active `VectorPoint` values in ascending
primary-key order. `batch_size` must be positive. Providers must bound the SQL
result itself, not query the full table and split the result in Python.

The consumer takes an incremental checkpoint before scanning pages, applies
each page to the local dense and keyword indexes, removes stale local IDs, and
replays changes since that checkpoint. Incremental providers used by this
store must also implement
`BatchedIncrementalVectorPersistenceBackend.iter_change_batches_since(checkpoint,
batch_size)`, which yields at most `batch_size` mutations per page in
`(gmt_modify, id)` order, including tombstones. Rebuild catch-up and periodic
incremental sync use this bounded feed; the local checkpoint advances only
after a page is applied. A failed page is retried from the last checkpoint,
with timestamp overlap making replay idempotent. The existing `load_all()` method remains
available for other callers, but the Qdrant rebuild no longer calls it. A
provider without the required paging methods fails before the rebuild changes the index.
Decode or storage failures must propagate rather than yield a partial page.
Internal ZDAS and public MySQL providers exclude `is_deleted = 1` rows; SQLite
has no soft-deletion column.

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

The public MySQL backend also rejects undecodable live rows during `load_all()`;
a rebuild must not silently acknowledge a partial snapshot. Incremental
tombstones need only their ID and timestamp and do not decode deleted content.

## Compatibility guidance

- Text search is not part of this durable persistence protocol.
  `QdrantMySQLVectorStore` supports keyword recall through a local sparse TF-IDF
  index rebuilt from persisted payloads and maintained during incremental replay;
  it is not BM25. See [the composition contract](composition-contract.md) for
  the keyword recall and ranking contract. Retiring the separate legacy AGENT
  `SparseRetriever` does not remove this current keyword capability or change
  dense search, durable persistence, or incremental replay semantics.
- Existing providers may continue to implement only
  `VectorPersistenceBackend`, but the Qdrant rebuild also requires the bounded
  page contract.
- Incremental providers must satisfy both protocols and return
  `VectorChangeSet` exactly as defined above.
- Adding optional metadata to a `VectorPoint.payload` is compatible.
- Changing checkpoint type, replay guarantees, deletion precedence, or
  tombstone retention is incompatible and requires a new contract version.

The public MySQL implementation and the injected-backend tests under
`tests/contract/test_mysql_vector_persistence_backend_contract.py` and
`tests/unit/infra/test_qdrant_durable_vector_store.py` are the reference
compatibility coverage for v1.
