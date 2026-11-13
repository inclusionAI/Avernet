#!/usr/bin/env bash
set -euo pipefail

# Export the exact instrumented workspace build for later nextest reuse. Report
# unit coverage before running any ignored MySQL contracts so its scope is stable.
config="$1"
shift
mkdir -p testresult
rm -f testresult/{junit.xml,cobertura.xml,cargo-metadata.json,test-binaries.json}
# Unlike `llvm-cov nextest`, show-env defaults to Cargo's ordinary target.
# Resolve Cargo configuration before choosing the same isolated coverage target
# that CI caches; retain an explicitly requested llvm-cov target override.
cargo metadata --locked --no-deps --format-version 1 > testresult/cargo-metadata.json
cargo_target="$(python3 -c 'import json; print(json.load(open("testresult/cargo-metadata.json"))["target_directory"])')"
export CARGO_TARGET_DIR="${CARGO_LLVM_COV_TARGET_DIR:-$cargo_target/llvm-cov-target}"
coverage_env="$(cargo llvm-cov show-env --sh)"
eval "$coverage_env"
cargo llvm-cov clean --workspace
cargo metadata --locked --format-version 1 > testresult/cargo-metadata.json
cargo nextest list --workspace --locked --list-type binaries-only --message-format json \
  > testresult/test-binaries.json
# Spawn-based integration tests (bootstrap/bcs/tests/integration_http_api,
# e2e_bot_authority) execute the bootstrap package's NON-TEST bin product at
# <target-dir>/debug/bcs. `cargo nextest list` builds the test binaries but
# has reported the bin unit up-to-date on runners whose restored rust-cache
# excluded workspace artifacts (cache-workspace-crates: false): fingerprints
# fresh, product file gone. Every spawn then dies with ENOENT and the
# bootstrap runtime lines fall out of coverage (2026-11-13 CI incident: 8
# spawn failures + overall line coverage 85.17% vs the required 87%). Assert
# the product exists and force a rebuild when the cache state lied.
if ! test -x "$CARGO_TARGET_DIR/debug/bcs"; then
  echo "coverage build: $CARGO_TARGET_DIR/debug/bcs missing after nextest build; forcing rebuild" >&2
  touch crates/bootstrap/bcs/src/main.rs
  cargo build -p bcs --bin bcs
fi
set +e
cargo nextest run --cargo-metadata testresult/cargo-metadata.json \
  --binaries-metadata testresult/test-binaries.json --config-file "$config" \
  --profile ci --retries 0 "$@"
status=$?
set -e
cargo llvm-cov report --cobertura --output-path testresult/cobertura.xml
exit "$status"
