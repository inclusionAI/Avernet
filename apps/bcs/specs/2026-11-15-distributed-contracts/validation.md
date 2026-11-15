# Declaration validation

Date: 2026-11-15. Base: Avernet `5a126bd84f` from authoritative `dev`.
Scope: domain data, 23 service/port traits, reusable assertion entry points and
documentation. No new production trait implementation, provider, role loop,
bootstrap registration, migration or deployment is part of this change.

## Commands executed

Commands below run at the repository root unless otherwise stated.

```sh
cargo +1.94.1 check --manifest-path apps/bcs/Cargo.toml \
  -p bcs-dist-model -p bcs-service-api -p bcs-test-support --all-targets
cargo +1.94.1 test --manifest-path apps/bcs/Cargo.toml \
  -p bcs-dist-model -p bcs-service-api --quiet
git diff --check
```

- Check: **passed**, exit 0, including the new test-support harnesses.
- Tests: **282 passed, 0 failed**, across 43 test/doc-test targets. This includes
  5 new model serialization tests, 2 new checkpoint/effect-shape tests and
  2 compile-fail documentation tests. The remainder are existing Service API
  tests. The compile-fail tests reject ordering opaque inbox positions and
  serializing ephemeral delivery receipts.
- Existing warnings remain in `bcs-bot-store/src/lib.rs` and
  `bcs-service-api/tests/caller_container_preparation_contract.rs`; neither source
  was changed.
- Formatting was limited to the 30 newly added Rust files, not the workspace.
- Diff whitespace validation passed. New documentation links were checked;
  source files are below the 1,000-line limit.
- All 23 new public traits have a matching callable
  `snake_trait_contract_tests` entry in test support. This verifies entry coverage
  and compilation, not execution against an unimplemented provider.

The repository pins Rust 1.94.0. Automatic toolchain provisioning hit a concurrent
download error and then repeatedly failed to fetch `llvm-tools`; the pending
download was stopped. Verification used the already installed Rust 1.94.1.
The exact pinned-toolchain gate remains to be run in CI/a working 1.94.0 setup.
The repository toolchain file was not changed.

## Architecture gate results

Executed from `apps/bcs`, with `RUSTUP_TOOLCHAIN=1.94.1`:

| Script | Actual result |
| --- | --- |
| `scripts/ci/check-port-purity.sh` | Passed |
| `scripts/ci/check-store-boundaries.sh` | Passed |
| `scripts/ci/check-trait-naming.sh` | Failed on 10 pre-existing trait names; no new trait reported |
| `scripts/ci/check-import-rules.sh` | Failed on pre-existing adapter/service/contract imports; no new source reported |
| `scripts/ci/check-deps.sh` | Failed on existing `bcs-protocol -> bcs-domain` and `bcs-service-api -> bcs-config-api/bcs-storage-api`; cargo-machete found no unused dependencies in its scanned roots |

All 62 source files reported by the naming/import checks were compared byte for
byte with the base commit and were unchanged. The three dependency edges also
exist in baseline manifests. These failures were not suppressed or waived.
The dependency gate adds the new pure model crate to the Service API allowlist
and separately forbids that model crate from depending on other BCS crates.

The whole-workspace architecture/conformance gate was not claimed as passing.
Its full workspace discovery/build was not run for this declaration-only change.

## Not yet verified

- No concrete provider runs the new semantic harnesses yet. Complete concurrency
  and fault fixtures remain S1; in-memory and durable providers remain S2/S3/S4.
- No real backend conditional-write, queue replay, retention, rebalance,
  cross-backend reconciliation or remote idempotency guarantee was tested.
- No Gateway/Worker/Connector integration, crash recovery, load or deployment
  validation was performed because their implementations are not part of scope.
- The declaration validation above predates branch publication. Publication was
  subsequently requested with `--no-verify`: no additional validation or Git
  hooks are run for that commit/push. The earlier results are not a claim that
  all CI gates pass. No merge, deployment or host submodule update is included.

See [ordered development tasks](tasks.md) and the owning
[contract semantics](../../crates/service-api/bcs-service-api/src/port/repo/distributed/README.md).
