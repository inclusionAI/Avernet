# bcs-db-mysql Context

## Provides

- Remote MySQL/OceanBase-backed implementation of `DbPlugin`.
- Concrete database adapter for SQL execution in production-like environments.
- Translation between BCS DB contract semantics and MySQL/OceanBase transport semantics.

## Consumes

- `bcs-db-api` contract types.
- Standard MySQL-compatible driver crates.
- Bootstrap-supplied datasource and credential settings.

## Allowed dependencies

- `plugin-api/bcs-db-api`
- `service-api/bcs-config-api`
- Implementation-specific MySQL driver crates
- Async runtime, driver, and transport helper crates

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- `services/*`
- Service-owned persistence policy or repository logic

## Configuration

- Bootstrap selects this plugin when remote DB is enabled.
- Datasource and credential selection must not leak into services.

## Runtime ownership

The crate owns remote DB transport and driver integration. It does not own service-level SQL or business persistence semantics.

String-valued columns with text character sets decode as `DbValue::String`,
including case-sensitive `_bin` collations. MySQL's `BINARY_FLAG` also marks
such text, so binary payloads are identified by the binary character set
(collation ID 63); geometry remains bytes. BINARY/VARBINARY/BLOB values retain
`DbValue::Bytes` even when their contents are valid UTF-8.

## Tests

- `cargo test --package bcs-db-mysql --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-db-mysql --all-targets --manifest-path src/bcs/Cargo.toml`

Plain Execute transaction steps honor the DB contract's opt-in stop-on-no-rows
control on the same pinned connection, for text and prepared statement protocols.
Zero-row early success commits and returns only the executed prefix; execution
or commit failures propagate. The shared real-MySQL conformance harness covers
this alongside SQLite.

The additive outcome-aware DML executor distinguishes setup failures and
acknowledged rollback (NotCommitted) from commit/unconfirmed rollback
(CommitUnknown). It never infers certainty from provider text or ignores a
rollback failure. Legacy manager/DbPlugin signatures and DbError variants stay
unchanged. Both paths share one step/binding/deadline/early-prefix executor and
pin one connection; no retry or extra SQL statement is added. The opt-in method
rejects DDL/control/scripts/commented SQL before starting and requires only
transactional tables/functions/triggers without external effects. Existing
real-MySQL text/prepared conformance now also calls the native outcome harness;
compilation or a local ignored test is not real-MySQL execution evidence.
