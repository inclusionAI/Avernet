#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMP="$(mktemp -d)"
trap 'rm -rf "$TEMP"' EXIT
export PROJECT_ROOT="$ROOT" LOG_DIR="$TEMP" DEP_DIR="$TEMP"
source "$ROOT/scripts/modules/frontend.sh"
FRONTEND_VARIANT=legacy
frontend_select_variant
[[ "$FRONTEND_DIR" == "$ROOT/src/frontend" && "$FRONTEND_DEFAULT_SCRIPT" == devs:local:oss ]]
FRONTEND_VARIANT=nextgen
frontend_select_variant
[[ "$FRONTEND_DIR" == "$ROOT/src/frontend-nextgen" && "$FRONTEND_DEFAULT_SCRIPT" == dev:local ]]
[[ "$FRONTEND_ROOT_ID" == root ]]
FRONTEND_VARIANT=invalid
if frontend_select_variant 2>/dev/null; then echo 'Invalid variant accepted' >&2; exit 1; fi
FRONTEND_VARIANT=nextgen
frontend_select_variant
# Exercise readiness without starting a server: nextgen must reject legacy HTML
# and the Umi bundling placeholder, not merely accept HTTP 200.
curl() { if [[ "$*" == *'/umi.js'* ]]; then return 0; fi; printf '%s' "$HTML"; }
HTML='<div id="root"></div><script src="/umi.js"></script>'
frontend_http_ready
HTML='<div id="root-master"></div><script src="/umi.js"></script>'
if frontend_http_ready; then exit 1; fi
HTML='<div id="root"></div><script src="/umi.js"></script>Bundling'
if frontend_http_ready; then exit 1; fi
FRONTEND_VARIANT=legacy
frontend_select_variant
HTML='<div id="root-master"></div><script src="/umi.js"></script>'
frontend_http_ready
printf 'PASS: frontend variants and readiness\n'

FRONTEND_VARIANT=nextgen
GATEWAY_PORT=29999 BCS_PORT=29998
unset TEAMCLAW_GW_BASE TEAMCLAW_ADMIN_BASE TASK_ENGINE_UPSTREAM BCS_ENDPOINT_PRE BCS_ENDPOINT_PROD
frontend_configure_upstreams
[[ "$TEAMCLAW_GW_BASE" == http://127.0.0.1:29999 ]]
[[ "$TEAMCLAW_ADMIN_BASE" == "$TEAMCLAW_GW_BASE" && "$TASK_ENGINE_UPSTREAM" == "$TEAMCLAW_GW_BASE" ]]
[[ "$BCS_ENDPOINT_PRE" == http://127.0.0.1:29998 && "$BCS_ENDPOINT_PROD" == "$BCS_ENDPOINT_PRE" ]]
TEAMCLAW_GW_BASE=http://127.0.0.1:29997
frontend_configure_upstreams
[[ "$TEAMCLAW_GW_BASE" == http://127.0.0.1:29997 ]]
source "$ROOT/scripts/modules/all.sh"
[[ "${START_ORDER[*]}" == 'baas backend bcsfuse bcs bots demo_bot gateway frontend' ]]
[[ "${STOP_ORDER[*]}" == 'frontend gateway demo_bot bots bcsfuse bcs backend baas' ]]
printf 'PASS: nextgen upstream defaults, overrides and Gateway lifecycle order\n'

# ---------------------------------------------------------------------------
# teamclaw variant: an external frontend checkout (the internal product UI),
# wired through the same gateway composition as nextgen.
# ---------------------------------------------------------------------------
TC_HOME="$TEMP/tc"
mkdir -p "$TC_HOME"
FRONTEND_VARIANT=teamclaw
if FRONTEND_VARIANT=teamclaw frontend_select_variant 2>/dev/null; then
    echo 'teamclaw variant accepted without TEAMCLAW_DIR' >&2
    exit 1
fi
TEAMCLAW_DIR="$TC_HOME"
frontend_select_variant
[[ "$FRONTEND_DIR" == "$TC_HOME" && "$FRONTEND_DEFAULT_SCRIPT" == devs:local ]]
[[ "$FRONTEND_ROOT_ID" == "root" ]]
# teamclaw composes the same gateway/backing defaults as nextgen, plus the
# internal-only extra upstreams that have no singlebox counterpart (they point
# at the gateway so their panels fail visibly instead of hanging on 8888).
unset TEAMCLAW_GW_BASE TEAMCLAW_ADMIN_BASE TASK_ENGINE_UPSTREAM \
    TEAMCLAW_PRIVATE_CHAT_MANAGEMENT_BASE TEAMCLAW_PRIVATE_CHAT_SESSION_BASE \
    TEAMCLAW_LEGACY_AGENTCLAW_BASE TEAMCLAW_AIXHARNESS_BASE TEAMCLAW_CLAWWEB_BASE \
    TEAMCLAW_DEV_USER BCS_ENDPOINT_PRE BCS_ENDPOINT_PROD
GATEWAY_PORT=29999 BCS_PORT=29998
frontend_configure_upstreams
[[ "$TEAMCLAW_GW_BASE" == http://127.0.0.1:29999 ]]
[[ "$TEAMCLAW_ADMIN_BASE" == "$TEAMCLAW_GW_BASE" ]]
[[ "$TEAMCLAW_PRIVATE_CHAT_MANAGEMENT_BASE" == "$TEAMCLAW_GW_BASE" ]]
[[ "$TEAMCLAW_PRIVATE_CHAT_SESSION_BASE" == "$TEAMCLAW_GW_BASE" ]]
[[ "$TEAMCLAW_DEV_USER" == "001" ]]
FRONTEND_VARIANT=teamclaw
# all.sh's _ALL_SH_LOADED guard makes a plain re-source a no-op — unset it so
# the module body actually re-runs under teamclaw (the first source at the
# top ran under nextgen), otherwise the order assertion below would compare
# the leftover nextgen arrays and pass vacuously.
unset _ALL_SH_LOADED
source "$ROOT/scripts/modules/all.sh"
[[ "${START_ORDER[*]}" == 'baas backend bcsfuse bcs bots demo_bot gateway frontend' ]]
# Stop is the unconditional union (legacy + gateway), regardless of variant.
[[ "${STOP_ORDER[*]}" == 'frontend gateway demo_bot bots bcsfuse bcs backend baas' ]]
printf 'PASS: teamclaw variant mapping, upstreams and Gateway lifecycle order\n'

# ---------------------------------------------------------------------------
# teamclaw auto-update: fetch + fast-forward on a clean checkout, warn and
# keep the current tree when it is dirty. Driven by a real throwaway git repo
# pair (worktree + bare remote) so the semantics are the script's own git
# calls, not a mock. utils.sh is needed for the log_* helpers.
# ---------------------------------------------------------------------------
source "$ROOT/scripts/utils.sh"
git init -q --bare "$TEMP/remote.git"
TC_CLONE="$TEMP/tc-clone"
git init -q -b main "$TC_CLONE"
git -C "$TC_CLONE" remote add origin "$TEMP/remote.git"
( cd "$TC_CLONE" && git config user.email t@t && git config user.name t
  echo one > file && git add . && git commit -qm one
  git push -q origin main )
( cd "$TC_CLONE" && echo two >> file && git commit -qam two && git push -q origin main )

# Clean checkout that is one behind: sync fast-forwards it.
TEAMCLAW_DIR="$TC_CLONE"
frontend_teamclaw_sync_latest >/dev/null
[[ "$(git -C "$TC_CLONE" rev-parse HEAD)" == "$(git -C "$TC_CLONE" rev-parse origin/main)" ]] || {
    echo 'sync did not fast-forward a clean checkout' >&2; exit 1; }

# Dirty checkout that is one behind: sync must NOT move HEAD.
( cd "$TC_CLONE" && echo three >> file && git commit -qam local-commit && git push -q origin main )
echo uncommitted >> "$TC_CLONE/file"
before="$(git -C "$TC_CLONE" rev-parse HEAD)"
frontend_teamclaw_sync_latest >/dev/null
[[ "$(git -C "$TC_CLONE" rev-parse HEAD)" == "$before" ]] || {
    echo 'sync moved a dirty checkout' >&2; exit 1; }
git -C "$TC_CLONE" checkout -q -- file
( cd "$TC_CLONE" && git reset -q --hard origin/main )

# Already at latest: sync succeeds (exit 0 semantics: never block startup).
frontend_teamclaw_sync_latest >/dev/null

# Opt-out: with TEAMCLAW_FRONTEND_AUTOUPDATE=0 a behind checkout is NOT moved.
( cd "$TC_CLONE" && echo four >> file && git commit -qam local-commit && git push -q origin main )
before="$(git -C "$TC_CLONE" rev-parse HEAD)"
TEAMCLAW_FRONTEND_AUTOUPDATE=0 frontend_teamclaw_sync_latest >/dev/null
[[ "$(git -C "$TC_CLONE" rev-parse HEAD)" == "$before" ]] || {
    echo 'sync moved HEAD with auto-update disabled' >&2; exit 1; }
printf 'PASS: teamclaw auto-update fast-forward, dirty-skip and opt-out\n'

# The ready banner must surface the gateway dev-login entry for every
# non-legacy variant, independent of GATEWAY_AUTH_MOCK (that flag gates the
# dev principal header strategy, not the dev_cookie strategy the login page
# arms — and the gateway is routinely started by another invocation, so the
# export is not even visible here). The URL must carry 127.0.0.1, the docs'
# canonical host: cookie jars are host-scoped, so localhost/127.0.0.1 must
# never be mixed. The legacy variant must not advertise a page it does not use.
source "$ROOT/scripts/utils.sh"
FRONTEND_VARIANT=nextgen
banner="$(FRONTEND_PORT=28800 GATEWAY_PORT=28801 print_frontend_ready_banner)"
grep -q "http://127.0.0.1:28801/_dev/login?next=28800" <<<"$banner" || {
    echo 'nextgen banner missing the exact dev-login entry' >&2; exit 1; }
grep -q "http://127.0.0.1:28800/" <<<"$banner" || {
    echo 'banner workbench URL must use 127.0.0.1 (cookie jars are host-scoped)' >&2; exit 1; }
FRONTEND_VARIANT=teamclaw
banner="$(FRONTEND_PORT=28800 GATEWAY_PORT=28801 print_frontend_ready_banner)"
grep -q "/_dev/login" <<<"$banner" || { echo 'teamclaw banner missing dev-login entry' >&2; exit 1; }
FRONTEND_VARIANT=legacy
banner="$(FRONTEND_PORT=28800 GATEWAY_PORT=28801 GATEWAY_AUTH_MOCK=1 print_frontend_ready_banner)"
if grep -q "/_dev/login" <<<"$banner"; then
    echo 'legacy banner must not advertise the dev-login entry' >&2
    exit 1
fi
printf 'PASS: non-legacy ready banner advertises the gateway dev-login entry\n'

# ---------------------------------------------------------------------------
# frontend-pull (singlebox.sh frontend-pull): the explicit, on-demand version
# of the startup auto-update — it refuses loudly where the sync only warns.
# Offline by construction: local-protocol throwaway remotes only, and
# OCB_SKIP_FRONTEND_INSTALL=1 on every probe (the npm re-install is the one
# step that would touch tools/registry; same OCB_SKIP_ escape-prefix family
# as OCB_SKIP_GIT_HOOKS). Probes run in a child shell so the harness's own
# TEAMCLAW_DIR never leaks in. utils.sh is sourced because singlebox.sh
# always has the log_* helpers loaded when it calls frontend_pull.
# ---------------------------------------------------------------------------
pull_run() {
  local tc="$1"
  shift
  if [ -n "${tc}" ]; then
    env PROJECT_ROOT="$ROOT" LOG_DIR="$TEMP" DEP_DIR="$TEMP" TEAMCLAW_DIR="${tc}" \
      OCB_SKIP_FRONTEND_INSTALL=1 \
      bash -c "source '$ROOT/scripts/utils.sh'; source '$ROOT/scripts/modules/frontend.sh'; frontend_pull $*" 2>&1
  else
    env -u TEAMCLAW_DIR PROJECT_ROOT="$ROOT" LOG_DIR="$TEMP" DEP_DIR="$TEMP" \
      OCB_SKIP_FRONTEND_INSTALL=1 \
      bash -c "source '$ROOT/scripts/utils.sh'; source '$ROOT/scripts/modules/frontend.sh'; frontend_pull $*" 2>&1
  fi
}

# Fixtures: a bare remote owning main, plus a plain checkout of it on main
# tracking origin/main. pull_advance_remote pushes one new commit from the
# checkout's own history and rewinds it, leaving it genuinely one behind.
git init -q --bare "$TEMP/pull-remote.git"
git init -q -b main "$TEMP/pull-seed"
( cd "$TEMP/pull-seed" && git config user.email t@t && git config user.name t
  echo one > file && git add . && git commit -qm one )
git -C "$TEMP/pull-seed" push -q "$TEMP/pull-remote.git" main:main
PULL_TC="$TEMP/pull-tc"
git init -q -b main "$PULL_TC"
git -C "$PULL_TC" remote add origin "$TEMP/pull-remote.git"
( cd "$PULL_TC" && git config user.email t@t && git config user.name t
  git fetch -q origin
  git branch -q main origin/main
  git checkout -q main )
pull_advance_remote() {
  ( cd "$PULL_TC" && git fetch -q origin && git reset -q --hard origin/main \
    && echo "$1" >> file && git commit -qam "$1" && git push -q origin main )
  git -C "$PULL_TC" reset -q --hard HEAD~1
}

# No TEAMCLAW_DIR -> the command has no target and must refuse, not guess.
rc=0; out="$(pull_run "")" || rc=$?
[ "$rc" -ne 0 ] || { echo 'frontend_pull ran without TEAMCLAW_DIR' >&2; exit 1; }
grep -q "TEAMCLAW_DIR is not set" <<<"$out" || { echo "unset-TEAMCLAW_DIR refusal must say so: $out" >&2; exit 1; }

# A non-repo dir must fail on the not-a-git-checkout refusal, not vacuously
# pass the porcelain guard (no `git status` on a non-repo dir can be dirty).
mkdir -p "$TEMP/pull-not-a-repo"
rc=0; out="$(pull_run "$TEMP/pull-not-a-repo")" || rc=$?
[ "$rc" -ne 0 ] || { echo 'frontend_pull accepted a non-repo dir' >&2; exit 1; }
grep -q "is not a git checkout" <<<"$out" || { echo "non-repo refusal wording missing: $out" >&2; exit 1; }

# Dirty tree -> refuse before any fetch: the WIP file survives verbatim and
# HEAD does not move even though the remote has advanced.
pull_advance_remote two
echo wip > "$PULL_TC/local-note"
before="$(git -C "$PULL_TC" rev-parse HEAD)"
rc=0; out="$(pull_run "$PULL_TC")" || rc=$?
[ "$rc" -ne 0 ] || { echo 'frontend_pull accepted a dirty tree' >&2; exit 1; }
grep -q "dirty" <<<"$out" || { echo "dirty refusal must say dirty: $out" >&2; exit 1; }
[ -f "$PULL_TC/local-note" ] || { echo 'dirty refusal destroyed the WIP file' >&2; exit 1; }
[ "$(git -C "$PULL_TC" rev-parse HEAD)" = "$before" ] || { echo 'dirty refusal moved HEAD' >&2; exit 1; }
rm -f "$PULL_TC/local-note"

# Detached HEAD -> refused distinctly (advancing a detached checkout would
# disarm the upstream-based startup auto-update).
git -C "$PULL_TC" checkout -q --detach
rc=0; out="$(pull_run "$PULL_TC")" || rc=$?
[ "$rc" -ne 0 ] || { echo 'frontend_pull accepted a detached checkout' >&2; exit 1; }
grep -q "detached" <<<"$out" || { echo "detached refusal must say detached: $out" >&2; exit 1; }
git -C "$PULL_TC" checkout -q main

# Branch with no upstream -> the hard never-guess refusal (frontend-pull
# never invents a ref like master to pull toward).
git -C "$PULL_TC" checkout -q -b no-up
rc=0; out="$(pull_run "$PULL_TC")" || rc=$?
[ "$rc" -ne 0 ] || { echo 'frontend_pull accepted a branch with no upstream' >&2; exit 1; }
grep -q "no pull target" <<<"$out" || { echo "no-upstream refusal wording missing: $out" >&2; exit 1; }
grep -q "refusing to guess" <<<"$out" || { echo "never-guess refusal wording missing: $out" >&2; exit 1; }
git -C "$PULL_TC" checkout -q main

# Clean + behind -> advanced to the remote tip, on-branch, with the
# "pulled to (was ...)" report.
pull_advance_remote three
rc=0; out="$(pull_run "$PULL_TC")" || rc=$?
[ "$rc" -eq 0 ] || { echo "clean behind pull failed (rc ${rc}): $out" >&2; exit 1; }
grep -q "pulled to" <<<"$out" || { echo "expected a 'pulled to' message: $out" >&2; exit 1; }
[ "$(git -C "$PULL_TC" rev-parse HEAD)" = "$(git -C "$PULL_TC" rev-parse origin/main)" ] || {
    echo 'clean pull did not advance HEAD to the remote tip' >&2; exit 1; }
[ "$(git -C "$PULL_TC" branch --show-current)" = "main" ] || {
    echo 'clean pull moved the checkout off its branch' >&2; exit 1; }

# Already at tip -> friendly no-op with a distinct message and no advance.
before="$(git -C "$PULL_TC" rev-parse HEAD)"
rc=0; out="$(pull_run "$PULL_TC")" || rc=$?
[ "$rc" -eq 0 ] || { echo "at-tip pull must succeed (rc ${rc}): $out" >&2; exit 1; }
grep -q "already up to date" <<<"$out" || { echo "at-tip pull must say 'already up to date': $out" >&2; exit 1; }
if grep -q "pulled to" <<<"$out"; then
    echo "at-tip pull must not report an advance: $out" >&2; exit 1
fi
[ "$(git -C "$PULL_TC" rev-parse HEAD)" = "$before" ] || { echo 'at-tip pull moved HEAD' >&2; exit 1; }
printf 'PASS: frontend-pull refusals, ff-only advance and at-tip no-op\n'
