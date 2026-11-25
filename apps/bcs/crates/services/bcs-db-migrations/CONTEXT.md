# bcs-db-migrations Context

## Provides

- The code-defined SQLite schema bootstrap: missing-table creation first,
  then versioned migrations, then index creation
  (`run_sqlite_bootstrap_tables`, `run_sqlite_versioned_migrations`,
  `run_sqlite_bootstrap_indexes`).
- The versioned SQLite migration chain (versions 1..50) with per-migration
  SHA-256 checksums, applied-history validation, repair conditions for legacy
  local databases, and the `bcs_schema_migrations` record bookkeeping.
- Migration state inspection (`check_sqlite_migrations`) and the apply-report
  summary (`SqliteMigrationReport` / `SqliteMigrationPlan` /
  `run_sqlite_migrations_with_report`), plus `sqlite_target_version()` and
  `sqlite_migration_count()` for CLI reporting.
- The full behavior test suite (65 tests) that previously registered 30 times
  across the workspace via `#[path]` source includes; they now run once here.

## Allowed dependencies

- Normal: `plugin-api/bcs-db-api` only (plus pure hashing `sha2`/`hex`).
- Dev-only: `plugins/bcs-db-local` (the real SQLite DB plugin backing the
  behavior tests) and `tokio`/`serde_json` for test execution and JSON fixtures.
- Enforced by `scripts/ci/check-deps.sh` (the `migration` gate: the crate must
  exist exactly once and any runtime/build path dependency must be
  `bcs-db-api`).

## Forbidden dependencies

- `bootstrap/bcs` and every other bcs-internal crate in the normal
  dependency graph (no reverse dependency from the runner into the runtime).
- HTTP/WS delivery adapters, business services, caches, or concrete DB plugins
  outside dev-dependencies.

## Schema ownership

- This crate owns SQLite DDL and upgrade semantics for BCS local databases and
  the shard DDL baseline embedded in `migrations/baseline.rs` /
  `migrations/schema.rs`. MySQL / OceanBase chain-applied `migrations/mysql`
  and `migrations/legacy/mysql` archives stay owned by `tools/bcs-admin
  migrate`; SQLite per-version `.sql` is read from the shared
  `migrations/sqlite` directory. The `.sql` content is frozen; executor
  semantics (transaction/order/error propagation/repair conditions) are
  frozen with it.

## Consumers

- `bootstrap/bcs` depends on it normally and re-exports the public API as
  `bcs::migrations` for backwards compatibility.
- The integration/contract test consumers import it as a dev-dependency under
  their historical alias (`use bcs_db_migrations as bootstrap_migrations;` or
  `as migrations;`): the store/service/application test files listed in the
  code-build-footprint plan appendix D, and `bcs-admin`'s SQLite chain test.

## Tests

- `cargo test -p bcs-db-migrations` — 65 tests, including fresh empty-DB
  init, legacy-database upgrades (pre-existing records / partial steps),
  repeated-startup idempotency, checksum-mismatch and write-failure error
  propagation, cross-dialect migration-content mirrors.
- MySQL-ignored contract tests in consumer packages remain with their owners;
  running them against a real MySQL database is Task 7 of the measured
  footprint plan, not this crate's default suite.

SQLite 046 / MySQL 048 add the public distributed runtime immutable archive.
SQLite upgrade tests preserve the prior history and retained object bytes across
restarts. The MySQL table shape is statically checked; real MySQL/OceanBase
acceptance is not implied by those checks.


SQLite 048 / MySQL 050 add the disabled-by-default Agent invocation metadata
foundation (eight tables and bounded indexes). No historical body/checksum or
legacy data is rewritten. New SQLite 048 owns its DDL and history insertion in
one transaction; record failure rolls back both, unlike implicit-commit MySQL
DDL. Upgrade tests preserve populated legacy rows and prior migration history.
The separate MySQL CI contract must execute the full chain on an EMPTY database;
static schema checks and local SQLite tests are not a MySQL acceptance result.

SQLite 049 / MySQL 051 add command registration/requester fences. Existing
approval/request/fact metadata defaults to unknown binding (zero), not invented
registration proof. Bot/Human registration rows receive a dedicated identity
revision column, but lifecycle writers still need to advance it before production
activation. MySQL widens legacy env and request operator columns without changing
the five-column edge unique key or actor collations. New SQLite 049 keeps DDL and
history atomic; committed 048/050 and all earlier migrations remain frozen.
Upgrade tests preserve prior history/data and inject history-record failure.
Static MySQL key-width checks do not replace full-chain execution on real MySQL.


SQLite 050 / MySQL 052 add the AgentInvoke request-pair and audit pair/time /
action/time query indexes as one additive upgrade per dialect. No committed
migration body, checksum or retained application data is changed. SQLite tests
verify index installation, existing history/data preservation and atomic
DDL/history rollback. Cross-dialect static key-width checks retain the native
MySQL 3072-byte budget; they do not replace real MySQL execution or query plans.
