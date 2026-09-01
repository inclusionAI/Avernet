# BCS Database Migrations

This directory contains BCS database schema migrations.

The open-source v1 baseline starts from a single MySQL/OceanBase init schema:

| Version | File | Purpose |
| --- | --- | --- |
| 001 | `mysql/001_init_schema.sql` | Create the full BCS schema for a fresh MySQL/OceanBase database |
| 002 | `mysql/002_add_owner_bot_id.sql` | Add message ownership metadata and its lookup index |
| 003 | `mysql/003_add_organizations.sql` | Add organizations and organization membership tables |
| 004 | `mysql/004_add_session_collection.sql` | Add session collection state |
| 005 | `mysql/005_add_session_collection_timestamp.sql` | Add session collection timestamp |
| 006 | `mysql/006_session_files.sql` | Add session file metadata |
| 007 | `mysql/007_add_human_input_runtime.sql` | Add generic node outcome and HumanInput responder metadata |
| 008 | `mysql/008_human_input_im_requests.sql` | Add persisted HumanInput IM request and queue state |
| 009 | `mysql/009_eventing.sql` | Add public Event, Subscription, fanout, Delivery, Attempt, and audit storage |
| 010 | `mysql/010_group_opening_message.sql` | Add Group opening-message configuration |
| 011 | `mysql/011_group_participant_tags.sql` | Add per-participant provider routing tags |
| 012 | `mysql/012_expand_session_ids.sql` | Expand canonical session identifiers to 128 characters |
| 013 | `mysql/013_add_bot_task_modes.sql` | Add task-claim and task-dream mode toggles on Bots |
| 014 | `mysql/014_edge_permission.sql` | Add A2A edge-permission tables (friend unification) |
| 015 | `mysql/015_add_bot_internal_attributes.sql` | Add persistent Provider Bot attributes (visibility, friend extension, check-in strategy) |
| 016 | `mysql/016_session_callback_lease.sql` | Add activation-aware callback delivery lease columns and recovery index |
| 017 | `mysql/017_state_machine_rerun_lineage.sql` | Add State Machine Run lineage, activation identity, and natural rerun idempotency |
| 018 | `mysql/018_one_shot_opening_message_override.sql` | Persist request-level opening-message overrides for one-shot State Machine Runs |

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

The current SQLite migration chain records versions `001` through `019`.
Versions whose schema is already created by the startup bootstrap record
progress as no-ops; version `007` repairs the HumanInput output metadata on
existing databases, versions `008` and `009` add their tables through the
additive bootstrap DDL, version `010` migrates Eventing endpoint storage to
plaintext, version `011` adds Group opening-message configuration, versions
`012` and `014` record progress for the task-mode and internal-attribute Bot
columns added through the additive bootstrap DDL, version `013` adds the
edge-permission tables, version `015` adds per-participant provider routing
tags, version `016` records parity for SQLite's already unbounded `TEXT`
session identifiers, version `017` adds activation-aware callback lease columns
plus the periodic recovery index, version `018` adds State Machine Run lineage,
activation identity, and the unique direct-rerun constraint, and version `019`
adds the request-level one-shot opening-message override column.
Future schema changes should use later numeric versions.
Do not add pre-open-source local schema repairs to the baseline migration.
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
