# BCS Database Migrations

This directory contains BCS database schema migrations.

Bot-owned Provider storage adds MySQL/OceanBase `029_bot_provider_storage.sql`
and SQLite `030_bot_provider_storage.sql`. These extend `bcs_bots` with Provider
identity, connection mode, webhook metadata and environment-scoped uniqueness;
they do not create a separate registration table. Gateway writes retain the
existing binding projection; upstream writes do not create bindings.
Apply remote DDL before new server code; SQLite bootstrap migrates automatically.
Follow the [rollout prerequisites](../docs/provider-bot-storage-migration.md).
Historical data correction must complete through a separate reviewed work order
before switching the delivery read source; no dedicated backfill tool is shipped.

The user confirmed PR #2358 has never been deployed and approved consolidating
its draft DB changes. Only this PR's unreleased slots are changed; upstream's
Fixed Loop migrations and all earlier bodies/identifiers remain unchanged.

MySQL/OceanBase queue tables (021/022) follow the existing `bcs_chat_runs`
convention: auto-increment `id` primary key and database-managed `gmt_create` /
`gmt_modified`. Business identity remains unique on `(env, delivery_id)` and
`env`, respectively. Queue `created_at_ms` / `updated_at_ms` remain application
timestamps; repositories neither write nor use the surrogate/audit columns.
SQLite schema and public delivery IDs are unchanged.

The 021/022 definitions were corrected before the planned first production
deployment. If the earlier PR DDL has already been applied, do not replay these
CREATE statements or replace recorded checksums: reconcile with an explicit
reviewed ALTER migration first. Fresh deployments use the corrected files.

Run reply normalization adds SQLite `027_run_reply_segments.sql` and MySQL/OceanBase
`026_run_reply_segments.sql`: an additive env/session/sender/run/sequence index.
The `run_reply` type uses existing message columns, with no historical backfill.
Apply remote DDL before enabling the updated queue path; SQLite bootstrap applies
its migration automatically. Rollback binaries must retain history filtering for
`run_reply` so internal summaries/diagnostic metadata are not exposed as chat.

The open-source v1 baseline starts from a single MySQL/OceanBase init schema.
001 is the starting schema, not a snapshot of the latest schema. Fresh databases
must apply the subsequent numbered migrations in order as well.

| Version | File | Purpose |
| --- | --- | --- |
| 001 | `mysql/001_init_schema.sql` | Create the starting BCS schema for a fresh MySQL/OceanBase database |
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
| 016 | `mysql/016_session_callback_lease_and_chat_runs.sql` | Add Session callback leases/recovery index and Direct Chat runs |
| 017 | `mysql/017_state_machine_rerun_lineage.sql` | Add State Machine Run lineage, activation identity, and natural rerun idempotency |
| 018 | `mysql/018_one_shot_opening_message_override.sql` | Persist request-level opening-message overrides for one-shot State Machine Runs |
| 019 | `mysql/019_invite_code.sql` | Invite-code schema |
| 020 | `mysql/020_human_participant_message_visibility.sql` | Human participant message visibility |
| 021 | `mysql/021_message_deliveries.sql` | Canonical-message target deliveries (SQLite version 022) |
| 022 | `mysql/022_message_delivery_policy.sql` | Environment-scoped live queue policy (SQLite version 023) |
| 023 | `mysql/023_delivery_worker_queries.sql` | Bounded worker query indexes (SQLite version 024) |
| 024 | `mysql/024_delivery_context_selection.sql` | Bounded inject selection (SQLite version 025) |
| 025 | `mysql/025_delivery_pending_abort.sql` | Pending abort lookup (SQLite version 026) |
| 026 | `mysql/026_run_reply_segments.sql` | Run reply reconstruction index (SQLite version 027) |
| 027 | `mysql/027_provider_bot_webhook.sql` | Per-Bot Provider webhook endpoint (SQLite version 028) |
| 028 | `mysql/028_fixed_loop_runtime.sql` | Fixed Loop snapshot plan, failure/Judge state, opening/dispatch checkpoints and recovery indexes (SQLite version 029) |
| 029 | `mysql/029_bot_provider_storage.sql` | Bot-owned Provider identity, delivery mode and webhook metadata (SQLite version 030) |

The consolidated Fixed Loop schema is MySQL 028 and SQLite
`029_fixed_loop_runtime.sql`. Each includes three nullable snapshot plan columns,
the `failure_action` and Judge phase/lease fields, the Run/Session recovery
indexes, and the opening-history/Bot-dispatch checkpoint table and lookup index.
Apply MySQL 028 before starting the new runtime, including v1 traffic with the
scanner disabled. SQLite 029 checks each column and resumes after any partial
step; it records the version only after all columns, indexes and tables succeed.
Existing rows retain their values, nullable new fields remain NULL, and the
Judge lease token starts at zero. This schema
migration does not enable v2 execution; runtime/recovery release gates still apply.

SQLite 029 already contains every Loop and checkpoint column. The separate
legacy-draft completion migration has been removed as part of this PR's
consolidation. The supported paths are a fresh database and an upgrade from 028;
automatic upgrades from earlier Loop development drafts are outside this release.
Use a fresh disposable test database for such drafts; retained data requires a
separately planned migration. This change does not rewrite database records.
The same version number can still represent different changes per dialect.
HumanInput index-size handling is manual; the MySQL chain ends at 029 and never
drops/rebuilds an existing HumanInput index automatically.

The Draft queue branch originally used MySQL 019–024 and SQLite 020–025.
After rebasing onto the invite-code/visibility migrations, its versions move by
two; upstream versions are never reassigned. A test database already migrated
with the old Draft must not be upgraded by deleting migration records or
blindly rerunning DDL. Back up and reconcile its schema/version records separately,
or use a fresh disposable test database. The normal checksum/name validation
remains fail-closed; this rebase does not modify any running local database.

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

### Interrupted MySQL DDL

MySQL 8 InnoDB atomic DDL applies to one supported statement, not a whole
migration file. DDL implicitly commits, so wrapping multiple ALTER/CREATE
statements in a transaction cannot make the migration all-or-nothing. See the
[MySQL atomic DDL documentation](https://dev.mysql.com/doc/refman/8.0/en/atomic-ddl.html).
For example, 028 has five DDL statements: earlier statements can remain applied
when a later statement fails. The runner reports the failing statement number
and records the migration only after every statement succeeds. It does not
automatically resume a partially applied MySQL migration. `--check-db` compares
migration records, not the physical schema.

Before applying DDL to retained data, take a recoverable backup and rehearse on
a representative database. Index creation and ALTER may require metadata locks
and substantial time or disk space; online DDL does not eliminate those costs.
Complete the migration before deploying code that requires the new schema.

If an apply is interrupted, stop the rollout and inspect `SHOW CREATE TABLE`,
`SHOW INDEX` and `bcs_schema_migrations` against the unchanged migration file.
Include the failing statement in this inspection: a lost connection does not
prove that the server rolled it back. Do not blindly replay the file or ignore
duplicate-column/index errors. Use the reviewed DBA/deployment process to apply
only the missing changes, checking column definitions and index order as well
as object names. Only after the entire target schema is verified may that
process insert a missing success record using the original version, name,
dialect and checksum reported by `--check-db`, then rerun `--check-db`.
Do not rewrite existing records or drop applied objects merely to make replay
pass. If completion is unsafe, use the planned backup recovery procedure.

## Baseline column ownership

The explicitly requested 001 cleanup removes these duplicate definitions from
the active baseline. Their existing incremental migrations own the additions:

| Table | Columns removed from 001 | Owning migration |
| --- | --- | --- |
| `bcs_group_participants` | `tags_json` | 011 |
| `bcs_group_participants` | `message_view_scope` | 020 |
| `bcs_group_sessions` | `message_visibility_version` | 020 |
| `bcs_messages` | `visibility_domain`, `audience_kind`, `audience_actor_ids_json` | 020 |

Git history shows the five visibility columns were added to 001 by
`7ed95c4d4f`, together with migration 020. `tags_json` was already in the initial
001 (`eafb67756d`) but was also added by 011. No other later ADD COLUMN duplicates
were found in 001. The original baseline remains byte-for-byte in
`legacy/mysql/001_init_schema.sql`; the exact historical checksum compatibility
is documented in the [deployment guide](reconciliation/human-input-index-size.md).
This file cleanup does not execute DROP COLUMN or rewrite deployed records.
The subsequent MySQL syntax correction also archives the original 011/020
files and removes their unsupported ADD COLUMN guard, as documented in the
[syntax compatibility guide](reconciliation/mysql-syntax-compatibility.md).

Follow [the committed-migration rule](../AGENTS.md#freeze-committed-migrations):
new columns belong in a new migration with a unique later version. Do not add
them to a committed migration or maintain 001 as a current-schema snapshot.

## Fixed Loop MySQL verification

The focused Fixed Loop contracts run against a disposable MySQL 8.4 database
through the production DB plugin. They certify the consolidated 028 migration and
its repository consumers. They do not certify a fresh installation of the
entire historical migration chain.

```bash
# Set BCS_TEST_MYSQL_URL to a disposable database with no collaboration tables.
cargo test --package bcs-admin fixed_loop_migration_applies_to_real_mysql -- --ignored
cargo test --package bcs-collaboration-store --test mysql_store real_mysql_fixed_loop_snapshot_and_rerun_contract -- --ignored
```

The first test uses the four original table CREATE statements from 001 and the
actual 028 migration executor. It covers empty tables, legacy v1 rows,
column types, Run/Session recovery indexes, the checkpoint table and its index, early/late failed DDL without a
success record, repeated apply planning, and checksum mismatch rejection. The
Store test uses the documented
pre-Loop physical-schema fixture, then applies 028 unchanged. Text and Prepared
protocols cover immutable snapshot round-trip, environment isolation, changed
current Definitions, concurrent Chat/Service reruns, complete plan copying,
activation exactly once, and rollback when snapshot insertion fails. They also
cover saved Judge input/lease fencing, immutable opening/dispatch checkpoints,
dispatch send markers, concurrent ACK/expiry fencing, and concurrent fixed-ID
message writes without extra sequence allocation. Tests
refuse existing owned tables and clean up only tables they create.
Both commands are wired into the MySQL service steps in `unit-tests.yml`.

The duplicate 016 files have been consolidated into one active migration as an
explicitly authorized exception for PR #2339. That release validated the
001–028 chain; it did not add an automatic upgrade for either earlier split-016
lineage. Once merged, subsequent schema changes follow the migration freeze rules
in `src/bcs/AGENTS.md`.
Both original files are preserved byte-for-byte under `legacy/mysql/`, outside
migration discovery. Existing split-016 records are rejected with a specific
reconciliation diagnostic; the runner does not silently accept or rewrite
one old record as the complete merged migration. Follow the
[016 reconciliation guide](reconciliation/016-session-callback-and-chat-runs.md)
for an existing deployment's explicit reconciliation.

The 001/008 HumanInput index definitions use a 700-character prefix and the
complete static `--check-files` gate passes. Their original files are archived
under `legacy/mysql/`; the runner recognizes the documented historical checksum
pairs without rewriting old records. Existing tables and indexes are unchanged.
See [HumanInput index compatibility](#humaninput-index-compatibility).
The unsupported `ADD COLUMN IF NOT EXISTS` syntax in 002/007/011/013/015/017/018/020
has been corrected to `ADD COLUMN`. Original files are archived and only the
documented exact checksum pairs are accepted without rewriting old records.
See the [syntax compatibility guide](reconciliation/mysql-syntax-compatibility.md).
The disposable MySQL 8.4 full-chain test covers 001–029, repeated apply and
an upgrade from 020 with archived checksum records. It runs before the other
MySQL CI contracts and leaves the test database empty:

```bash
cargo test -p bcs-admin full_mysql_migration_chain_applies_and_preserves_history -- --ignored
```

This replaces the earlier 002 syntax blocker. Actual deployment-specific
schema/history reconciliation, OceanBase and full product release validation
remain tracked by FL-28/FL-29; static validation alone is not execution evidence.

For an existing deployment whose pre-Loop table structure has been verified,
028 can be checked/applied as a selected additive migration using the existing
`--only 28` workflow. The selection does not certify the complete history or
permit enabling v2. Either historical split-016 lineage still requires the
documented reconciliation; production rollout must satisfy the full release
gates.

## HumanInput index compatibility

The non-unique index in active 001/008 uses:

```sql
KEY `idx_human_input_scope_status`
  (`reply_scope_key`(700), `status`, `deadline_ms`, `created_at`)
```

Both `VARCHAR(768)` columns and full `UNIQUE(active_slot_key)` are unchanged.
No numbered migration drops or rebuilds an existing HumanInput index. Fresh
MySQL installations use the latest 001–029 chain; 001 and standalone 008 already
create the prefix index. Historical 001/008 checksums remain recognized without
rewriting their records, but accepting history does not inspect the live index.

If deployment reports `ERROR 1071: Specified key was too long`:

1. Check the failing statement and use `SHOW CREATE TABLE bcs_human_input_requests`
   (if the table exists) and `SHOW INDEX FROM bcs_human_input_requests` to inspect
   the actual engine, charset, column types and index parts. This scope-index fix
   must not be applied blindly to a different oversized index.
2. For a fresh or failed CREATE, use the current 001/008 definition above. The
   prefix applies only to `reply_scope_key` in this **non-unique** lookup index;
   keep the full `active_slot_key` unique index and both column lengths unchanged.
   Resume through the migration runner after checking the partial schema/history.
3. For an existing populated table, leave a working index alone. If that index
   blocks a planned charset/schema change, back up and arrange a DBA-reviewed
   maintenance window. Only then replace it with the prefix form; the operation
   can build an index and wait for locks even when online DDL is supported:

   ```sql
   ALTER TABLE bcs_human_input_requests
     DROP INDEX idx_human_input_scope_status,
     ADD INDEX idx_human_input_scope_status
       (reply_scope_key(700), status, deadline_ms, created_at);
   ```

   If the index is absent, use only `ADD INDEX`. Choose the DDL algorithm/locking
   mode supported by the target MySQL/OceanBase version; do not run this example
   automatically during startup or every deployment.
4. Verify prefix length 700, unchanged full slot uniqueness and exact-scope query
   results, then retain the change evidence separately. Do not rewrite migration
   checksums or mark failed migrations successful.

The automatic index replacement drafted as MySQL 028 in this PR was removed.
The latest MySQL chain ends at 029. A retained database that recorded that draft
keeps its record and index; `--to 27 --check-db` can inspect the selected chain,
while an unrestricted history check rejects its conflicting migration identity. Do not delete or
rewrite the record to silence that diagnostic.
See the [history and index guide](reconciliation/human-input-index-size.md).

```bash
cargo test -p bcs-admin human_input_index_migrations_apply_to_real_mysql -- --ignored
```

The test uses a disposable `BCS_TEST_MYSQL_URL`, executes corrected 001 and
standalone 008, and checks prefix collisions, full slot uniqueness and retained
historical records. The full-chain test validates all 29 active migrations.


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

The current SQLite migration chain records consecutive versions `001` through
`030` (30 versions total).
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
adds the request-level one-shot opening-message override column. Version `020`
repairs invite-code identity, and `021` adds Human participant visibility.
Versions `022`–`027` add delivery queues, policy and query indexes; `028` adds
the per-Bot Provider webhook endpoint, and `029` adds
the fixed Loop snapshot plan, progression/session recovery indexes, saved
failure/Judge state and opening/dispatch checkpoints in one migration.
Version `030` adds Provider identity, delivery mode and webhook metadata to Bots,
with a unique environment-scoped Provider/ref index and no new table.

Versions `001`–`021` are defined in Rust. Independent SQL files were introduced
for `022`–`027` by the message congestion-control change; the runner loads them
explicitly with `include_str!`, rather than discovering a directory. `028`
adds the Provider webhook column. `029`
also has a SQL file; its Rust runner checks eight columns before adding them
and executes four idempotent index statements and the checkpoint table CREATE
(13 statements total). The checkpoint table CREATE includes all eight progress,
dispatch and recovery columns; no separate completion migration is needed.
A missing SQL file for an
earlier version therefore does not imply a missing
migration. `SQLITE_VERSIONED_MIGRATIONS` and its dispatch in
`crates/bootstrap/bcs/src/migrations.rs` define the complete chain; frozen DDL
and guarded schema operations are in its `migrations/` modules.
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

The original documentation called for matching numbers. The implementation
did not preserve that convention: SQLite-specific repairs, bootstrap-owned
changes and different grouping/order produced separate histories. For example,
SQLite `002` repairs channel-binding audit timestamps, while MySQL `002` adds
message ownership; SQLite `010` repairs Eventing endpoint storage, while MySQL
`010` adds Group opening messages. Number equality is not a schema-parity check.

Preserve the committed identifiers and records in each dialect. Do not renumber
historical migrations to align them. Track logical schema changes using the
following mapping and verify both implementations. New migrations use the next
available number in their own dialect and document the corresponding change.

| Logical change | MySQL | SQLite |
| --- | --- | --- |
| Initial schema | 001 | 001 + bootstrap |
| Message ownership | 002 | bootstrap column repair |
| Organizations, collection, collection timestamp, Session files | 003–006 | 003–006 + bootstrap |
| HumanInput output metadata / requests | 007 / 008 | 007 / 008 |
| Eventing tables | 009 | 009; SQLite-only endpoint repair 010 |
| Group opening message | 010 | 011 |
| Participant tags | 011 | 015 |
| Session identifier width | 012 | 016 (TEXT parity record) |
| Bot task modes / internal attributes | 013 / 015 | 012 / 014 + bootstrap |
| Edge permissions | 014 | 013 |
| Callback lease / Direct Chat runs | merged 016 | 017 / bootstrap |
| Rerun lineage / opening override | 017 / 018 | 018 / 019 |
| Invite codes | 019 | bootstrap + 020 identity repair |
| Human participant visibility | 020 | 021 |
| Delivery queues, policy and query indexes | 021–026 | 022–027 |
| Per-Bot Provider webhook endpoint | 027 | 028 |
| Fixed Loop plan, recovery indexes, failure/Judge state and opening/dispatch checkpoints | 028 | 029 |
| Bot-owned Provider identity, delivery mode and webhook metadata | 029 | 030 |
| HumanInput index prefix | 001/008 for fresh databases; manual repair if required | no equivalent MySQL index-size limit |

The committed-migration freeze also applies to historical migration bodies in
Rust; do not bypass it by adding a column to SQLite bootstrap or an old function.

## Seed Data

Migrations are for DDL and necessary data backfills only. They should not create
default bots, service groups, templates, demo accounts, or test fixtures.

Seed data belongs in a separate seed path or command, for example:

- `src/bcs/seeds/`
- `scripts/dev-seed-*`
- `bcs-admin seed`

## Rollback

Fixed Loop schema is delivered by MySQL `028_fixed_loop_runtime.sql` and SQLite
`029_fixed_loop_runtime.sql`. The nullable `failure_action`
column records `retry` or `fail_run` with the Failed-attempt CAS, and is cleared
by the retry CAS. Apply this DDL before starting the new runtime, including v1
traffic with the experimental scanner disabled. SQLite bootstrap checks for the
column before adding it so migration replay is safe. Legacy NULL values are
preserved and cannot be proactively recovered by guessing a policy. No backfill
or cross-node transaction is required for this decision. Downgrade may retain the column;
concurrent old/new writers are unsupported because old writers do not maintain
this decision. Drain active Runs before downgrade; do not resume old-writer
failures with the new scanner without reconciling their saved failure facts.

The same migration also adds Node `runtime_phase` and the Judge
`recovery_lease_owner`, `recovery_lease_token`, `recovery_lease_until_ms` fields.
New rows start without a phase or lease and token zero. A saved artifact alone
does not backfill `judging` for old rows. The normal Judge path also requires these
columns, even with the scanner disabled. FinishJudge commits its Node result,
Judge audit and optional public Event in a single local transaction. Drain active
Runs before downgrade; old writers do not participate in this fencing contract.
SQLite 029 includes the full schema in this PR. Once merged, later schema
additions use a new version. Earlier Loop development drafts are outside the
automatic upgrade scope; normal migration identity validation remains enabled.

The same migration creates `bcs_collaboration_delivery_checkpoints` for the rendered
opening payload and its history barrier. Apply it before normal v1/v2 startup;
existing Runs are not backfilled from today's Group configuration. Opening
history uses the existing `bcs_messages.message_id` primary key with
`message_id = client_msg_id = {run_id}:000-panel`, without adding a Message
column or a distributed transaction. A legacy history row may retain its old
message ID when its client key and exact payload already match. The checkpoint
table may remain on downgrade; drain active Runs because old binaries neither
save nor honor this startup checkpoint.

Bot dispatch uses the same checkpoint table, adding Node/attempt, original
deadline, lease owner/token/until and saved error columns. Its Run lookup index is
`(env, aggregate_id, operation_kind, status)`. The immutable payload contains the
rendered request and target reference, excluding URLs, credentials and forwarding
headers. A durable send marker precedes IO; recovery can send an unsent Pending
request, but never replays Delivering. ACK and ambiguity expiry each use a small
checkpoint/Node transaction. Expiry records the existing failure decision and
retires the checkpoint, without overriding an accepted request or saved artifact.
The fallback dispatch deadline does not enable timeouts on accepted Nodes whose
timeout is disabled. Apply this migration before normal v1/v2 dispatch even when the
scanner is off; old writers do not honor this send barrier. No new migration
version or baseline column was added for this slice.

The same migration adds the experimental State Machine progression index.
The `(env, status, record_status, run_id)` index supports
bounded active-Run cursor scans without sorting all historical Runs. Apply it
before enabling the experimental scanner; downgrade can retain this index.
There is no new progression state table or data backfill.

Terminal Service Session recovery is also included in MySQL 028 / SQLite 029.
Its `(env, session_kind, status, session_id)`
index supports bounded Running Session scans without sorting historical Sessions.
Apply before enabling the experimental scanner; downgrade may retain the index.
Completion uses the existing Run `session_activation_count` and Session
`activation_count`, without a cross-Run/Session transaction or new state column.
Runs lacking saved activation metadata are excluded from proactive recovery and
cannot complete a Service Session through the guarded State Machine path.

Terminal IM uses the same checkpoint table with nullable `progress_json`
(JSON on MySQL, TEXT on SQLite) and the bounded pending-page index
`(env, operation_kind, status, aggregate_id)`. Other operation kinds leave progress
NULL. The immutable payload freezes the original target set, text, activation and
deadline; progress records cleanup and each recipient's send/ack state. The
normal path saves this intent before completing the Service Session, even when
the scanner is disabled. Apply this schema before starting this binary;
already completed historical Sessions are not backfilled. SQLite 029 creates
these fields together with the checkpoint table; no whole-workflow transaction
is needed.
The new scanner preserves unknown sends without replay and fences old activations;
downgrade must drain pending IM as well as active Runs.

Missing startup recovery stores its typed `startup_failure` fact in the same
checkpoint table, with no schema change. The conditional Run failure and this
fact commit together only on the failure path; normal startup adds no writes.
The fact authorizes snapshot-less completion of the original failed Service
activation and is not a delivery operation. Keep it until Session completion
and the applicable audit retention boundary; do not infer it from error text.

Control-query optimization adds MySQL `025_delivery_pending_abort.sql` and
SQLite `026_delivery_pending_abort.sql`. Apply before the new worker starts;
SQLite applies it through bootstrap, remote databases require deployment DDL.
This adds only the `(env, status, abort_request_id, delivery_id)` index. Existing
run/cancel deadline indexes are reused. A binary downgrade can retain this index;
there is no data backfill or additional persisted delivery state in this migration.

Delivery worker optimization adds MySQL `023_delivery_worker_queries.sql` and
SQLite `024_delivery_worker_queries.sql` (dialect numbering differs due to the
earlier SQLite-only history). Apply before the optimized worker starts. The new
`downstream_run_id` column is a derived index projection of transport JSON, not
a new logical run identity. Older binaries do not maintain this projection;
mixed-version concurrent writers are unsupported, and a downgrade/re-upgrade
requires a reviewed alias-projection reconciliation before resuming the worker.

Migrations are forward-only. If a production migration must be reverted, author
a reviewed paired revert migration or DBA change plan. Automatic rollback is not
provided.
