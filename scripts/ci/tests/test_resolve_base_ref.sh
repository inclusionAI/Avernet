#!/usr/bin/env bash
# Tests for scripts/ci/resolve_base_ref.sh
#
# Covers:
# - AVERNET_BASE_REF env var override (highest priority)
# - AVERNET_PRE_PUSH_MERGE_TARGET env var override
# - avernet.prePush.mergeTarget git config override
# - Tracking branch resolution for dev (default)
# - Tracking branch resolution for a non-dev target branch
# - Failure when base ref cannot be resolved
# - AVERNET_BASE_REF with non-dev branch
set -euo pipefail

SCRIPT_UNDER_TEST="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/resolve_base_ref.sh"
ORIG_PWD="$(pwd)"

pass=0
fail=0

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    echo "  PASS: $label"
    pass=$((pass + 1))
  else
    echo "  FAIL: $label — expected '$expected', got '$actual'"
    fail=$((fail + 1))
  fi
}

assert_contains() {
  local label="$1" needle="$2" haystack="$3"
  if [[ "$haystack" == *"$needle"* ]]; then
    echo "  PASS: $label"
    pass=$((pass + 1))
  else
    echo "  FAIL: $label — output does not contain '$needle'"
    fail=$((fail + 1))
  fi
}

assert_exit_code() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" -eq "$actual" ]]; then
    echo "  PASS: $label"
    pass=$((pass + 1))
  else
    echo "  FAIL: $label — expected exit code $expected, got $actual"
    fail=$((fail + 1))
  fi
}

# Create a temporary git repo for testing.
# Does NOT use a subshell so that git operations (branch, checkout) persist
# in the temporary repo. The caller must cd back to ORIG_PWD when done.
setup_repo() {
  local repo_dir="$1"
  mkdir -p "$repo_dir"
  cd "$repo_dir"
  git init --initial-branch=dev >/dev/null 2>&1
  git config user.name "Test User"
  git config user.email "test@example.com"
  echo "initial" > README.md
  git add .
  git commit -m "initial commit" >/dev/null 2>&1
  local bare_repo="$repo_dir/_bare_origin"
  git clone --bare . "$bare_repo" >/dev/null 2>&1
  git remote add origin "$bare_repo" >/dev/null 2>&1
  git fetch origin >/dev/null 2>&1
}

echo "=== Baseline Resolution Tests ==="

# --- Test 1: AVERNET_BASE_REF has highest priority ---
echo ""
echo "Test: AVERNET_BASE_REF has highest priority"
tmpdir="$(mktemp -d)"
setup_repo "$tmpdir"
actual="$(AVERNET_BASE_REF=origin/dev AVERNET_PRE_PUSH_MERGE_TARGET=origin/release/2026-07 REPO_ROOT="$tmpdir" bash "$SCRIPT_UNDER_TEST" 2>/dev/null)"
assert_eq "AVERNET_BASE_REF takes precedence" "origin/dev" "$actual"
cd "$ORIG_PWD" && rm -rf "$tmpdir"

# --- Test 2: AVERNET_PRE_PUSH_MERGE_TARGET when AVERNET_BASE_REF is unset ---
echo ""
echo "Test: AVERNET_PRE_PUSH_MERGE_TARGET when AVERNET_BASE_REF is unset"
tmpdir="$(mktemp -d)"
setup_repo "$tmpdir"
actual="$(AVERNET_PRE_PUSH_MERGE_TARGET=origin/dev REPO_ROOT="$tmpdir" bash "$SCRIPT_UNDER_TEST" 2>/dev/null)"
assert_eq "AVERNET_PRE_PUSH_MERGE_TARGET resolves" "origin/dev" "$actual"
cd "$ORIG_PWD" && rm -rf "$tmpdir"

# --- Test 3: git config avernet.prePush.mergeTarget ---
echo ""
echo "Test: avernet.prePush.mergeTarget git config"
tmpdir="$(mktemp -d)"
setup_repo "$tmpdir"
git config --worktree avernet.prePush.mergeTarget origin/dev
actual="$(REPO_ROOT="$tmpdir" bash "$SCRIPT_UNDER_TEST" 2>/dev/null)"
assert_eq "git config avernet.prePush.mergeTarget resolves" "origin/dev" "$actual"
git config --worktree --unset avernet.prePush.mergeTarget 2>/dev/null || true
cd "$ORIG_PWD" && rm -rf "$tmpdir"

# --- Test 4: Tracking branch resolution for dev (default) ---
echo ""
echo "Test: Tracking branch resolution with dev"
tmpdir="$(mktemp -d)"
setup_repo "$tmpdir"
git checkout -b cb-dev-test-feature origin/dev >/dev/null 2>&1
actual="$(REPO_ROOT="$tmpdir" bash "$SCRIPT_UNDER_TEST" 2>/dev/null)"
assert_eq "tracking branch resolves to origin/dev" "origin/dev" "$actual"
cd "$ORIG_PWD" && rm -rf "$tmpdir"

# --- Test 5: Tracking branch resolution for non-dev target branch ---
echo ""
echo "Test: Tracking branch resolution for non-dev target (release branch)"
tmpdir="$(mktemp -d)"
setup_repo "$tmpdir"
git branch release/2026-07 HEAD >/dev/null 2>&1
git push origin release/2026-07 >/dev/null 2>&1
git checkout -b cb-dev-test-release origin/release/2026-07 >/dev/null 2>&1
actual="$(REPO_ROOT="$tmpdir" bash "$SCRIPT_UNDER_TEST" 2>/dev/null)"
assert_eq "tracking branch resolves to origin/release/2026-07" "origin/release/2026-07" "$actual"
cd "$ORIG_PWD" && rm -rf "$tmpdir"

# --- Test 6: Default fallback to origin/dev when no tracking branch ---
echo ""
echo "Test: Default fallback to origin/dev when no tracking branch"
tmpdir="$(mktemp -d)"
setup_repo "$tmpdir"
git checkout -b cb-dev-test-no-tracking >/dev/null 2>&1
# Unset any tracking — this branch has no upstream
actual="$(REPO_ROOT="$tmpdir" bash "$SCRIPT_UNDER_TEST" 2>/dev/null)"
assert_eq "fallback to origin/dev" "origin/dev" "$actual"
cd "$ORIG_PWD" && rm -rf "$tmpdir"

# --- Test 7: Failure when base ref cannot be resolved ---
echo ""
echo "Test: Failure when base ref cannot be resolved"
tmpdir="$(mktemp -d)"
setup_repo "$tmpdir"
set +e
output="$(AVERNET_BASE_REF=origin/nonexistent REPO_ROOT="$tmpdir" bash "$SCRIPT_UNDER_TEST" 2>&1)"
exit_code=$?
set -e
assert_exit_code "non-resolvable base ref exits non-zero" 1 "$exit_code"
assert_contains "error message mentions the ref" "origin/nonexistent" "$output"
assert_contains "error message includes AVERNET_BASE_REF hint" "AVERNET_BASE_REF=" "$output"
assert_contains "error message includes git config hint" "avernet.prePush.mergeTarget" "$output"
cd "$ORIG_PWD" && rm -rf "$tmpdir"

# --- Test 8: AVERNET_BASE_REF with non-dev branch ---
echo ""
echo "Test: AVERNET_BASE_REF with non-dev branch"
tmpdir="$(mktemp -d)"
setup_repo "$tmpdir"
git branch release/2026-07 HEAD >/dev/null 2>&1
git push origin release/2026-07 >/dev/null 2>&1
actual="$(AVERNET_BASE_REF=origin/release/2026-07 REPO_ROOT="$tmpdir" bash "$SCRIPT_UNDER_TEST" 2>/dev/null)"
assert_eq "AVERNET_BASE_REF with release branch" "origin/release/2026-07" "$actual"
cd "$ORIG_PWD" && rm -rf "$tmpdir"

# Summary
echo ""
echo "=== Summary ==="
echo "  Passed: $pass"
echo "  Failed: $fail"
if [[ "$fail" -gt 0 ]]; then
  exit 1
fi
echo "All tests passed."