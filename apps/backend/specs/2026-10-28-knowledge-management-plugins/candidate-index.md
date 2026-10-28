# Candidate Bot scope index

## Approved scope and design

Add only a non-unique `BotModel` index plus an operator-applied migration and
real repository/tenant-guard regression. Do not change selection, permissions,
identity uniqueness, keyword semantics or pagination. Alternatives considered:
`LIMIT` alone does not bound scanning; forcing an index in SQL is backend-specific
and cannot supply a missing index. A schema index is the minimal portable fix.

```sql
CREATE INDEX idx_bot_knowledge_candidates
    ON ac_bots (avernet_tenant, env, space_id, is_delete, id);
```

Equality predicates precede the ordered ID seek. The trailing ID is explicit for
schema clarity and SQLite parity even where an engine implicitly appends its PK.
The existing identity unique key remains unchanged. New tables use ORM metadata;
`create_all` does **not** install indexes on an already existing table.

## Existing-database rollout (operator only)

1. Inspect `SHOW CREATE TABLE ac_bots` and `SHOW INDEX FROM ac_bots` in each
   deployment's actual database. Confirm all columns, available/visible index
   state, full-column order (no prefixes), and engine/tenant compatibility.
2. If an equivalent available index already exists under any name, reuse it and
   skip this DDL. If the proposed name has a different definition, stop: do not
   drop or blindly replace it. This migration is intentionally not automatically
   idempotent; unconditional retries must not hide an incomplete/wrong index.
3. Have the database owner review build duration, locks, capacity and write
   amplification. Choose engine-supported online options through its normal
   change process; this portable script promises no lock-free build.
4. Apply `core/knowledge_management/sql/2026_10_28_bot_candidates.sql` before
   treating the candidate performance acceptance as complete. No boot-time DDL.
5. Confirm the index is ready/visible and inspect the actual emitted ORM SELECT
   with `EXPLAIN`, including a nonzero cursor, an empty/sparse space and keyword
   requests. Tenant/environment/space/delete predicates should bound the Bot
   access range. Joins/optimizer choices can still introduce sorts; do not infer
   a production plan or latency guarantee from the SQLite regression.
6. Smoke-test candidates, keyword filtering and cursor continuation. If reverting
   application code, retaining this non-unique index is compatible. Remove only
   this new index if the owner explicitly approves rollback, after checking other
   readers: `DROP INDEX idx_bot_knowledge_candidates ON ac_bots;`. Never drop an
   existing unique key or the prior space index as part of this fix.

## Proof and limits

Tests execute the unmodified repository and injected tenant guard. They capture
the actual SELECT, inspect SQLite `EXPLAIN QUERY PLAN` and count VM instructions
with 1k/10k/100k unrelated rows. Sparse continuation also includes the legacy
single-space index and other-tenant/environment/deleted rows **in the same
space**. A missing composite index must fail, not merely satisfy a returned-row
limit. The checked-in SQL is applied locally to an existing-table fixture and
compared with ORM/MySQL DDL.

Substring keyword filtering remains within the active scoped Bots, not a
full-text indexed or constant-cost search across a large target space. No DDL
execution, production optimizer-after-DDL result, load test or deployment is
claimed by this change.
