# HumanInput scope index: 001/008 revision and 028 upgrade

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
utf8mb4. Migration [028](../mysql/028_human_input_scope_index.sql) can also run
after the corrected CREATE; it rebuilds the same index without changing rows.

The 001 record checksum is SHA-256 of all file bytes before the comment
`-- Record the open-source v1 baseline`; this avoids a self-referential checksum.
008 and 028 use SHA-256 of the complete SQL file, as other increments do.
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

Recognizing an old record means its original CREATE ran, not that the new
index is installed. The actual repair is recorded independently as 028.
Never replay 001/008 or update/delete their old records to perform this repair.

1. Inspect the intended datasource with `SHOW CREATE TABLE bcs_human_input_requests`
   and retain the original 001/008 migration records with the deployment evidence.
2. Check the selected history with `bcs-admin --config <config-file> db migrate
   --dialect mysql --only 1 --only 8 --only 28 --check-db`. An existing deployment
   with both old records should report only 028 pending for this selection.
   An absent 001/008 row is an incomplete history; inspect before applying a
   wider selection that could replay its CREATE statements.
3. Apply only 028 through the deployment-controlled runner or DBA process:
   `bcs-admin --config <config-file> db migrate --dialect mysql --only 28 --apply`.
   The single ALTER drops/recreates the non-unique scope index. A missing index
   or incompatible table fails instead of recording a successful repair.
4. Inspect index metadata and rerun the selected history check. The first index
   part must have `sub_part = 700`; the other three parts must remain full.
   The unique slot index must retain `non_unique = 0` and `sub_part IS NULL`.
   Records 001/008, including their `applied_at`, must be unchanged; 028 is new.

MySQL 8 InnoDB commits this ALTER independently of its migration record. If the
DDL succeeds but recording 028 fails, reapplying 028 safely rebuilds the same
index and then writes its record. It never swallows a failed DDL or record write.
Rollback binaries can retain this schema: column values, full-value comparison
and slot uniqueness are unchanged. Do not restore the oversized utf8mb4 index.

This selection handles only the HumanInput index. It neither reconciles the
[split 016 histories](016-session-callback-and-chat-runs.md) nor certifies the
remaining historical migration chain. `--check-db` compares history records,
not live schema; selecting only 001/008 does not verify that 028 ran.

## Verification

`cargo test -p bcs-admin human_input_index_migrations_apply_to_real_mysql -- --ignored`
uses `BCS_TEST_MYSQL_URL` for a disposable database. It executes the complete
corrected 001, exercises 008 independently, and verifies fresh/replayed 028,
long Unicode prefix collisions, full slot uniqueness, failed DDL without a
success record, unchanged column metadata and preserved old records/timestamps.

MySQL cannot create the original utf8mb4 oversized index. The existing-index
fixture uses utf8mb3 to represent its full 768-character index shape, then
verifies that 028 changes only the index. This is not an OceanBase deployment
test. The fixture requires baseline tables and versions 001/008/028 to be absent,
cleans its own objects, and preserves other tests' migration records.
