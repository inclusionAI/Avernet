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

## CI coverage follow-up — 2026-11-16

The original [BCS CI job](https://github.com/inclusionAI/Avernet/actions/runs/43726757541/job/132850255451)
passed all 8,387 tests and the 88.75% overall line-coverage gate, but failed
changed-line coverage: 0 of 338 instrumentable added lines were covered.
All 338 lines belonged to the five distributed assertion-harness modules.
The new model/Service API serialization tests did run; none called the reusable
harnesses because production providers do not exist yet. Replaying the downloaded
JUnit/Cobertura reports through the unchanged `scripts/cov_gate.py` reproduced
the failure locally.

The [distributed_harness integration target](../../crates/test-support/bcs-test-support/tests/distributed_harness.rs)
now invokes all 23 harness entries through test-only scripted doubles. It checks
exact method arguments and call ordering, positive outcomes, storage-error
propagation, and deliberate violations of admission/content idempotency, commit
resolution, activation settlement, dispatch ownership, pending-input confirmation,
access control, reducer determinism, frozen definitions and replay-gap reporting.
It adds no production provider, role loop, dependency, interface or gate exemption.
These tests verify the assertions themselves; backend conformance and fault
recovery remain unverified until actual providers run the shared harnesses.

Executed from `apps/bcs` with Rust 1.94.1:

```sh
cargo +1.94.1 test --locked -p bcs-test-support --test distributed_harness
cargo +1.94.1 test --locked -p bcs-dist-model -p bcs-service-api -p bcs-test-support --quiet
cargo +1.94.1 llvm-cov --locked -p bcs-test-support --test distributed_harness \
  --cobertura --output-path /tmp/pr4020-focused-coverage.xml
```

- Harness self-tests: **25 passed**, including negative scenarios; all 23 entries
  are called. Related crate tests/doc-tests: **331 passed, 0 failed**, with one
  existing ignored documentation example.
- Focused instrumented run: **331/338 harness lines covered (97.93%)**, above
  the unchanged 85% changed-line requirement. This focused run is not a full
  workspace overall-coverage result.
- Local Rust 1.94.1 `llvm-tools` download timed out. The coverage command used
  explicit `LLVM_COV`/`LLVM_PROFDATA` paths to the already installed LLVM 21.1.2
  tools, an isolated `CARGO_LLVM_COV_TARGET_DIR`, line-tables-only debug info and
  `CARGO_INCREMENTAL=0`. The repository's Rust 1.94.0 pin is unchanged; the
  original GitHub job successfully provisioned that exact toolchain.
- Changed files pass whitespace and 1,000-line source-file checks. Production
  contracts and existing unrelated work are unchanged. Full workspace/MySQL
  verification of the follow-up is delegated to the new GitHub CI run.
- The enabled pre-push secret scanner also flagged the original serialization
  fixture's opaque cursor text as a credential literal. Its value is now the
  explicit `example` placeholder; backend/generation/scope assertions are
  unchanged. The five model persistence-shape tests were rerun and passed.

## Ownership contract refinement — 2026-11-16

Rebased onto authoritative dev `cf9c8c414b`. The unrelated HTTP test-method
correction is already present on dev and is not part of the PR diff.

The shared identity is now `ShardOwner`; `SessionOwnership` remains scoped to
one session. Shard claims, checkpoint envelopes, commit receipts and recovery
responses no longer expose a separate revision. Claim and commit preconditions
are `expected_epoch` and `expected_commit_seq`, with atomic ownership checks.
The authority placeholders were removed from `ShardClaim`, `MembershipLease`
and `SessionOwnership`. Exact instance/epoch, session sealing and live-registration
checks remain required; commit proofs and dispatch scheduling versions remain.

Executed from the repository root with the installed Rust 1.94.1:

```sh
cargo +1.94.1 test --locked --manifest-path apps/bcs/Cargo.toml \
  -p bcs-dist-model -p bcs-service-api -p bcs-test-support --quiet
```

- **337 tests/doc-tests passed, 0 failed, 1 existing ignored example**, including
  **31 distributed harness self-tests**. Added scenarios reject competing stale
  epochs/sequences, incorrect commit receipts, an unsealed higher owner epoch,
  repeated recovery counts, recovery writes that discard outbox, and lost leases
  that revive or affect replacement instances.
- Port-purity and store-boundary checks passed from `apps/bcs`; changed Rust
  formatting, whitespace, source-file size and local Markdown links passed.
- The tests use scripted doubles to validate the reusable assertions; no actual
  persistence provider, role loop, lease implementation or adapter was added.
- The Rust 1.94.0 repository pin is unchanged. Full CI/coverage on the final
  revision, native TBase conditional-write/sealing behavior, and independent
  recovery-metadata persistence still require provider/CI validation. Earlier
  coverage results above are not a coverage claim for this revision.

## Session specification read boundary — 2026-11-16

Moved `get_spec` from `InboxProducerPort` to `SessionStorePort`. Producer,
Consumer and ShardFeed remain separate; admission still owns initial spec/task
creation, durable input and recoverable activation. No production implementation
or extra runtime read was introduced.

Repeated the three-crate test command above with Rust 1.94.1:

- **340 tests/doc-tests passed, 0 failed, 1 existing ignored example**, including
  **34 distributed harness self-tests**. The new cases check spec visibility
  after admission before any checkpoint, unchanged frozen spec on retry, and
  storage-error propagation instead of treating failure as absence.
- Port-purity and store-boundary checks passed from `apps/bcs`. Changed Rust
  formatting, whitespace, source-file size and local Markdown links passed.
  One pre-existing indentation difference in `inbox.rs` is unchanged.
- These remain assertion-harness self-tests using scripted doubles. Actual
  provider conformance, runtime behavior and full final-revision CI/coverage
  remain unverified.
