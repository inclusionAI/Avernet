# bcs-db-migrations Context

## Provides

- The code-defined SQLite schema bootstrap: missing-table creation first,
  then versioned migrations, then index creation
  (`run_sqlite_bootstrap_tables`, `run_sqlite_versioned_migrations`,
  `run_sqlite_bootstrap_indexes`).
- The versioned SQLite migration chain (versions 1..42) with per-migration
  SHA-256 checksums, applied-history validation, repair conditions for legacy
  local databases, and the `bcs_schema_migrations` record bookkeeping.
- Migration state inspection (`check_sqlite_migrations`) and the apply-report
  summary (`SqliteMigrationReport` / `SqliteMigrationPlan` /
  `run_sqlite_migrations_with_report`), plus `sqlite_target_version()` and
  `sqlite_migration_count()` for CLI reporting.
- The full behavior test suite (49 tests) that previously registered 30 times
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

- `cargo test -p bcs-db-migrations` — 49 tests, including fresh empty-DB
  init, legacy-database upgrades (pre-existing records / partial steps),
  repeated-startup idempotency, checksum-mismatch and write-failure error
  propagation, cross-dialect migration-content mirrors.
- MySQL-ignored contract tests in consumer packages remain with their owners;
  running them against a real MySQL database is Task 7 of the measured
  footprint plan, not this crate's default suite.
