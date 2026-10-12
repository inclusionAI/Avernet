# bcs-test-support Context

## Provides

- The result-publisher conformance command includes the original message timestamp,
  preserving Chat publication identity across persistence/recovery consumers.

- Fixed Loop instrumentation noop and shared hook conformance inputs, exercised
  by noop tests and the bootstrap Prometheus adapter's rendered-series tests.

- Shared contract-test harnesses for BCS service and plugin boundaries.
- Plan-Task-13/14/17/18 authority-lane shared harnesses
  (`bot_manager_service_contract_tests`,
  `team_manager_sync_service_contract_tests`,
  `ownership_transfer_service_contract_tests`,
  `ownership_migration_service_contract_tests`,
  `ownership_migration_core_service_contract_tests`,
  `team_manager_credential_verifier_port_contract_tests`) plus the
  fail-closed Noops those lanes register (`NoopBotManagerService`,
  `NoopBotOwnershipTransferService`, `NoopTeamManagerSyncService`) and the
  recording doubles (`RecordingBotAuthorityHook`,
  `CountingBotAuthorityCore`) the conformance drivers construct production
  facades over. Noop evolution is explicit: answers stay fail-closed
  (Forbidden / fixed-code deny), and inherited trait defaults never stand in
  for a required Noop method.
- Bot-owned Provider metadata, binding-projection and authorized deletion
  contracts; consumers supply Memory/SQL repositories or Core implementations.
- Reusable fixtures and helpers for local conformance testing.
- A single place to host boundary-level test utilities reused across crates.
- Request-scoped JSON log capture for checking failure diagnostics and correlation,
  enabled only through the consuming crate's dev-dependency `diagnostic-logs` feature.

## Consumes

- `bcs-service-api`, `bcs-cache-api`, and `bcs-db-api` contract crates.
- Test runtime crates such as `tokio`.
- Optional `bcs-observability` request-ID scopes and `tracing-subscriber` for test-local log capture.

## Allowed dependencies

- `service-api/*`
- `plugin-api/*`
- Test-only helper crates
- `auxiliary/bcs-observability` for diagnostic test context

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- `services/*` concrete runtime crates
- `plugins/*` concrete implementations in shared harness code
- `external-clients/*`

## Configuration

- Test setup is provided by each consuming test crate.
- This crate must not read production env or embed production bootstrap logic.

## Runtime ownership

The crate owns shared test fixtures and contract harnesses only. It does not participate in production runtime wiring.

## Tests

- `cargo test --package bcs-test-support --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-test-support --all-targets --manifest-path src/bcs/Cargo.toml`
