# Relational business storage — implementation boundary

Owner: Backend context_collab L6. This is the **storage foundation**, not a
completed execution cutover. The [change specification](../../../../../../../../specs/2026-11-27-context-collab-relational-product-storage/spec.md)
records the remaining runtime/progress/feedback contracts. The existing
[ProductProfileService](../product_profile_service/README.md) and DI still use
snapshot v2; do not bind its `RolloutRepository` to the business repository.

## Implemented responsibilities

- [Models](product_relational_models.py) define the integer-ID Profile, rollout
  and stage tables and attach the shared Collet/Event foreign keys. L4 defines
  the four Collet/two Event columns but never imports L6 models. A schema
  installer must load this L6 module before creating the business table group.
- [SQL](../../sql/2026_11_27_product_relational_storage.sql) materializes the
  MySQL/InnoDB additive migration. It requires the existing L4 migrations;
  it is not run automatically. SQLite tests explicitly create `BUSINESS_TABLES`
  only, with foreign keys enabled. This is not evidence that global app
  `Base.metadata.create_all` or DI has retired the old runtime tables.
- [SqlProductBusinessRepository](product_relational_repository.py) provides
  independent Profile creation/read, atomic Collet + initial rollout + static
  stages + creation audit, and authorized consistent business-history reads.
  No worker, receipt, dispatch, acceptance, feedback or cancellation writer is
  installed by this foundation. Database failures propagate and roll back.
- [ProductProfileRecord](../profile_record.py) carries stored description,
  lifecycle and audit metadata separately from the four-field execution
  definition. Legacy snapshot reads do not invent this metadata.
  `encode_profile_response` is a future delivery encoder, not yet HTTP-wired.
- New Profiles are explicitly `DRAFT`; reads reject unknown lifecycle values.
  `creator` and `modifer` are trusted `principal_type:principal_id` audit strings,
  not ACLs. Publication and [use authorization](../../g1_secure_govern/profile_use.py)
  are independent; the default use policy denies. Runtime read authorization
  is separately injected. Only `ONLINE` admits new business Collets, checked
  under a Profile row lock. There is **no** new publish/update endpoint or
  automatic `DRAFT -> ONLINE` path.
- Configuration is closed, bounded [YAML](../product_profile_service/profile_config_yaml.py)
  containing exact composition and the original stages string. Pure structural
  compilation is separate from external capability validation. Storage creation
  is **not** runtime capability admission; the eventual service must still
  validate installed execution/evidence/strategy/isolation capabilities.
- Business reads use explicit transactions: SQLite `BEGIN`, MySQL
  `REPEATABLE READ`. SQLite writes use `BEGIN IMMEDIATE`, respecting a
  transaction already opened by the DatabasePlugin instead of issuing a nested
  BEGIN. MySQL creation locks Profile before inserting root/children. Other SQL
  dialects fail closed rather than claiming untested locking behavior.
- Stage order is reconstructed from exact YAML names, not row IDs. Missing,
  duplicate, wrong-scope or unknown children fail. Reads do not repair rows.
  Evidence remains strict JSON; acceptances are limited to 8192 serialized
  characters, error text to 65535 UTF-8 bytes. Aggregate reads/creation reject
  more than 12 MiB instead of truncating history.
- Product audits have no L4 Task/version facts and do not advance context_version.
  L4 row/Task decoders reject product roots, and L4 append reserves the
  `product_runtime.*` namespace. Independent L4 rows retain NULL product columns.

## Schema compatibility and deployment warning

The old versioned-string Profile model now points at
`ac_context_collab_product_profile_legacy_v1`. Existing installations **must**
stop writers, inspect both names and perform the SQL file's conditional rename
before deploying that model change. Fresh installations skip the rename. Never
use `IF NOT EXISTS` or reader fallback to hide an incompatible same-name table.
The old published definitions, canonical bytes and digests remain unchanged.

Neither SQL/data migration nor deployment has been executed. Do not enable a
mixed writer fleet, bulk-import in-flight runtime snapshots, or resume missing
control/route facts merely because the business graph has been inserted.

## Explicitly not implemented

1. Replacement execution/worker/progress protocol without attempts, aggregate
   revisions, dispatch phases or leases; trustworthy full session-route lookup;
   late-result, unknown-delivery, cancellation and recovery guarantees.
2. MultiRollout strategy fences, hard budgets/deadlines, stop/reuse facts,
   automatic observation/acceptance writes and human-gated successor commits.
3. Cross-context feedback UUID arbitration. `feedback_event_id` implements the
   specified deterministic identity only. The existing Event unique index
   includes context; tests demonstrate that the same ID can exist in two
   contexts. No accepted-feedback writer is exposed.
4. Normal DI/metadata retirement of old `ac_product_*`, HTTP response/progress
   cutover, administrative state management, historical data migration, target
   MySQL/OceanBase locking/DDL and live provider integration.

The [business-history contract](../../l5_methods/product_business_storage.py)
intentionally has **no** RuntimeSnapshot/revision/attempt/route/control defaults.
There is no process-local recovery cache or substitute control/receipt table.
The remaining decisions must be adopted with consumers and tests before cutover.
