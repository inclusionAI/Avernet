# Bot authority generated-column compatibility correction

## Scope

The requester reported that OceanBase schema errors occur when the generated
expression for `edge_grants.authority_owner_log` uses
`CONVERT(to_id USING utf8mb4) COLLATE utf8mb4_bin`, and explicitly requested the
column-level charset/collation definition already used to fix that deployment.
This is a scoped historical correction to MySQL/OceanBase migration
`036_bot_authority.sql`, not permission to modify other committed migrations.

Both authority generated columns now declare
`VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin`:

- `authority_owner_log`: returns `to_id` only for an approved owner edge;
  otherwise NULL. It has the comment `authority 唯一约束的生成列`.
- `pending_log`: returns `bot_id` only for a pending ownership transfer;
  otherwise NULL. It used the same expression-level conversion pattern and
  receives the same correction.

The CASE expressions contain no charset conversion or COLLATE operator. The
existing unique indexes, predicates, column widths and virtual storage stay
unchanged. SQLite migrations are untouched.

## Historical checksum compatibility

The original bytes remain in `legacy/mysql/036_bot_authority.sql`, outside
migration discovery. Only this exact pair is accepted for version 036, name
`bot_authority`, dialect `mysql`:

| Archived SHA-256 | Corrected SHA-256 |
| --- | --- |
| `d8f1487e6d0e2d9e28f12e354637dff6a36d7b05e23f8a6e2641162d8c4227ec` | `70eb337d39d104395792534dfc91f6bfca9a1b23cf98ce7e90e41788a3d83571` |

An accepted historical record is retained with its existing checksum and
`applied_at`; migration 036 is not replayed. Unknown checksums and future edits
to the corrected file remain errors. Do not replace database checksums with the
new file hash to silence validation failures.

## Deployment and repair

- Fresh database, or a database where 036 has not started: apply the corrected
  migration in the normal full chain.
- Completed 036 with its original recorded checksum: the runner preserves and
  skips it. This source correction does **not** inspect or repair its schema.
  For OceanBase, inspect `SHOW CREATE TABLE edge_grants` and
  `SHOW CREATE TABLE bot_ownership_transfers`; reconcile both generated columns
  and their unique indexes through a reviewed DBA change plan. A deployment
  that already fixed `authority_owner_log` must still check `pending_log`.
- Failed/partially applied 036 with no record: stop and inspect every statement
  and the real schema before repair. MySQL-compatible DDL can commit before
  recording the migration. Do not blindly replay 036, delete successful history,
  or record it as complete merely because one generated column was repaired.
- A manually corrected file with a different recorded checksum requires review
  of the actual deployment artifact and schema; this change does not accept
  arbitrary equivalent SQL hashes.

Keep the original migration artifact and schema evidence with the deployment
record. Do not roll back to the expression-level conversion as an automatic
reversal; any retained-database schema change needs a reviewed plan.

## Validation

`cargo test -p bcs-admin` covers the corrected column definitions and unchanged
unique keys, the archived bytes, accepted old history without replay, and
rejection of altered names, dialects, versions and checksums. These static and
planner checks do not establish live OceanBase compatibility.

The existing real-MySQL full-chain gate is:

```bash
cargo test -p bcs-admin full_mysql_migration_chain_applies_and_preserves_history -- --ignored
```

It requires `BCS_TEST_MYSQL_URL` pointing to an empty disposable database.
Run the authority store MySQL conformance suites against a separate disposable
database as well to exercise owner/pending uniqueness. Live OceanBase execution
and schema inspection remain deployment validation requirements; MySQL execution
alone is not proof of OceanBase behavior.
