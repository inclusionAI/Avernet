# bcs-db-local Context

## Provides

- Local database implementation of `DbPlugin`.
- Dependency-light SQL execution for local development and contract tests.
- A concrete DB adapter without remote middleware dependencies.

## Consumes

- `bcs-db-api` contract types.
- Local database driver crates and async runtime primitives.

## Allowed dependencies

- `plugin-api/bcs-db-api`
- Utility crates needed for local database access

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- `services/*`
- Service-owned persistence policy or business repositories

## Configuration

- Bootstrap or tests select this implementation explicitly.
- Local database path and driver options must arrive through constructors or test setup.

## Runtime ownership

The crate owns local DB driver mechanics only. It does not own service-level SQL policy or schema semantics above the plugin contract.

File-backed SQLite owns five connections, each with WAL, foreign keys and a
5000 ms busy timeout. In-memory construction keeps one isolated connection.
An async semaphore bounds checkout; blocking-pool jobs own connection leases
until completion, including when the awaiting caller is cancelled. Transactions
pin one connection and use BEGIN IMMEDIATE when containing Execute steps to
avoid read-to-write snapshot upgrades; query-only transactions remain deferred.
This changes local execution concurrency, not the DbPlugin contract or durability
settings. SQLite remains single-writer. Timing spans propagate into blocking jobs.

## Tests

- `cargo test --package bcs-db-local --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-db-local --all-targets --manifest-path src/bcs/Cargo.toml`
