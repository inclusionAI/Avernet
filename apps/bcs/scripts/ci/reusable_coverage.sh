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
# Process-level test helpers spawn `<target>/<profile>/bcs` and `bcs-cli` next to
# the instrumented test binaries. Depending on the restored cache contents those
# bins can be absent even when the test binaries exist, which fails the
# integration_http_api / e2e_bot_authority spawn suites with ENOENT. Build the
# bins of the packages whose tests spawn them so every run is deterministic.
# Resolve the package list dynamically: reusing this script in synthetic fixture
# workspaces (singlebox/ci/tests) must not reference packages that do not exist.
spawn_pkgs="$(python3 -c '
import json
names = {p["name"] for p in json.load(open("testresult/cargo-metadata.json"))["packages"]}
print(" ".join(f"-p {n}" for n in ("bcs", "bcs-cli") if n in names))
')"
if [ -n "$spawn_pkgs" ]; then
  # Intentional word splitting: the variable holds repeated "-p <name>" pairs.
  # shellcheck disable=SC2086
  cargo build --locked $spawn_pkgs --bins
  # Pin the spawned server binaries for the process-level tests: nextest may
  # execute their test binaries outside `debug/deps`, breaking their relative
  # path arithmetic (observed as ENOENT in runs 43345664811 / 43351522373 /
  # 43369707101). The helpers honor BCS_E2E_SERVER_BIN first.
  export BCS_E2E_SERVER_BIN="$CARGO_TARGET_DIR/debug/bcs"
  export BCS_E2E_CLI_BIN="$CARGO_TARGET_DIR/debug/bcs-cli"
fi
cargo nextest list --workspace --locked --list-type binaries-only --message-format json \
  > testresult/test-binaries.json
set +e
cargo nextest run --cargo-metadata testresult/cargo-metadata.json \
  --binaries-metadata testresult/test-binaries.json --config-file "$config" \
  --profile ci --retries 0 "$@"
status=$?
set -e
cargo llvm-cov report --cobertura --output-path testresult/cobertura.xml
exit "$status"
