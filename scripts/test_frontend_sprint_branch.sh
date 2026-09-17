#!/usr/bin/env bash
# scripts/test_frontend_sprint_branch.sh — frontend_sprint_branch tool tests
#
# Offline by construction: the remote is the TEAMCLAW_DIR stand-in's own
# file-protocol origin (a throwaway repo), so every probe carries
# GIT_ALLOW_PROTOCOL=file uniformly (same uniform-env pattern as ocb's
# frontend_sprint_branch harness, keeping the fixtures one shape).
# FRONTEND_SPRINT_CONFIG points the state file into the throwaway temp dir,
# so no probe ever touches ~/.config/teamclaw-frontend.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOL="${ROOT}/scripts/frontend_sprint_branch.sh"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

assert_eq() {
  local expected="$1"
  local actual="$2"
  local label="$3"
  [ "$expected" = "$actual" ] || fail "${label}: expected '${expected}', got '${actual}'"
}

assert_contains() {
  local haystack="$1"
  local needle="$2"
  local label="$3"
  case "$haystack" in
    *"$needle"*) ;;
    *) fail "${label}: missing '${needle}' in: ${haystack}" ;;
  esac
}

assert_not_contains() {
  local haystack="$1"
  local needle="$2"
  local label="$3"
  case "$haystack" in
    *"$needle"*) fail "${label}: unexpected '${needle}' in: ${haystack}" ;;
  esac
}

TEMP="$(mktemp -d)"
trap 'chmod -R u+w "$TEMP" 2>/dev/null || true; rm -rf "$TEMP"' EXIT

# Run the tool against a fixture pair. rc lands in PROBE_RC; stdout in
# PROBE_OUT; stderr in PROBE_ERR ("$@" are the tool's own arguments). TC and
# CFG select the TEAMCLAW_DIR stand-in and the state-file override.
TC=""
CFG=""
PROBE_RC=0
PROBE_OUT=""
PROBE_ERR=""
# Ambient-env hygiene: an exported FRONTEND_SPRINT_BRANCH from the caller's
# shell would shadow the state file in every probe; strip it once here so
# tests that set it deliberately (env-override case) are the only source.
unset FRONTEND_SPRINT_BRANCH
run_tool() {
  : >"${TEMP}/probe-out"
  : >"${TEMP}/probe-err"
  PROBE_RC=0
  env GIT_ALLOW_PROTOCOL=file \
    TEAMCLAW_DIR="${TC:?run_tool requires a TEAMCLAW_DIR fixture}" \
    FRONTEND_SPRINT_CONFIG="${CFG:?run_tool requires a FRONTEND_SPRINT_CONFIG fixture}" \
    bash "${TOOL}" "$@" >"${TEMP}/probe-out" 2>"${TEMP}/probe-err" || PROBE_RC=$?
  PROBE_OUT="$(cat "${TEMP}/probe-out")"
  PROBE_ERR="$(cat "${TEMP}/probe-err")"
}

recorded_branch() {
  [ -f "${CFG}" ] || return 1
  head -n 1 "${CFG}"
}

config_sha() {
  [ -f "${CFG}" ] || { printf 'absent'; return 0; }
  git hash-object "${CFG}"
}

# ---------------------------------------------------------------------------
# Fixtures: a fake teamclaw remote owning sprint + non-sprint heads, and a
# stand-in checkout (TEAMCLAW_DIR) whose origin points at it over the file
# protocol. The tool derives its remote from the checkout's own origin.
# ---------------------------------------------------------------------------
setup_remote() {
  local remote="$1"
  git init -q -b main "${remote}"
  git -C "${remote}" config user.email t@t
  git -C "${remote}" config user.name t
  printf 'base one\n' >"${remote}/src.txt"
  git -C "${remote}" add .
  git -C "${remote}" commit -qm "base commit"
  git -C "${remote}" branch sprint_teamclaw_OLD
  git -C "${remote}" branch sprint_teamclaw_NEW
  git -C "${remote}" branch unrelated
}

setup_tc() {
  local remote="$1"
  local tc="$2"
  git init -q -b main "${tc}"
  git -C "${tc}" config user.email t@t
  git -C "${tc}" config user.name t
  git -C "${tc}" remote add origin "file://${remote}"
}

new_fixture() {
  TC="${TEMP}/tc-$1"
  CFG="${TEMP}/config-$1/branch"
  mkdir -p "${TEMP}/config-$1"
  setup_remote "${TEMP}/remote-$1.git" "${TC}"
  setup_tc "${TEMP}/remote-$1.git" "${TC}"
}

# ---------------------------------------------------------------------------
# --list: declared branch (env > file) + remote sprint heads, sprint-filtered
# ---------------------------------------------------------------------------
test_list_prints_declared_and_sprint_heads() {
  new_fixture list
  # With no record yet: declared prints <none>, not an empty value.
  run_tool --list
  [ "${PROBE_RC}" -eq 0 ] || fail "--list must be rc 0 with no record, got ${PROBE_RC}: ${PROBE_ERR}"
  assert_contains "${PROBE_OUT}" "declared: <none>" "no-record declared line"
  printf 'sprint_teamclaw_OLD\n' >"${CFG}"
  run_tool --list
  [ "${PROBE_RC}" -eq 0 ] || fail "--list must be rc 0, got ${PROBE_RC}: ${PROBE_ERR}"
  assert_contains "${PROBE_OUT}" "declared: sprint_teamclaw_OLD" "--list declared line"
  assert_contains "${PROBE_OUT}" "config: ${CFG}" "--list names the state file"
  assert_contains "${PROBE_OUT}" "sprint_teamclaw_NEW" "--list shows new sprint head"
  assert_contains "${PROBE_OUT}" "sprint_teamclaw_OLD" "--list shows old sprint head"
  assert_not_contains "${PROBE_OUT}" "unrelated" "--list must not show non-sprint heads"
  assert_not_contains "${PROBE_OUT}" "refs/heads/" "--list must strip the refs/heads/ prefix"
  assert_not_contains "${PROBE_OUT}" "src.txt" "--list must not show the base branch (worktree name)"
}

test_list_declared_env_overrides_file() {
  new_fixture envover
  printf 'sprint_teamclaw_OLD\n' >"${CFG}"
  export FRONTEND_SPRINT_BRANCH=sprint_teamclaw_NEW
  run_tool --list
  unset FRONTEND_SPRINT_BRANCH
  [ "${PROBE_RC}" -eq 0 ] || fail "--list must be rc 0, got ${PROBE_RC}: ${PROBE_ERR}"
  assert_contains "${PROBE_OUT}" "declared: sprint_teamclaw_NEW" "env FRONTEND_SPRINT_BRANCH overrides the file"
  assert_eq "sprint_teamclaw_OLD" "$(recorded_branch)" "an env override must not rewrite the file"
}

# ---------------------------------------------------------------------------
# no argument → rc 2 usage error, no state file written
# ---------------------------------------------------------------------------
test_no_argument_is_usage_error() {
  new_fixture noarg
  run_tool
  [ "${PROBE_RC}" -eq 2 ] || fail "no-arg must be rc 2, got ${PROBE_RC}"
  assert_contains "${PROBE_ERR}" "usage" "no-arg must print usage"
  [ ! -f "${CFG}" ] || fail "no-arg must not create the state file"
}

# ---------------------------------------------------------------------------
# unknown branch → rc 1, state untouched (validation happens before any write)
# ---------------------------------------------------------------------------
test_unknown_branch_refused_no_write() {
  new_fixture unknown
  printf 'sprint_teamclaw_OLD\n' >"${CFG}"
  run_tool sprint_teamclaw_DOES_NOT_EXIST
  [ "${PROBE_RC}" -eq 1 ] || fail "unknown branch must be rc 1, got ${PROBE_RC}"
  assert_contains "${PROBE_ERR}" "branch not found on remote: sprint_teamclaw_DOES_NOT_EXIST" \
    "unknown-branch refusal wording"
  assert_contains "${PROBE_ERR}" "--list" "refusal must point at --list"
  assert_eq "sprint_teamclaw_OLD" "$(recorded_branch)" "refusal must leave the recorded branch"
}

# Anchored validation: a prefix of an existing head, and wildcard/glob names
# that ls-remote would happily expand, must both be refused — the recorded
# branch must be a branch that exists exactly, not a pattern that matches.
test_prefix_and_wildcard_names_do_not_validate() {
  new_fixture anchored
  local before
  before="$(config_sha)"
  run_tool sprint_teamclaw_OL
  [ "${PROBE_RC}" -eq 1 ] || fail "prefix of an existing head must be rc 1, got ${PROBE_RC}"
  assert_contains "${PROBE_ERR}" "branch not found on remote: sprint_teamclaw_OL" \
    "prefix refusal wording"
  run_tool 'sprint_teamclaw_*'
  [ "${PROBE_RC}" -eq 1 ] || fail "wildcard name must be rc 1, got ${PROBE_RC}"
  assert_contains "${PROBE_ERR}" "branch not found on remote: sprint_teamclaw_*" \
    "wildcard refusal wording"
  run_tool 'mai*'
  [ "${PROBE_RC}" -eq 1 ] || fail "wildcard over the base branch must be rc 1, got ${PROBE_RC}"
  assert_eq "${before}" "$(config_sha)" "anchored refusals must not write the state file"
}

# ---------------------------------------------------------------------------
# valid branch → recorded in the config file, advice for the follow-up steps
# ---------------------------------------------------------------------------
test_valid_branch_records_state_and_advises() {
  new_fixture switch
  run_tool sprint_teamclaw_NEW
  [ "${PROBE_RC}" -eq 0 ] || fail "valid branch must be rc 0, got ${PROBE_RC}: ${PROBE_ERR}"
  assert_eq "sprint_teamclaw_NEW" "$(recorded_branch)" "recorded branch after switch"
  assert_contains "${PROBE_OUT}" "recorded sprint branch sprint_teamclaw_NEW" "switch confirmation"
  assert_contains "${PROBE_OUT}" "git -C \"${TC}\" fetch && git -C \"${TC}\" checkout sprint_teamclaw_NEW" \
    "follow-up advice must name the real TEAMCLAW_DIR and branch"
  assert_contains "${PROBE_OUT}" "frontend-pull" "advice must point at frontend-pull"
}

test_valid_branch_notes_env_shadow() {
  new_fixture shadow
  export FRONTEND_SPRINT_BRANCH=sprint_teamclaw_OLD
  run_tool sprint_teamclaw_NEW
  unset FRONTEND_SPRINT_BRANCH
  [ "${PROBE_RC}" -eq 0 ] || fail "valid branch must be rc 0 despite env shadow, got ${PROBE_RC}: ${PROBE_ERR}"
  assert_eq "sprint_teamclaw_NEW" "$(recorded_branch)" "the file record is updated regardless"
  assert_contains "${PROBE_OUT}" "FRONTEND_SPRINT_BRANCH=sprint_teamclaw_OLD" \
    "switch must note the env override that keeps winning until unset"
}

# ---------------------------------------------------------------------------
# unreachable remote → rc 1, no write (validation happens before any write)
# ---------------------------------------------------------------------------
test_unreachable_remote_refused_no_write() {
  new_fixture dead
  git -C "${TC}" remote set-url origin "file://${TEMP}/not-a-repo.git"
  printf 'sprint_teamclaw_OLD\n' >"${CFG}"
  local before
  before="$(config_sha)"
  run_tool sprint_teamclaw_NEW
  [ "${PROBE_RC}" -eq 1 ] || fail "unreachable remote must be rc 1, got ${PROBE_RC}"
  assert_contains "${PROBE_ERR}" "cannot reach" "unreachable-refusal wording"
  assert_eq "${before}" "$(config_sha)" "unreachable remote must not write the state file"
  run_tool --list
  [ "${PROBE_RC}" -eq 1 ] || fail "--list must be rc 1 on an unreachable remote, got ${PROBE_RC}"
  assert_contains "${PROBE_ERR}" "cannot reach" "--list unreachable wording"
}

# ---------------------------------------------------------------------------
# -h/--help and argument arity: usage exits 0 without touching the remote;
# --list-with-extras and 2+ args are named errors, not silent ignores
# ---------------------------------------------------------------------------
test_help_and_argument_errors() {
  new_fixture args
  printf 'sprint_teamclaw_OLD\n' >"${CFG}"
  local before
  before="$(config_sha)"
  run_tool --help
  [ "${PROBE_RC}" -eq 0 ] || fail "--help must be rc 0, got ${PROBE_RC}"
  assert_contains "${PROBE_OUT}" "usage:" "--help prints usage"
  run_tool -h
  [ "${PROBE_RC}" -eq 0 ] || fail "-h must be rc 0, got ${PROBE_RC}"
  assert_contains "${PROBE_OUT}" "usage:" "-h prints usage"
  run_tool --list extra-arg
  [ "${PROBE_RC}" -eq 2 ] || fail "--list with extra args must be rc 2, got ${PROBE_RC}"
  assert_contains "${PROBE_ERR}" "--list takes no additional argument(s)" \
    "--list extra-arg error names the problem"
  assert_contains "${PROBE_ERR}" "extra-arg" "--list extra-arg error names the stray argument"
  run_tool sprint_teamclaw_NEW stray-arg
  [ "${PROBE_RC}" -eq 2 ] || fail "2+ args must be rc 2, got ${PROBE_RC}"
  assert_contains "${PROBE_ERR}" "unexpected argument(s)" "2+ args error names the problem"
  assert_contains "${PROBE_ERR}" "stray-arg" "2+ args error names the stray argument"
  assert_eq "${before}" "$(config_sha)" "argument errors must not write the state file"
}

test_list_prints_declared_and_sprint_heads
test_list_declared_env_overrides_file
test_no_argument_is_usage_error
test_help_and_argument_errors
test_unknown_branch_refused_no_write
test_prefix_and_wildcard_names_do_not_validate
test_valid_branch_records_state_and_advises
test_valid_branch_notes_env_shadow
# ---------------------------------------------------------------------------
# Boundary fix: no baked-in remote URL — with no resolvable TEAMCLaw checkout
# the tool hard-refuses instead of falling back to a spelled-out internal URL.
# ---------------------------------------------------------------------------
test_no_checkout_refuses_without_url_fallback() {
  PROBE_RC=0
  env -u TEAMCLAW_DIR -u FRONTEND_SPRINT_BRANCH \
    GIT_ALLOW_PROTOCOL=file \
    FRONTEND_SPRINT_CONFIG="${CFG}" \
    bash "${TOOL}" --list >"${TEMP}/probe-out" 2>"${TEMP}/probe-err" || PROBE_RC=$?
  PROBE_OUT="$(cat "${TEMP}/probe-out")"
  PROBE_ERR="$(cat "${TEMP}/probe-err")"
  [ "${PROBE_RC}" -eq 1 ] || fail "no-checkout run must be rc 1, got ${PROBE_RC}"
  assert_contains "${PROBE_ERR}" "no teamclaw checkout to resolve the remote from" \
    "no-checkout refusal wording"
  assert_contains "${PROBE_ERR}" "no fallback URL is baked in" \
    "refusal must state there is no baked-in URL"
  assert_contains "${PROBE_ERR}" "TEAMCLAW_DIR" "refusal must name the escape hatch"
  # Non-repo checkout gets the same refusal (not a git error dump).
  PROBE_RC=0
  env -u FRONTEND_SPRINT_BRANCH \
    GIT_ALLOW_PROTOCOL=file \
    TEAMCLAW_DIR="${TEMP}" \
    FRONTEND_SPRINT_CONFIG="${CFG}" \
    bash "${TOOL}" --list >"${TEMP}/probe-out" 2>"${TEMP}/probe-err" || PROBE_RC=$?
  PROBE_ERR="$(cat "${TEMP}/probe-err")"
  [ "${PROBE_RC}" -eq 1 ] || fail "non-repo checkout must be rc 1, got ${PROBE_RC}"
  assert_contains "${PROBE_ERR}" "no teamclaw checkout to resolve the remote from" \
    "non-repo refusal wording"
}

test_unreachable_remote_refused_no_write
test_no_checkout_refuses_without_url_fallback

echo "frontend_sprint_branch: all tests passed"
