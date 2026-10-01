#!/usr/bin/env bash
# scripts/ci/tests/checker_scope_regression.sh
# Self-check for the architecture scope checkers (TEST-2 protocol compat, R10
# store boundaries). It builds temporary miniature BCS trees and asserts that:
#   1. a valid fixture passes both checkers,
#   2. a missing expected source directory FAILS (no PASS on missing trees),
#   3. an injected DbPlugin reference FAILS R10,
#   4. a missing required protocol test file FAILS TEST-2.
# Each case runs the checkers from two working directories (the repository
# root and the fixture tree root) so cwd can never rescue a broken scan.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# SCRIPT_DIR = <bcs-root>/scripts/ci/tests
BCS_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
REPO_ROOT="$(cd "$BCS_ROOT/../.." && pwd)"
CHECK_DIR="$BCS_ROOT/scripts/ci"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

PROTOCOL_TEST_FILES=(
  coordination_contract_alignment
  domain_contracts
  provider_bot_connection_mode_dto
  provider_bot_webhook_dto
)
SERVICE_TARGETS=(bcs-bot bcs-group bcs-friend bcs-relation bcs-proposal)

failures=0

note() { echo "[checker-scope] $*"; }
bail() {
  echo "[checker-scope] FAIL: $*" >&2
  exit 1
}

# build_tree <root> — valid miniature BCS tree.
build_tree() {
  local root=$1 dir name
  mkdir -p "$root/scripts/ci" "$root/crates/contracts/bcs-protocol/tests"
  for name in "${SERVICE_TARGETS[@]}"; do
    dir="$root/crates/services/$name/src"
    mkdir -p "$dir"
    printf 'pub struct %sRepoShim;\n' "$name" > "$dir/lib.rs"
  done
  for name in "${PROTOCOL_TEST_FILES[@]}"; do
    printf '// protocol wire contract test: %s\n' "$name" \
      > "$root/crates/contracts/bcs-protocol/tests/${name}.rs"
  done
}

# run_case <name> <expected: pass|fail> <cwd> <script> [args...]
run_case() {
  local name=$1 expected=$2 cwd=$3 script=$4
  shift 4
  local out rc
  set +e
  out=$(cd "$cwd" && bash "$script" "$@" 2>&1)
  rc=$?
  set -e
  if [[ $rc -eq 77 ]]; then
    note "FAIL case '$name': checker exited 77 (SKIP) — errors must not be skips"
    failures=$((failures + 1))
    return
  fi
  if [[ $expected == pass && $rc -ne 0 ]]; then
    note "FAIL case '$name': expected exit 0, got $rc; output:"
    note "$out"
    failures=$((failures + 1))
  elif [[ $expected == fail && $rc -eq 0 ]]; then
    note "FAIL case '$name': expected non-zero exit, checker passed; output:"
    note "$out"
    failures=$((failures + 1))
  else
    note "ok case '$name' (exit $rc)"
  fi
}

command -v rg >/dev/null || bail "ripgrep (rg) must be installed to run this regression"

[[ -f "$CHECK_DIR/check-store-boundaries.sh" && -f "$CHECK_DIR/check-protocol-compat.sh" ]] \
  || bail "scope checkers not found under $CHECK_DIR"

# Copy the real checkers into the fixture so their SCRIPT_DIR resolution
# anchors BCS_ROOT inside the temporary tree — never at the real checkout.
stage_fixture() {
  local root=$1
  mkdir -p "$root/scripts/ci"
  cp "$CHECK_DIR/check-store-boundaries.sh" "$CHECK_DIR/check-protocol-compat.sh" \
    "$root/scripts/ci/"
}

# ── Suite 1: valid fixture passes, from both working directories ──────────
VALID="$WORK/valid"
build_tree "$VALID"
stage_fixture "$VALID"
for cwd in "$REPO_ROOT" "$VALID"; do
  run_case "R10 valid fixture (cwd=$cwd)" pass "$cwd" "$VALID/scripts/ci/check-store-boundaries.sh"
  run_case "TEST-2 valid fixture (cwd=$cwd)" pass "$cwd" "$VALID/scripts/ci/check-protocol-compat.sh"
done

VALID_R10_OUT=$(cd "$VALID" && bash "$VALID/scripts/ci/check-store-boundaries.sh")
[[ "$VALID_R10_OUT" == *"PASS [R10]"* ]] || bail "valid fixture R10 output lacks PASS marker: $VALID_R10_OUT"

# ── Suite 2: missing expected source directory must FAIL ──────────────────
MISSING="$WORK/missing_dir"
build_tree "$MISSING"
stage_fixture "$MISSING"
rm -rf "$MISSING/crates/services/bcs-friend/src"
for cwd in "$REPO_ROOT" "$MISSING"; do
  run_case "R10 missing target dir (cwd=$cwd)" fail "$cwd" "$MISSING/scripts/ci/check-store-boundaries.sh"
done

MISSING_PROTOCOL="$WORK/missing_protocol_dir"
build_tree "$MISSING_PROTOCOL"
stage_fixture "$MISSING_PROTOCOL"
rm -rf "$MISSING_PROTOCOL/crates/contracts/bcs-protocol/tests"
for cwd in "$REPO_ROOT" "$MISSING_PROTOCOL"; do
  run_case "TEST-2 missing tests dir (cwd=$cwd)" fail "$cwd" "$MISSING_PROTOCOL/scripts/ci/check-protocol-compat.sh"
done

# ── Suite 3: injected DbPlugin reference must FAIL R10 ─────────────────────
INJECTED="$WORK/injected"
build_tree "$INJECTED"
stage_fixture "$INJECTED"
printf 'pub fn drain() { let _ = bcs_db_api::DbPlugin::connect; }\n' \
  > "$INJECTED/crates/services/bcs-bot/src/lib.rs"
for cwd in "$REPO_ROOT" "$INJECTED"; do
  run_case "R10 injected DbPlugin (cwd=$cwd)" fail "$cwd" "$INJECTED/scripts/ci/check-store-boundaries.sh"
done

# ── Suite 4: missing required protocol test file must FAIL TEST-2 ──────────
GAP="$WORK/missing_category"
build_tree "$GAP"
stage_fixture "$GAP"
rm "$GAP/crates/contracts/bcs-protocol/tests/domain_contracts.rs"
for cwd in "$REPO_ROOT" "$GAP"; do
  run_case "TEST-2 missing required file (cwd=$cwd)" fail "$cwd" "$GAP/scripts/ci/check-protocol-compat.sh"
done

if [[ $failures -ne 0 ]]; then
  bail "$failures regression case(s) failed — a scope checker can pass without scanning real source"
fi

echo "[checker-scope] PASS: scope checkers scan real source; missing dirs, references, and required files fail loudly"
