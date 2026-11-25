# bcs-db-api Context

## Provides

- Infrastructure database plugin contract for BCS services.
- Shared DB error types and async trait boundary.
- A driver-level SQL execution surface for local and remote implementations.
- SQL migration statement splitting preserves quoted and commented semicolons;
  callers still own dialect selection and SQL validity. Custom delimiters and
  stored routines are outside this helper's supported migration syntax.
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

## Transaction outcome capability (additive API v1 extension)

`DbPlugin::transaction_with_outcome` returns `DbTransactionFailure` with an
independent `DbTransactionOutcome`. NotCommitted means no steps ran or explicit
rollback was acknowledged. Commit/rollback acknowledgement failure remains
CommitUnknown; neither outcome is an automatic retry instruction. Cancellation
returns no outcome and must use application receipt recovery. `DbError` and
legacy methods/variants remain unchanged, preserving custom-plugin source
compatibility. The default delegates to legacy execution and only trusts its
explicit ConditionFailed/Timeout rollback contracts; opaque causes stay unknown,
even if they resemble a duplicate-key error.

This opt-in method rejects scripts, DDL, control statements and commented SQL
before executing any prefix. Callers own transactional tables/functions/triggers
and must not include nontransactional or external side effects. It is not a
general-purpose migration executor. Bundled plugins observe execution, explicit
rollback and commit separately. Shared native capability conformance lives in
`bcs-test-support::contract::plugin::db_transaction_outcome`; this additional
capability is not fabricated by legacy plugins passing the older DB contract.

Repository implementations and test adapters compile with the default method;
this is not an external host build or capability certification.
