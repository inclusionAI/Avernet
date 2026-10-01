# Validation — Eventing idle polling

## Completed checks

- `cargo test --manifest-path src/bcs/Cargo.toml -p bcs-db-api -p bcs-db-local
  -p bcs-db-mysql -p bcs-config-api -p bcs-event-store -p bcs-eventing --offline`:
  201 tests passed; two real-MySQL entries were ignored in this offline run and
  then executed separately against a disposable local MySQL 8.4 instance.
- `cargo test --manifest-path src/bcs/Cargo.toml -p bcs-db-mysql
  -p bcs-event-store --offline -- --ignored`: both MySQL conformance entries
  passed. DB Plugin tests cover text and prepared protocols; Event Store tests
  include lease expiry/recovery, ordering and second-precision timestamps.
- `cargo test --manifest-path src/bcs/Cargo.toml -p bcs-event-store
  --test idle_claims --offline`: 27 tests passed, including imported migration
  tests, four-worker idle statement counting, and attempt-write failure rollback.
- `cargo test --manifest-path src/bcs/Cargo.toml -p bcs-db-local
  --test transaction_commit --offline`: passed; a deferred foreign-key failure
  proves early success propagates commit errors and rolls back earlier writes.
- Virtual-clock worker tests verify bounded jitter, fixed legacy cadence,
  backoff reset on work/errors and immediate cancellation of idle waits.
- Verified all 18 moved EventRepoPort implementations against the original:
  only the intended changes to the two claim methods alter their bodies.
- Added/modified Rust files satisfy the 1000-line limit. `git diff --check` passes.

## Gate verification

### Configuration and full BCS suite

- `CARGO_NET_OFFLINE=true bash scripts/ci/check-config-validation.sh`, run from
  `src/bcs`: all 126 tests passed after allowing local test ports.
- `LC_ALL=C LC_CTYPE=C LANG=C NEXTEST_TEST_THREADS=2 CARGO_NET_OFFLINE=true
  bash src/bcs/scripts/ci_test.sh --fast-fail`: 4,776 tests ran; 4,775 passed,
  one failed and 49 were skipped. An earlier attempt was interrupted by SIGKILL
  during test enumeration; this rerun completed test execution.
- The sole failure was the unchanged CLI test
  `client::tests::test_chat_async_returns_transport_error_on_non_listening_port`:
  requesting `127.0.0.1:1` returned HTTP 500 instead of a transport error.
  With the same test binary, the inherited proxy environment reproduced the
  failure; adding `NO_PROXY=127.0.0.1,localhost` and the lowercase equivalent made
  it pass. No CLI or proxy configuration was changed. This isolated result does
  not replace the full-suite result above; the full gate did not pass.

### Architecture gates

The import-boundary checker reports 48 findings and the Rule 25 structural and
harness checker reports 151 findings. Both normalized finding sets are identical
when the same checkers run on the original HEAD sources: no new findings in
either set. Checks for port purity, forbidden symbols and store boundaries passed.
The aggregate architecture gate did not pass; duplicate test enumeration was
stopped after the static comparisons, while the full BCS suite ran separately.
No gate or baseline was weakened.

### Singlebox coverage

`scripts/ci/singlebox_coverage.sh --coverage-root
/tmp/avernet-eventing-singlebox` could not reach E2E execution. Initial attempts
encountered sandbox restrictions on local test ports and an unsupported locale.
After allowing local ports and using the C locale, BaaS started but Backend did
not become ready before the startup timeout. Separate ports avoided interfering
with an existing local stack. The test stack and disposable MySQL instance were
cleaned up.

Consequently, Singlebox acceptance/E2E and coverage thresholds remain unverified.
`verify_singlebox_coverage_artifacts.py` failed because the startup failure left
no required report artifacts. This is not a measured coverage result.

## Deployment measurements

The idle test executes 80 SQL statements for four workers each issuing ten fanout
and ten delivery claims, versus 320 statements for the prior plans. This is a
75% reduction in these SQL statements; BEGIN/COMMIT and total database resource
usage are not included in that figure.

Production SQL rate, transaction cost, delivery P95/P99, backlog and retry/recovery
metrics remain unmeasured. Adaptive idle ceilings stay opt-in until a deployment
owner verifies its latency budget under the intended replica count and traffic.
