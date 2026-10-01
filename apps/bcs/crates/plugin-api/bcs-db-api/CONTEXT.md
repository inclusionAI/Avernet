# bcs-db-api Context

## Provides

- Infrastructure database plugin contract for BCS services.
- Shared DB error types and async trait boundary.
- A driver-level SQL execution surface for local and remote implementations.
- `ExecuteChecked` transaction steps require an exact affected-row count before
  commit; mismatch returns `DbError::ConditionFailed` and rolls back every step.
  Existing Execute semantics are unchanged. Plugins must implement the new enum
  variant explicitly; services use version-changing CAS to avoid no-op UPDATE
  affected-row differences between MySQL and SQLite.

## Consumes

- Async trait and error helper crates only.

## Allowed dependencies

- Contract helper crates such as `async-trait` and `thiserror`
- Test-only reuse through `bcs-test-support`

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- `services/*`
- `plugins/*`
- Internal SDKs or external middleware clients

## Configuration

- This crate defines the DB capability contract only.
- Concrete datasource config is supplied to implementations by bootstrap.

## Runtime ownership

The crate owns DB capability semantics. It does not own service persistence policy, SQL dialect adaptation above the contract, or implementation selection.

## Tests

- `cargo test --package bcs-db-api --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-db-api --all-targets --manifest-path src/bcs/Cargo.toml`

## Transaction early success (API v1 extension)

A plain Execute may opt into `DbStatement::with_transaction_stop_on_no_rows`.
Zero affected rows commits the executed prefix and returns only its results;
remaining SQL and bindings are skipped. Nonzero results continue atomically.
Execution/binding failures roll back, and commit failures propagate. Query,
ExecuteChecked and standalone operations reject the option. Both local SQLite
and MySQL implement and run the shared `db_plugin_contract_tests` semantics.
Existing enum variants, method signatures and unflagged semantics are unchanged.
Consumers and third-party implementations must coordinate support for the option.

Statement and transaction types live in `src/transaction.rs` and retain root
re-exports. See [idle polling spec](../../../specs/2026-09-15-eventing-idle-polling/spec.md).
