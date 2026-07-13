# BCS Database Migrations

This directory contains BCS database schema migrations.

The open-source v1 baseline starts from a single MySQL/OceanBase init schema:

| Version | File | Purpose |
| --- | --- | --- |
| 001 | `mysql/001_init_schema.sql` | Create the full BCS schema for a fresh MySQL/OceanBase database |
| 002 | `mysql/002_add_owner_bot_id.sql` | Add message ownership metadata and its lookup index |
| 003 | `mysql/003_add_organizations.sql` | Add organizations and organization membership tables |

The previous internal incremental SQL files were removed from the public
migration path and replaced by the v1 baseline. New public migrations should be
added after the baseline as `002_xxx.sql`, `003_xxx.sql`, and so on.

## Schema Version Table

Both MySQL/OceanBase and SQLite use the same logical migration version model.
The shared record table is:

```sql
CREATE TABLE bcs_schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  dialect TEXT NOT NULL,
  checksum TEXT NOT NULL,
  applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

Concrete column types may differ by dialect (`INT`/`VARCHAR`/`TIMESTAMP` for
MySQL, `INTEGER`/`TEXT` for SQLite), but the semantics must stay aligned.

## MySQL/OceanBase

`mysql/001_init_schema.sql` is generated from the sanitized online BCS schema.
It intentionally removes:

- runtime data and current `AUTO_INCREMENT = ...` values
- environment-specific database names, hosts, and datasource names
- OceanBase physical placement options such as `AUTO_INCREMENT_MODE`,
  `ROW_FORMAT`, `COMPRESSION`, `REPLICA_NUM`, `BLOCK_SIZE`,
  `USE_BLOOM_FILTER`, `TABLET_SIZE`, and `PCTFREE`
- non-business repro or legacy tables that are not referenced by BCS stores
- non-English SQL comments and schema comments

BCS does not auto-apply MySQL/OceanBase migrations at service startup. For
deployment-controlled changes, use `bcs-admin db migrate --dialect mysql
--emit-sql` and apply the emitted SQL through the DBA/deployment process.

`bcs-admin db migrate --dialect mysql --check-files` performs static validation
of the local migration files. `--check-db` connects to the configured
MySQL/OceanBase datasource, reads `bcs_schema_migrations`, and compares the
applied version/name/dialect/checksum records with the selected local migration
files without applying DDL. `--apply` executes pending migrations against the
configured datasource after an interactive `y/N` confirmation. Pass `-y` or
`--yes` to skip the prompt for scripted environments.

The baseline SQL creates `bcs_schema_migrations` and records version `1` after
all schema objects are created.

## SQLite

SQLite local mode uses `crates/bootstrap/bcs/src/migrations.rs` for fresh
database bootstrap. The bootstrap DDL mirrors the public baseline schema in a
SQLite-compatible form.

The startup runner executes SQLite schema work in this order:

1. Ensure `bcs_schema_migrations` exists.
2. Create missing tables for fresh local databases.
3. Run SQLite-specific versioned migrations in numeric order.
4. Create missing indexes after versioned migrations have run.

Each migration is recorded only after all of its steps succeed. Re-running
startup must be idempotent, and checksum mismatches fail startup.

The current SQLite migrations are `001_init_schema`,
`002_channel_binding_audit_timestamps`, and `003_add_organizations`. The
version-3 body is a no-op because startup creates missing organization tables
before recording the version. Future schema changes should add later migration
versions. Do not add pre-open-source local schema repairs to the baseline
migration.
Pre-baseline local SQLite files are not a compatibility target; recreate them
from the current bootstrap schema if needed.

BCS startup auto-runs the SQLite migration runner when
`[database].type = "sqlite"`. The same runner is also available manually:

```bash
# Infer the SQLite path from [database.sqlite].path
cargo run --package bcs-admin -- --config-dir configs db migrate --check-db
cargo run --package bcs-admin -- --config-dir configs db migrate --apply

# Or target a specific SQLite file
cargo run --package bcs-admin -- db migrate --dialect sqlite --sqlite-path ./bcs.db --check-db
```

For SQLite, `--emit-sql` is diagnostic output only. The real runner applies the
code-defined SQLite migration steps.

## Dialect Parity

MySQL/OceanBase and SQLite migrations should share the same logical version
numbers. SQL text may differ by dialect, but each version must represent the
same schema change.

Example:

```text
mysql/002_add_example_column.sql
sqlite/002_add_example_column.sql
```

If a version is a no-op for one dialect, document that explicitly in the
corresponding file.

## Seed Data

Migrations are for DDL and necessary data backfills only. They should not create
default bots, service groups, templates, demo accounts, or test fixtures.

Seed data belongs in a separate seed path or command, for example:

- `src/bcs/seeds/`
- `scripts/dev-seed-*`
- `bcs-admin seed`

## Rollback

Migrations are forward-only. If a production migration must be reverted, author
a reviewed paired revert migration or DBA change plan. Automatic rollback is not
provided.
