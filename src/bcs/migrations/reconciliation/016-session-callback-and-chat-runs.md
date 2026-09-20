# MySQL 016 consolidation and existing deployments

PR #2339 explicitly consolidates the duplicate 016 migrations before merge.
The agreed release scope is a working latest migration chain; automatic upgrades
from either split-016 lineage are outside this change. Subsequent changes to the
merged chain use new migrations. The diagnostic below remains intentional for
earlier deployments.

The active migration is
[`016_session_callback_lease_and_chat_runs.sql`](../mysql/016_session_callback_lease_and_chat_runs.sql).
It adds the three Session callback lease columns and recovery index, then creates
`bcs_chat_runs`. Its `ADD COLUMN` syntax is compatible with MySQL 8. Apply it once
through the versioned runner; MySQL DDL can commit before a later statement fails.

The original files are preserved byte-for-byte outside migration discovery:

| Historical name | Archive | SHA-256 |
| --- | --- | --- |
| `session_callback_lease` | [original callback 016](../legacy/mysql/016_session_callback_lease.sql) | `1f06ddc25a84418ec12fb2b6936f35750489996e0a9adbf35df1ef8151712abe` |
| `chat_runs` | [original Chat 016](../legacy/mysql/016_chat_runs.sql) | `f1f448f49d5cc00aaebe22e3810cd15366484c88c141e0122ee0b297fa13cfd5` |

The merged name is `session_callback_lease_and_chat_runs`; its SHA-256 is
`8750570715d9ef27ddd81abe0b98dafa272cb9ab9a13220fcb5c932511496cb6`.
These hashes describe repository bytes, not proof of a database's schema.
Platform-specific DDL/checksums must be reconciled against that deployment's
original release artifacts; do not substitute a repository checksum blindly.

## Inspect before choosing a path

Run the following read-only queries against the intended database and save the
results with the deployment's original SQL and migration record, including
`applied_at`. Pause concurrent schema deployments during reconciliation.

```sql
SELECT version, name, dialect, checksum, applied_at
FROM bcs_schema_migrations WHERE version = 16;

SELECT table_name, column_name, column_type, is_nullable, column_default, extra
FROM information_schema.columns
WHERE table_schema = DATABASE()
  AND table_name IN ('bcs_group_sessions', 'bcs_chat_runs')
ORDER BY table_name, ordinal_position;

SELECT table_name, index_name, non_unique, seq_in_index, column_name, sub_part
FROM information_schema.statistics
WHERE table_schema = DATABASE()
  AND table_name IN ('bcs_group_sessions', 'bcs_chat_runs')
ORDER BY table_name, index_name, seq_in_index;
```

For tables present in the output, also save `SHOW CREATE TABLE`. Compare types,
nullability, defaults, key order/uniqueness and collation with the merged DDL,
accounting for separately recorded later migrations. A matching column name or
`CREATE TABLE IF NOT EXISTS` success alone does not validate its definition.

| Observed history and schema | Required action |
| --- | --- |
| No 016 row; all three callback columns/index and Chat table absent | Use the merged migration once via `--only 16 --apply`, after validating the pre-016 prerequisites. |
| Original callback 016 row | Verify the callback portion. If the Chat table is absent, apply only the merged file's Chat `CREATE TABLE`; if present, verify its complete definition. |
| Original Chat 016 row | Verify the Chat table. Apply only the missing callback columns/index from the merged file. Do not replay columns/indexes already present. |
| Either old row; both schema portions complete | No DDL is needed; metadata reconciliation still needs an explicit deployment decision. |
| No row but some/all objects exist, unknown name/hash, or mismatched object definitions | Treat as a partial/manual/platform deployment. Determine the actual history and prepare a specific repair before proceeding. |
| Merged name and checksum already recorded | The runner has no pending 016. Inspect schema separately if drift is suspected. |

The runner rejects either recognized legacy name/checksum with a link to this
guide. A legacy checksum mismatch remains an error. It never treats one old
record as evidence that both portions ran, deletes a record, or updates a
record automatically. `--emit-sql` only prints SQL; it does not reconcile or
validate database history.

## Record reconciliation after schema verification

Retain the original record and SQL in the deployment audit before making any
metadata change. If historical database records must remain immutable, leave
the old 016 row intact and keep this deployment blocked from the merged-history
runner until an approved history strategy exists. File consolidation alone does
not authorize overwriting a deployed record.

Where the deployment owner explicitly permits metadata normalization, do it
only after both schema portions have been verified and all required writes
have succeeded. Prepare a reviewed transaction that checks the exact original
`version`, `name`, `dialect` and `checksum`, then changes only name/checksum to
the merged values, preserves `applied_at`, and requires exactly one affected
row. Retain the old metadata in the deployment audit. Never use an unguarded
update, delete/reinsert, or mark 016 complete before the second portion exists.

Re-run `bcs-admin db migrate --dialect mysql --only 16 --check-db` after an
approved reconciliation. Retain the inspection, DDL results and final check as
release evidence. This repository change does not perform that operation on
any existing database.

## Verification scope

`cargo test -p bcs-admin merged_016_migration_applies_to_real_mysql -- --ignored`
uses a disposable database via `BCS_TEST_MYSQL_URL`. It applies the merged file
with the production executor to the original Session CREATE statement, checks
legacy row preservation and both schema portions, verifies repeat planning has
no pending DDL, and confirms both old version records are rejected unchanged.
Unit tests lock the two archive checksums and verify checksum mismatch handling.

This addresses the duplicate 016 number. The HumanInput index in 001/008 is
now corrected, with manual index-size handling and historical record preservation
documented in the [index guide](human-input-index-size.md). Full historical
chain deployment validation remains separate.
