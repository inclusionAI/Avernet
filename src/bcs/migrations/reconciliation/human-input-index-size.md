# HumanInput scope index: size limits and historical records

The corrected 001 and 008 use a 700-character prefix for `reply_scope_key` in
`idx_human_input_scope_status`. Both columns remain `VARCHAR(768)` and
`uk_human_input_active_slot` still indexes the complete `active_slot_key`.
Queries keep comparing the full scope string, so matching prefixes do not mix
requests. Long shared prefixes may reduce selectivity but do not change results.

## Fresh databases

001 is the starting schema. Participant tags and the five Human message
visibility columns are added only by 011 and 020; this baseline cleanup does
not drop columns from any existing database.

Use the active `mysql/001_init_schema.sql` and `mysql/008_human_input_im_requests.sql`.
They have new checksums and can create the HumanInput table on MySQL 8.4 with
utf8mb4. The active MySQL chain ends at 028; the automatic index-rebuilding
028 has been removed. Existing tables are not altered just because their 001/008
record uses a historical checksum. See the [manual handling procedure](../README.md#humaninput-index-compatibility)
for a deployment that encounters an index-size limit.

The 001 record checksum is SHA-256 of all file bytes before the comment
`-- Record the open-source v1 baseline`; this avoids a self-referential checksum.
008 uses SHA-256 of the complete SQL file, as other increments do.
A regression test fixes the exact delta from the archived files: the index
prefix and 001 record checksum change, and 001 excludes the six columns owned
by migrations 011/020 as documented in the migration README.

## Existing databases: preserve historical records

Original SQL is archived under `legacy/mysql/`. The runner recognizes exactly
these historical-to-current checksum pairs with the same version, name and
`mysql` dialect. Unknown checksums and subsequent unregistered revisions fail.

| Version / name | Historical record checksum | Current record checksum |
| --- | --- | --- |
| 001 / `init_schema` | `b3de64c97b982a735230f6c55e966e4404eb509d70a0b7fd8f11dfa43e3452a7` | `a7f351ed88f95eb233e535f5fda9226a161fea5fc2af84d97ff2e2593a57a1d3` |
| 008 / `human_input_im_requests` | `0e10c711afc436cf59d2393e3d5c88b9c24e73be72f4e984044840e6f798ebd5` | `efc4dc1b457f7ce1f20c78394d1af4b1ca2af7bee963c8cff4f4587660728c96` |

The old 001 file's full SHA-256 is
`f2248c186b6d4260978d817a9654adfbee2f08dd12fa8cc3c1aa5a70d1bb9d3d`;
its historical record uses the declared checksum in the table above.
Platform-managed variants must be compared with their own release artifacts;
the runner does not guess or rewrite their checksums.

Recognizing an old record means its original CREATE ran, not that the prefix
index is installed. Never replay 001/008 or update/delete their records to
change an index. A working historical index needs no automatic replacement.

Inspect the table with `SHOW CREATE TABLE` and `SHOW INDEX`, then follow the
[README procedure](../README.md#humaninput-index-compatibility) only if an index
limit blocks the intended operation. The manual change must preserve full
`active_slot_key` uniqueness and compare full scope values in queries. Record
its execution evidence through the deployment process.

If the now-removed 028 was already applied, preserve its record, checksum,
`applied_at` and physical index. `--to 27 --check-db` inspects the earlier
chain while reporting the extra version; an unrestricted check rejects the
conflicting identity now that 028 belongs to Fixed Loop. Reconcile such a retained development database explicitly rather
than automatically replacing its record.

This handling is independent of [split 016 reconciliation](016-session-callback-and-chat-runs.md).
A history check does not certify physical schema or a production upgrade.

## Verification

`cargo test -p bcs-admin human_input_index_migrations_apply_to_real_mysql -- --ignored`
uses a disposable database to check corrected 001, standalone 008, long Unicode
prefix collisions, full slot uniqueness and historical record preservation.
`full_mysql_migration_chain_applies_and_preserves_history` executes the latest
001–028 chain. Neither test authorizes DDL on a production datasource.
