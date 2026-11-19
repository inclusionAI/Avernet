# Immutable runtime archive

`DbArchiveStore` implements the public `ResultStorePort` and `BlobStorePort` using
the host-selected `DbPlugin`. Its policy and SQL live in Avernet; a host supplies
the database adapter and configuration. This implements the persistence portion
of [Worker runtime SPEC §5–6](../../../../../specs/2026-11-18-worker-runtime-phase1/spec.md).
The Worker still owns AwaitingResult, the second business commit and terminal
publication ordering. The provider does not perform those transitions.

SQLite migration 047 and MySQL migration 049 add `bcs_dist_immutable_objects`.
Existing migrations are unchanged. Namespace and object identity hashes form
the primary key; every read also checks the complete logical keys, exact bytes
and content digest. Result identities include tenant, principal and task ID.
Blob identities are caller-fixed keys. `blob_reference` computes a domain-separated
SHA-256 digest with the shared length-prefixed field encoding; callers retain the
exact bytes before committing a reference. The `result:` key prefix is reserved.

Before first saving a result, `EffectVerifier` reads its durable checkpoint and
checks the exact batch identity, source sequence, consumed coverage, effect ID,
digest and full body. A receipt string or a claimed owner is not authority.
Sealing a successor owner does not revoke already committed effects. Successful
archives retain stable provenance with the content, so exact retries remain
verifiable after the checkpoint outbox advances. Different content or provenance
under an existing result identity is `ContentConflict`.

The provider verifies snapshot/result Blob references exist with their expected
digests before saving a new final record. It does not publish a terminal state or
invent a result when a referenced object is unavailable. Orphaned Blob objects,
results and completion evidence are retained in phase 1: no TTL, delete or GC
path is exposed.

An insert error, including a duplicate-key error, triggers one exact row read.
Matching bytes and provenance resolve to `AlreadyApplied`/`AlreadySaved`; changed
content conflicts. Absence or a failed resolution read remains `OutcomeUnknown`.
The provider never automatically repeats the insert or derives failure from a
driver error string. A later caller retry retains the original immutable key.

## Bounds and database cost

The provider imposes no semantic Blob or final-record payload byte quota.
Key metadata remains at most 1024 bytes and provenance at most 16 KiB. Reads
select at most one complete primary-key row and verify its digest. Existing
SQLite BLOB and MySQL LONGBLOB columns remain unchanged; actual database,
driver, packet and available-memory limits still apply. Read failures propagate
as storage failures; insert failures retain the existing exact-resolution /
OutcomeUnknown protocol. No provider success is invented when storage fails.

Removing byte quotas leaves row counts and round trips unchanged, but each
operation can transfer and allocate more bytes. Host capacity validation must
account for actual output sizes; no maximum-size throughput claim is made.

| Operation | SQL round trips | Other reads |
| --- | --- | --- |
| New Blob | 1 lookup + 1 insert | none |
| Exact Blob retry | 1 lookup | none |
| New final result | 1 lookup + 1 insert | 1 checkpoint; at most 2 Blob lookups |
| Exact final retry | 1 lookup | none |
| Insert error/unknown | one additional resolution lookup | no automatic insert retry |
| Outbox changes during verification | one additional archive lookup | resolves concurrent completion |
| Get | 1 lookup, at most 1 row | none |

No database transaction remains open across store reads. Worker-level IO admission
must count these operations against its shared pool budget. Small correctness
tests do not establish a deployment throughput or latency guarantee.

## Verification

`cargo +1.94.0 test --manifest-path apps/bcs/Cargo.toml -p bcs-dist-store --test archive --locked --offline`
tests real file-backed SQLite with the production provider and a fault-injecting
driver wrapper. It covers immutable/conflicting writes, bytes and final records
beyond the former quotas,
read/write counts, persisted effect authorization, scope/namespace separation,
dangling references, lost replies, absent-row unknown results, concurrency,
provider restart and opening another database connection pool.

`cargo +1.94.0 test --manifest-path apps/bcs/Cargo.toml -p bcs-db-migrations --locked --offline`
checks the complete SQLite chain, retained history and rows, repeatability,
unique identities and static MySQL migration shape. Static checks do not claim
real MySQL/OceanBase acceptance; that host validation is separately deferred.
