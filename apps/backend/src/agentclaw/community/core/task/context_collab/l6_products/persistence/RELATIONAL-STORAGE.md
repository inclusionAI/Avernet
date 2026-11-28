# Relational business storage — implementation boundary

Owner: Backend context_collab L6. The ProductProfileService creation/read seam is
now wired through [ProductBusinessRepositoryAdapter](product_business_repository_adapter.py)
to these five business tables. This is phase one, not a completed execution
cutover: profile/Collet creation and business reads use `ac_context_collab_*`, while
runtime advances still fail closed until the [change specification](../../../../../../../../specs/2026-11-27-context-collab-relational-product-storage/spec.md)
resolves the progress/feedback/control contracts. The adapter may project a
`RuntimeSnapshot` for current reads, but it does not fabricate attempts, leases,
routes, receipts or MultiRollout controller facts, and refuses to save such state.

## Implemented responsibilities

- [Models](product_relational_models.py) define the integer-ID Profile, rollout
  and stage tables and attach the shared Collet/Event indexes. Relationships
  use ID columns and ordinary/unique indexes, with **no physical foreign keys**
  anywhere in this five-table schema, including the Collet/Event ALTERs. Every
  ordinary index introduced by this schema uses the required `idx_` prefix;
  `ac_context_collab_stage_rollout` has **no secondary unique key**. L4 defines
  the four Collet/two Event columns but never imports L6 models. A schema
  installer must load this L6 module before creating the business table group.
- [SQL](../../sql/2026_11_27_product_relational_storage.sql) materializes the
  MySQL/InnoDB additive migration. It requires the existing L4 migrations;
  it is not run automatically. Every column introduced or created by that DDL has
  a meaningful MySQL `COMMENT`. SQLite tests explicitly create `BUSINESS_TABLES`
  only, with SQLite foreign-key checking enabled and no declared foreign keys.
  This is not evidence that a target deployment has applied the migration or
  retired historical `ac_product_*` tables. Current product service DI binds this
  business repository adapter, not the legacy `SqlProductRuntimeRepository`.
- The shared L4 Collet model matches the deployed physical schema exactly. Its
  one non-null JSON `context_spec` column is the versioned envelope for scope,
  audit metadata, resource bindings, creation facts and the root-Task pointer;
  no separate physical columns exist for those values. Product creation writes
  an empty version-2 envelope and never relies on `ac_product_*` storage.
- [SqlProductBusinessRepository](product_relational_repository.py) provides
  independent Profile creation/read, atomic Collet + initial rollout + static
  stages + creation audit, and authorized consistent business-history reads.
- [ProductBusinessRepositoryAdapter](product_business_repository_adapter.py) is
  the new `ProductProfileRepository`/`RolloutRepository` binding. Profile and
  Collet creation use the business repository; `get_collet`/`locate` reconstruct
  only facts represented by the five tables. `replay`, `save`, delivery, late
  delivery and intent methods are not silently backed by legacy `ac_product_*`
  storage: the unsupported mutation paths raise explicit validation errors.
  No worker, receipt, dispatch, acceptance, feedback or cancellation writer is
  installed by this phase. Database failures propagate and roll back.

- [ProductProfileRecord](../profile_record.py) carries stored description,
  lifecycle and audit metadata separately from the four-field execution
  definition. Legacy snapshot reads do not invent this metadata.
  `encode_profile_response` is a future delivery encoder, not yet HTTP-wired.
- New Profiles are explicitly `DRAFT`; reads reject unknown lifecycle values.
  `creator` and `modifer` are trusted `principal_type:principal_id` audit strings;
  `creator` is projected as the Profile owner for the service's update
  authorization. `update_profile` locks the Profile row, validates the transition
  and preserves `creator` while writing `modifer`. DRAFT Profiles may edit
  name/description/stages and transition to ONLINE; ONLINE Profiles may only
  transition to OFFLINE. `DRAFT -> OFFLINE` and `OFFLINE -> ONLINE` are rejected,
  and ONLINE/OFFLINE content edits fail closed. There is no automatic
  `DRAFT -> ONLINE` path and no versioned Profile history. Only `ONLINE` admits
  new business Collets, checked under the same Profile-row lock order.
- Configuration is closed, bounded [YAML](../product_profile_service/profile_config_yaml.py)
  using `product-profile/config@2`. New writes normalize accepted stages into a
  readable structured YAML list; existing `config@1` rows, where `stages` is a
  string, remain readable. Profile `config`
  uses `TEXT`; its complete serialized YAML is limited to 65535 UTF-8 bytes on
  writes and reads, with no truncation. The non-unique scope/name index is
  `idx_cc_profile_scope_name` over `(avernet_tenant, env, name)`. Pure structural
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

## Application integrity boundary

The repository creates Profile/Collet/rollout/stage relationships atomically,
validating the locked Profile and real inserted IDs before commit. Business
reads reject missing parents, invalid current-rollout membership, broken
predecessor chains, duplicate stage names and incomplete/wrong-scope stages.
The creation audit writer checks Collet/current/rollout membership before
adding an Event. Ordinary/unique indexes remain in place, but do not validate
parent existence.

There is no database `RESTRICT` or cascading relationship behavior. Direct SQL
can create orphan records or mismatched Event stage links; it must not bypass
the required application checks. Stage/feedback Event writers and deletion APIs
are **not implemented** by this foundation. They must validate scope and parent
membership transactionally before being enabled; removing foreign keys is not
evidence that those future paths already have equivalent protection.

## Schema compatibility and deployment warning

The old versioned-string Profile model now points at
`ac_context_collab_product_profile_legacy_v1`. Existing installations **must**
stop writers, inspect both names and perform the SQL file's conditional rename
before deploying that model change. Fresh installations skip the rename. Never
use `IF NOT EXISTS` or reader fallback to hide an incompatible same-name table.
The old published definitions, canonical bytes and digests remain unchanged.

For an installation of the earlier five-table schema, changing the bootstrap
SQL does not alter an existing table. Stop writers and check that every
`OCTET_LENGTH(config) <= 65535` before narrowing the column to `TEXT` and renaming
the old scope/name index to `idx_cc_profile_scope_name`. Oversize configurations
require manual review; never truncate them to complete the upgrade.

For an installation that already has the earlier seven foreign keys, changing
the bootstrap SQL does not remove them. Stop writers, inspect the actual schema
and have the reviewed upgrade drop only existing constraints listed in the
[specification](../../../../../../../../specs/2026-11-27-context-collab-relational-product-storage/spec.md#38-已安装旧外键结构的升级边界),
preserving all relationship columns and indexes. A partially applied migration
must be inspected before resuming; do not blindly repeat every ADD COLUMN.

The same bootstrap change does not rename or remove existing database indexes.
An installation with the earlier `ix_cc_*` ordinary indexes or the removed
`uk_cc_stage_name` / `uk_cc_stage_parent_id` Stage unique keys needs a reviewed
upgrade: rename/remove those indexes only after inspecting the actual schema,
and clean or isolate duplicate stage names before dropping the Stage
constraints. Do not delete relationship columns or the retained `idx_cc_*`
indexes during that upgrade.

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
4. Full runtime cutover, including HTTP response/progress semantics,
   administrative state management, historical data migration, target
   MySQL/OceanBase locking/DDL and live provider integration. Phase-one service
   DI writes and reads the five business tables, but that does not replace the
   missing runtime contract.

The [business-history contract](../../l5_methods/product_business_storage.py)
intentionally has **no** RuntimeSnapshot/revision/attempt/route/control defaults.
There is no process-local recovery cache or substitute control/receipt table.
The remaining decisions must be adopted with consumers and tests before the full
runtime cutover.
