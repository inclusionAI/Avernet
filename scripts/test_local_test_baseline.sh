#!/usr/bin/env bash
# Automated tests for scripts/ci/resolve_test_baseline.sh and the local_test.sh
# dispatcher. Uses temporary git repositories to exercise baseline resolution
# for dev->dev, non-dev target branches, explicit overrides, fail-closed
# behavior, and --no-cov argument forwarding.
#
# Run:  bash scripts/test_local_test_baseline.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RESOLVER="$REPO_ROOT/scripts/ci/resolve_test_baseline.sh"
LOCAL_TEST="$REPO_ROOT/scripts/ci/local_test.sh"

passed=0
failed=0
global_tmp=""

cleanup() {
  if [[ -n "$global_tmp" && -d "$global_tmp" ]]; then
    rm -rf "$global_tmp"
  fi
}
trap cleanup EXIT

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  failed=$((failed + 1))
}

pass() {
  printf 'PASS: %s\n' "$1"
  passed=$((passed + 1))
}

# Set up an isolated HOME and git env so tests don't touch the real user config.
setup_isolated_env() {
  local tmp="$1"
  export HOME="$tmp/home"
  export GIT_CONFIG_GLOBAL=/dev/null
  export GIT_CONFIG_NOSYSTEM=1
  mkdir -p "$HOME"
}

# Create a bare remote + a developer clone. The repo is shaped so the
# `origin/release-2026-07` branch has its own ancestry NOT shared with the
# current `origin/dev` tip:
#
#   A (initial, shared)
#     \
#      dev:        A -> D1 (dev tip)
#      release:    A -> R1 (release tip, R1 NOT in dev)
#      feature-x:  based on dev tip (D1), add F1 -> HEAD
#
# Therefore:
#   merge-base(HEAD, origin/dev)            = D1  (dev tip)
#   merge-base(HEAD, origin/release-2026-07)= A   (initial commit)
# These two baselines differ, which is what the silent-fallback test asserts.
make_fake_repo() {
  local tmp="$1"
  local remote="$tmp/remote.git"
  local dev="$tmp/dev"

  mkdir -p "$remote" "$dev"
  git init --bare -q "$remote" >/dev/null

  git init -q --initial-branch=dev "$dev" >/dev/null
  git -C "$dev" config user.name "CI Test"
  git -C "$dev" config user.email "ci-test@example.com"
  git -C "$dev" config commit.gpgsign false

  # Commit A (shared ancestor) on dev.
  mkdir -p "$dev/src/backend"
  echo "print('hi')" > "$dev/src/backend/a.py"
  git -C "$dev" add .
  git -C "$dev" commit -q -m "A: shared ancestor"
  git -C "$dev" remote add origin "$remote"

  # Branch release-2026-07 off A, add commit R1 (independent ancestry).
  git -C "$dev" checkout -q -b release-2026-07
  echo "print('release')" > "$dev/src/backend/release_only.py"
  git -C "$dev" add .
  git -C "$dev" commit -q -m "R1: release-only change"
  git -C "$dev" push -q -u origin release-2026-07

  # Back to dev, advance it with D1 (so dev tip != A).
  git -C "$dev" checkout -q dev
  echo "dev-only = 1" >> "$dev/src/backend/a.py"
  git -C "$dev" add .
  git -C "$dev" commit -q -m "D1: dev-only advance"
  git -C "$dev" push -q -u origin dev

  # feature-x off dev tip (D1), add F1 -> HEAD.
  git -C "$dev" checkout -q -b feature-x
  echo "x = 1" >> "$dev/src/backend/a.py"
  git -C "$dev" add .
  git -C "$dev" commit -q -m "F1: feature change"

  printf '%s\n' "$dev"
}

# Copy the resolver into a fake repo so local_test.sh's with-cov path can find
# it. The fake repo's scripts/ci tree is created if needed.
install_resolver() {
  local dev="$1"
  mkdir -p "$dev/scripts/ci"
  cp "$RESOLVER" "$dev/scripts/ci/resolve_test_baseline.sh"
  chmod +x "$dev/scripts/ci/resolve_test_baseline.sh"
}

# Resolve baseline in a given repo, capturing stdout/stderr and exit code.
run_resolver() {
  local repo="$1"; shift
  local out err rc
  out_file="$(mktemp)"
  err_file="$(mktemp)"
  set +e
  ( cd "$repo" && "$RESOLVER" "$@" ) >"$out_file" 2>"$err_file"
  rc=$?
  set -e
  printf '%s|' "$rc"
  cat "$out_file"
  printf '|'
  cat "$err_file"
  rm -f "$out_file" "$err_file"
}

test_dev_default_baseline() {
  local tmp=$(mktemp -d)
  local dev
  dev="$(make_fake_repo "$tmp")" || { fail "dev->dev: make_fake_repo"; rm -rf "$tmp"; return; }

  # origin/dev exists and is an ancestor of HEAD feature-x.
  local result
  result="$(cd "$dev" && "$RESOLVER")" || {
    fail "dev->dev: resolver exit non-zero on origin/dev default"
    rm -rf "$tmp"; return; }

  # The baseline must be origin/dev's tip (HEAD of dev branch).
  local dev_tip
  dev_tip="$(git -C "$dev" rev-parse origin/dev)"
  if [[ "$result" == "$dev_tip" ]]; then
    pass "dev->dev: default origin/dev baseline resolves to dev tip"
  else
    fail "dev->dev: expected $dev_tip got $result"
  fi
  rm -rf "$tmp"
}

test_non_dev_target_branch() {
  local tmp=$(mktemp -d)
  local dev
  dev="$(make_fake_repo "$tmp")" || { fail "non-dev: make_fake_repo"; rm -rf "$tmp"; return; }

  # Configure a non-dev target via git config.
  git -C "$dev" config avernet.test.mergeTarget origin/release-2026-07

  local result
  result="$(cd "$dev" && "$RESOLVER")" || {
    fail "non-dev: resolver exit non-zero for origin/release-2026-07"
    rm -rf "$tmp"; return; }

  local release_tip release_base expected
  release_tip="$(git -C "$dev" rev-parse origin/release-2026-07)"
  expected="$(git -C "$dev" merge-base HEAD "$release_tip")"
  if [[ "$result" == "$expected" ]]; then
    pass "non-dev: avernet.test.mergeTarget=origin/release-2026-07 resolves to release merge-base"
  else
    fail "non-dev: expected $expected got $result"
  fi

  # Make sure it is NOT the dev tip (would indicate silent fallback to origin/dev).
  local dev_tip
  dev_tip="$(git -C "$dev" rev-parse origin/dev)"
  if [[ "$result" != "$dev_tip" ]]; then
    pass "non-dev: baseline differs from origin/dev tip (no silent fallback)"
  else
    fail "non-dev: baseline equals origin/dev tip — silent fallback detected"
  fi
  rm -rf "$tmp"
}

test_env_override_priority() {
  local tmp=$(mktemp -d)
  local dev
  dev="$(make_fake_repo "$tmp")" || { fail "env-override: make_fake_repo"; rm -rf "$tmp"; return; }

  # Set a git config target AND an env override pointing at the release branch.
  git -C "$dev" config avernet.test.mergeTarget origin/dev
  local result
  result="$(cd "$dev" && AVERNET_TEST_BASE_REF=origin/release-2026-07 "$RESOLVER")" || {
    fail "env-override: resolver exit non-zero"
    rm -rf "$tmp"; return; }

  local release_tip expected
  release_tip="$(git -C "$dev" rev-parse origin/release-2026-07)"
  expected="$(git -C "$dev" merge-base HEAD "$release_tip")"
  if [[ "$result" == "$expected" ]]; then
    pass "env-override: AVERNET_TEST_BASE_REF wins over avernet.test.mergeTarget"
  else
    fail "env-override: expected $expected got $result"
  fi
  rm -rf "$tmp"
}

test_cli_base_override() {
  local tmp=$(mktemp -d)
  local dev
  dev="$(make_fake_repo "$tmp")" || { fail "cli-base: make_fake_repo"; rm -rf "$tmp"; return; }

  # --base with a bare commit SHA should resolve directly.
  local release_tip expected result
  release_tip="$(git -C "$dev" rev-parse origin/release-2026-07)"
  expected="$(git -C "$dev" merge-base HEAD "$release_tip")"

  result="$(cd "$dev" && "$RESOLVER" --base "$release_tip")" || {
    fail "cli-base: resolver exit non-zero"
    rm -rf "$tmp"; return; }

  if [[ "$result" == "$expected" ]]; then
    pass "cli-base: --base <sha> resolves to merge-base"
  else
    fail "cli-base: expected $expected got $result"
  fi
  rm -rf "$tmp"
}

test_fail_closed_on_bad_target() {
  local tmp=$(mktemp -d)
  local dev
  dev="$(make_fake_repo "$tmp")" || { fail "fail-closed: make_fake_repo"; rm -rf "$tmp"; return; }

  # Point at a remote that doesn't exist locally; resolver must fail non-zero
  # and the diagnostic must mention an actionable hint.
  git -C "$dev" config avernet.test.mergeTarget upstream/no-such-branch
  local out err rc
  set +e
  ( cd "$dev" && "$RESOLVER" ) >"$tmp/out" 2>"$tmp/err"
  rc=$?
  set -e
  if [[ "$rc" -ne 0 ]]; then
    pass "fail-closed: non-zero exit when target remote is unknown"
  else
    fail "fail-closed: expected non-zero exit, got 0"
  fi
  err="$(cat "$tmp/err")"
  if printf '%s' "$err" | grep -q 'avernet.test.mergeTarget'; then
    pass "fail-closed: diagnostic references avernet.test.mergeTarget"
  else
    fail "fail-closed: diagnostic missing actionable hint"
  fi
  rm -rf "$tmp"
}

test_fail_closed_on_no_merge_base() {
  local tmp=$(mktemp -d)
  local dev
  dev="$(make_fake_repo "$tmp")" || { fail "no-merge-base: make_fake_repo"; rm -rf "$tmp"; return; }

  # Create an orphan commit without ancestry sharing, point --base at it.
  local orphan
  orphan="$(git -C "$dev" commit-tree -m "orphan" "$(git -C "$dev" write-tree)")" || {
    fail "no-merge-base: could not create orphan commit"; rm -rf "$tmp"; return; }

  local rc
  set +e
  ( cd "$dev" && "$RESOLVER" --base "$orphan" ) >/dev/null 2>"$tmp/err"
  rc=$?
  set -e
  if [[ "$rc" -ne 0 ]]; then
    pass "no-merge-base: non-zero exit when target has no merge-base with HEAD"
  else
    fail "no-merge-base: expected non-zero exit, got 0"
  fi
  rm -rf "$tmp"
}

test_upstream_tracking_branch() {
  local tmp=$(mktemp -d)
  local dev
  dev="$(make_fake_repo "$tmp")" || { fail "upstream: make_fake_repo"; rm -rf "$tmp"; return; }

  # feature-x has no upstream yet. Set its upstream to origin/release-2026-07.
  git -C "$dev" checkout -q feature-x
  git -C "$dev" branch --set-upstream-to=origin/release-2026-07 feature-x >/dev/null 2>&1

  local result
  result="$(cd "$dev" && "$RESOLVER")" || {
    fail "upstream: resolver exit non-zero"
    rm -rf "$tmp"; return; }

  local release_tip expected
  release_tip="$(git -C "$dev" rev-parse origin/release-2026-07)"
  expected="$(git -C "$dev" merge-base HEAD "$release_tip")"
  if [[ "$result" == "$expected" ]]; then
    pass "upstream: baseline resolves from upstream tracking branch (origin/release-2026-07)"
  else
    fail "upstream: expected $expected got $result"
  fi
  rm -rf "$tmp"
}

test_no_cov_forwards_no_base() {
  local tmp=$(mktemp -d)
  local dev
  dev="$(make_fake_repo "$tmp")" || { fail "no-cov: make_fake_repo"; rm -rf "$tmp"; return; }

  # Install a stub backend ci_test.sh that records its arguments.
  mkdir -p "$dev/src/backend/scripts"
  cat > "$dev/src/backend/scripts/ci_test.sh" <<'SH'
#!/usr/bin/env bash
echo "stub-ci_test-args:$*" >> "${LOCAL_TEST_STUB_LOG:?}"
exit 0
SH
  chmod +x "$dev/src/backend/scripts/ci_test.sh"

  export LOCAL_TEST_STUB_LOG="$tmp/stub.log"
  rm -f "$LOCAL_TEST_STUB_LOG"

  # Run local_test.sh --no-cov; it must NOT pass --base to the stub.
  ( cd "$dev" && bash "$LOCAL_TEST" --no-cov ) >/dev/null 2>"$tmp/lt_err" || {
    fail "no-cov: local_test.sh --no-cov exited non-zero"
    cat "$tmp/lt_err" >&2
    rm -rf "$tmp"; return; }

  if [[ ! -s "$LOCAL_TEST_STUB_LOG" ]]; then
    fail "no-cov: stub ci_test.sh was not invoked"
    rm -rf "$tmp"; return
  fi
  local log
  log="$(cat "$LOCAL_TEST_STUB_LOG")"
  if printf '%s' "$log" | grep -q -- '--base'; then
    fail "no-cov: --base was forwarded to ci_test.sh under --no-cov: $log"
  else
    pass "no-cov: ci_test.sh invoked without --base under --no-cov"
  fi
  rm -rf "$tmp"
}

test_with_cov_forwards_base() {
  local tmp=$(mktemp -d)
  local dev
  dev="$(make_fake_repo "$tmp")" || { fail "with-cov: make_fake_repo"; rm -rf "$tmp"; return; }
  install_resolver "$dev"

  mkdir -p "$dev/src/backend/scripts"
  cat > "$dev/src/backend/scripts/ci_test.sh" <<'SH'
#!/usr/bin/env bash
echo "stub-ci_test-args:$*" >> "${LOCAL_TEST_STUB_LOG:?}"
exit 0
SH
  chmod +x "$dev/src/backend/scripts/ci_test.sh"

  export LOCAL_TEST_STUB_LOG="$tmp/stub.log"
  rm -f "$LOCAL_TEST_STUB_LOG"

  # Run local_test.sh with coverage gate (default). Baseline resolves to
  # origin/dev, then --base <sha> --head HEAD must be forwarded.
  ( cd "$dev" && bash "$LOCAL_TEST" ) >/dev/null 2>"$tmp/lt_err" || {
    fail "with-cov: local_test.sh exited non-zero"
    cat "$tmp/lt_err" >&2
    rm -rf "$tmp"; return; }

  local log
  log="$(cat "$LOCAL_TEST_STUB_LOG")"
  if printf '%s' "$log" | grep -q -- '--base'; then
    pass "with-cov: ci_test.sh invoked with --base"
  else
    fail "with-cov: --base not forwarded to ci_test.sh: $log"
  fi
  rm -rf "$tmp"
}

test_with_cov_non_dev_baseline_forwarded() {
  local tmp=$(mktemp -d)
  local dev
  dev="$(make_fake_repo "$tmp")" || { fail "with-cov-non-dev: make_fake_repo"; rm -rf "$tmp"; return; }
  install_resolver "$dev"
  git -C "$dev" config avernet.test.mergeTarget origin/release-2026-07

  mkdir -p "$dev/src/backend/scripts"
  cat > "$dev/src/backend/scripts/ci_test.sh" <<'SH'
#!/usr/bin/env bash
echo "stub-ci_test-args:$*" >> "${LOCAL_TEST_STUB_LOG:?}"
exit 0
SH
  chmod +x "$dev/src/backend/scripts/ci_test.sh"

  export LOCAL_TEST_STUB_LOG="$tmp/stub.log"
  rm -f "$LOCAL_TEST_STUB_LOG"

  ( cd "$dev" && bash "$LOCAL_TEST" ) >/dev/null 2>"$tmp/lt_err" || {
    fail "with-cov-non-dev: local_test.sh exited non-zero"
    cat "$tmp/lt_err" >&2
    rm -rf "$tmp"; return; }

  local log expected_initial dev_tip
  log="$(cat "$LOCAL_TEST_STUB_LOG")"
  # Initial commit A is the merge-base of HEAD and origin/release-2026-07.
  expected_initial="$(git -C "$dev" rev-list --max-parents=0 HEAD | tail -1)"
  dev_tip="$(git -C "$dev" rev-parse origin/dev)"
  if printf '%s' "$log" | grep -q -- "--base $expected_initial"; then
    pass "with-cov-non-dev: ci_test.sh got the release merge-base as --base"
  else
    fail "with-cov-non-dev: expected --base $expected_initial in: $log"
  fi
  if ! printf '%s' "$log" | grep -q -- "--base $dev_tip"; then
    pass "with-cov-non-dev: ci_test.sh did NOT receive the dev tip as --base"
  else
    fail "with-cov-non-dev: ci_test.sh incorrectly received dev tip as --base"
  fi
  rm -rf "$tmp"
}

test_empty_change_skips_gate() {
  local tmp=$(mktemp -d)
  local dev
  dev="$(make_fake_repo "$tmp")" || { fail "empty-change: make_fake_repo"; rm -rf "$tmp"; return; }
  install_resolver "$dev"

  # Move HEAD to dev tip (no changes vs origin/dev).
  git -C "$dev" checkout -q dev
  local out rc
  set +e
  out="$(cd "$dev" && bash "$LOCAL_TEST" 2>&1)"
  rc=$?
  set -e
  if [[ "$rc" -eq 0 ]]; then
    pass "empty-change: local_test.sh exits 0 when no changes vs baseline"
  else
    fail "empty-change: expected exit 0, got $rc"
  fi
  if printf '%s' "$out" | grep -q 'coverage gate skipped\|nothing to gate'; then
    pass "empty-change: banner explains gate was skipped"
  else
    fail "empty-change: missing skip banner"
  fi
  rm -rf "$tmp"
}

main() {
  if [[ ! -x "$RESOLVER" ]]; then
    echo "error: resolver not executable: $RESOLVER" >&2
    exit 1
  fi
  if [[ ! -f "$LOCAL_TEST" ]]; then
    echo "error: missing local_test.sh: $LOCAL_TEST" >&2
    exit 1
  fi

  global_tmp="$(mktemp -d)"
  setup_isolated_env "$global_tmp"

  test_dev_default_baseline
  test_non_dev_target_branch
  test_env_override_priority
  test_cli_base_override
  test_fail_closed_on_bad_target
  test_fail_closed_on_no_merge_base
  test_upstream_tracking_branch
  test_no_cov_forwards_no_base
  test_with_cov_forwards_base
  test_with_cov_non_dev_baseline_forwarded
  test_empty_change_skips_gate

  echo ""
  echo "summary: $passed passed, $failed failed"
  if [[ "$failed" -ne 0 ]]; then
    exit 1
  fi
}

main "$@"