# MySQL 8 migration syntax corrections

The active migrations 002, 007, 011, 013, 015, 017, 018 and 020 use standard
`ADD COLUMN` instead of `ADD COLUMN IF NOT EXISTS`. The latter is not accepted
by MySQL 8. The correction changes only those keywords: column types, defaults,
nullability, placement, indexes and data semantics remain unchanged.

This is an explicitly requested historical correction. The original bytes are
retained under `legacy/mysql/`, outside migration discovery. New development
must follow the committed-migration rule in `src/bcs/AGENTS.md`.

## Historical checksum compatibility

The runner accepts only the following exact archived/current pairs, with the
same version, name and `mysql` dialect. It skips already recorded versions and
never updates their checksum or `applied_at`. Unknown checksums or later edits
to a corrected file still fail validation. This does not relax the separate
[016 reconciliation requirement](016-session-callback-and-chat-runs.md).

| Version / name | Archived SHA-256 | Current SHA-256 |
| --- | --- | --- |
| 002 / `add_owner_bot_id` | `b0ee5d777bf79f7c04e676ea61cac4553e0c0be1db3fb3bc2849978104f2ffec` | `4190aed200a1cf880321bec17ca50ca2835822a90e03df234d1f98ae2d80d588` |
| 007 / `add_human_input_runtime` | `4cea1e1ff6db55afa1ac5c7b10823c57f4876c20b033703f98940eb25e4ee19c` | `2a1685ae578fdfe06401321107dab9007266fb367495cae03fbbc662cdfac69c` |
| 011 / `group_participant_tags` | `3f8148d618395fd2b45312c08287422d4e3b8fdc17587e29f5c80a55a3962053` | `41e0f84544b1fdc53f840af30e43dc108dc4408c91ee0ceca1e9011e591ae0b1` |
| 013 / `add_bot_task_modes` | `fddb6f03977b313c6f5d1bed1ad4a735fb1c439f1b35c0a4436372a90dc1ad3e` | `956f7cb936e293feb98281d86d78c978432c6c2ed0a75a4c766dc381dde353b3` |
| 015 / `add_bot_internal_attributes` | `247e163a9fd7c4b4c0e023757f72a978857722f9ea6eebee5dc607dbfae44060` | `b65ac46e25683050fd34c791f2223f7bbd3de59e359a70907579eabec1e48ec8` |
| 017 / `state_machine_rerun_lineage` | `942f9d52fd2438bfe65badc561c7d0d840d2ce46d164268cb5d60199c258dfd1` | `8128dedd4f597c00e8b290cc2046e8417ce40e41a7e75b993080370b3d1b393c` |
| 018 / `one_shot_opening_message_override` | `57b4534262cd9a30f42e5eeda7cc5953a3c56fc731d3d75c0a540c4d4b98862a` | `1e0a66ad71d6dd5cf98f0b180401dfdeb6e6e2e5984fc690bd67904922ab533d` |
| 020 / `human_participant_message_visibility` | `9423d10481b072df738befc9e8987a70808beba3f199921ee60c8b7463448e8c` | `24df88c5a34c7f1c547820b8b4483f1dabedd1373a158dc1bbf2e262ee0c60d1` |

## Applying migrations

- Fresh database: run the complete chain through `bcs-admin db migrate --dialect
  mysql --apply --yes`. Do not run only 001 or use a baseline as the latest schema.
- Completed historical migration: keep its old record. A matching archived
  checksum is recognized; only pending versions are applied.
- Missing record with existing columns, or a partially completed migration:
  inspect the real schema and the original deployment artifacts before repair.
  This includes databases initialized with an older 001 containing tags or
  visibility columns but lacking 011/020 records. Do not replay ADD COLUMN,
  delete successful records, or invent a success record without checking all
  statements in the migration. The runner does not suppress duplicate-column
  errors or treat them as proof that the whole migration succeeded.
- `--emit-sql` prints the selected files, including already applied versions;
  it does not consult database history. Use `--check-db` and the versioned
  `--apply` path for normal upgrades.

MySQL DDL may commit before the migration record is written. A failure leaves
that version unrecorded and returns an error; a subsequent attempt can require
explicit repair of the partial schema. Removing IF NOT EXISTS does not promise
transactional DDL or automatic recovery of unrecorded writes.

## Verification

```bash
cargo test -p bcs-admin
cargo test -p bcs --lib mysql_
cargo test -p bcs-admin full_mysql_migration_chain_applies_and_preserves_history -- --ignored
```

The full-chain test requires an empty disposable `BCS_TEST_MYSQL_URL`. It uses
the production configured apply path for 001–028 (28 migrations), a
repeated no-op apply, and an upgrade from version 020 with archived checksum
records to 028. It verifies
the added columns, retained historical values and NULL snapshot plan, unchanged
old records/timestamps, and unknown-checksum rejection. It refuses non-empty
databases and cleans up its own database tables. CI runs it before other MySQL
contracts so they can reuse the emptied database.

The historical fixture uses the corrected, equivalent DDL with old checksum
records; it does not execute the unsupported SQL on MySQL or claim validation
of an actual OceanBase deployment. A real deployment must still reconcile its
own schema and any split-016 history.
