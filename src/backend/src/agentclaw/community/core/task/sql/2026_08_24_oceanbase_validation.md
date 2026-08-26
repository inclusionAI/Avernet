# OceanBase Migration Validation Checklist — Task Graph Shared Persistence

- **Spec:** `src/backend/specs/2026-08-24-task-graph-shared-persistence/spec.md`
- **Migration files:**
  - `2026_08_24_task_graph_shared_persistence.sql` — additive `task_info` graph metadata/version/lease columns, `task_callback` event-idempotency columns, `task_node`/`task_node_relation` natural-key uniqueness, recovery indexes, and the new `task_action_log` table.
  - `2026_08_24_task_action_log.sql` — standalone operator DDL for the append-only action log (identical table definition; bundled inside the combined migration as well).
- **ORM parity:** `Base.metadata.create_all` (used by singlebox SQLite bootstrapping) is the canonical fresh-install shape. **Keep the DDL and the ORM models in sync** when changing either: column names/types, unique indexes (`uk_task_node_identity (task_id,node_id)`, `uk_src_dst (task_id,src_node_id,dst_node_id)`, `uk_task_callback_event (event_id)`, `uk_task_action_event (event_id)`, `uk_task_node_action_seq (task_id,node_id,seq)`), and the recovery/version indexes.

## Deployment steps (dev → pre → prod)

1. **Audit duplicates before enforcing uniqueness.** The new unique indexes
   (`uk_task_node_identity`, `uk_src_dst` widened to include `task_id`,
   `uk_callback_event`) will fail to create if duplicate rows already exist.
   Run the audit queries below; deduplicate or backfill before applying the
   migration.
2. **Apply the additive migration.** All changes are additive `ALTER … ADD
   COLUMN` / `CREATE INDEX` / `CREATE TABLE`, so they are safe to run before
   the application change. Old application instances ignore the new
   columns/table.
3. **Validate, then ship the application change** (dual-write → hydrate
   fallback → version-aware cache → DB-authoritative mutation → recovery).
4. **Rollback:** old application code continues using the in-memory graph and
   ignores the new columns/table. Do **not** drop the new columns/indexes until
   all old instances are drained.

## OceanBase (MySQL-compatible) behavior to verify in pre-release

These behaviors are the correctness basis for cross-instance graph storage and
**cannot be reproduced by singlebox SQLite** (SQLite ignores `SELECT … FOR
UPDATE`, has no distributed row locks, and uses a single in-process writer).
Run each check against a pre-release OceanBase tenant before enabling recovery
and DB-authoritative mutation in prod.

### V1 — `SELECT … FOR UPDATE` row lock serialization (BBS claim + recovery lease)

- **What it guarantees:** two concurrent claimers of the same `task_info` /
  root `task_node_run_info` row are serialized; the loser blocks, re-reads the
  winner's committed state, and conflicts. This is the mechanism in
  `TaskGraphRepository.claim_bbs_owner` (root `task_node_run_info` row lock) and
  the `save_graph` optimistic-version guard (`task_info … FOR UPDATE`).
- **Verify:**
  - Open two transactions T1, T2 on the *same* row.
  - `BEGIN; SELECT … FROM task_node_run_info WHERE task_id=? AND node_id=? FOR UPDATE;` in T1 (do not commit).
  - Run the same `SELECT … FOR UPDATE` in T2 → T2 must **block** until T1
    commits or rolls back.
  - Commit T1 (claiming `bbs_owner='A'`); T2 unblocks, reads `bbs_owner='A'`,
    and the application returns `False` (conflict). Exactly one winner.

### V2 — Transaction isolation and lost-update prevention (optimistic version)

- **What it guarantees:** `save_graph` does
  `SELECT task_info … FOR UPDATE` then `UPDATE … SET graph_version = v+1`; the
  row lock means a concurrent writer cannot observe a stale `graph_version`.
- **Verify:**
  - T1 and T2 both read `graph_version=0`.
  - T1 commits `graph_version=1` first.
  - T2's `SELECT … FOR UPDATE` must block, then read `graph_version=1` after
    T1 commits, so T2 raises `GraphVersionConflictError` instead of silently
    overwriting (no lost update).
  - Confirm the isolation level (`READ COMMITTED` or `REPEATABLE READ`) gives
    this behavior with the row lock; document the required level.

### V3 — TEXT column size

- `graph_output`, `graph_extend_props`, `task_action_log.payload`,
  `task_callback.orig_callback_data` / `execution_graph` / `result` are `TEXT`.
- Verify the largest realistic serialized graph + action payload fits the
  OceanBase `TEXT` limit for the configured tenant, and that `LONGTEXT` is not
  required. If a graph or callback body can exceed ~64 KB, switch the affected
  column to `LONGTEXT` (additive change, coordinate with DBA).

### V4 — Index length (varchar keys)

- The unique/indexed varchar keys (`event_id(256)`, `task_id(128)`,
  `node_id(128)`, `lease_owner(256)`, `instance_id(256)`) must fit OceanBase's
  per-index key length limit for the configured `utf8mb4` charset. Verify the
  combined `uk_task_node_action_seq (task_id,node_id,seq)` and
  `uk_task_callback_event (event_id)` create successfully; if the limit is
  exceeded, add a prefix length or shorten the column.

### V5 — SQLite equivalence baseline (already covered by unit tests)

These are the behaviors that ARE equivalently enforced on SQLite and are
continuously tested locally; prod parity depends on V1–V2 plus this:

- Event-idempotent unique constraint `uk_task_callback_event (event_id)`.
- Action-log ordering/idempotency `uk_task_node_action_seq (task_id,node_id,seq)`
  and `uk_task_action_event (event_id)`.
- Lease CAS one-winner via conditional `UPDATE … WHERE lease_until IS NULL OR
  lease_until < now` (rowcount == 1 → winner); see
  `test_recovery_lease_cas_one_winner`.
- Stale graph version rejected with `GraphVersionConflictError`; see
  `test_graph_version_rejects_stale_writer`.

## Pre-release validation status

| Check | Owner | Status |
| --- | --- | --- |
| Duplicate audit queries (task_node, task_node_relation, task_callback.event_id) | DBA + backend | TODO (pre) |
| V1 FOR UPDATE serialization | backend | TODO (pre) |
| V2 lost-update / isolation level | backend | TODO (pre) |
| V3 TEXT size | DBA | TODO (pre) |
| V4 index length | DBA | TODO (pre) |
| V5 SQLite equivalence | CI | ✅ automated |
