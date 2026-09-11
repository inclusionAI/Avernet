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
source "$ROOT/scripts/modules/all.sh"
[[ "${START_ORDER[*]}" == 'baas backend bcsfuse bcs bots demo_bot gateway frontend' ]]
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

# The ready banner must surface the gateway dev-login entry for the nextgen
# variant: without it, "logging in" locally is a DevTools instruction. The
# legacy variant must not advertise a page it does not use.
source "$ROOT/scripts/utils.sh"
FRONTEND_VARIANT=nextgen
banner="$(FRONTEND_PORT=28800 GATEWAY_PORT=28801 GATEWAY_AUTH_MOCK=1 print_frontend_ready_banner)"
grep -q "/_dev/login" <<<"$banner" || { echo 'nextgen banner missing dev-login entry' >&2; exit 1; }
grep -q "28801" <<<"$banner" || { echo 'dev-login entry does not name the gateway port' >&2; exit 1; }
grep -q "next=28800" <<<"$banner" || { echo 'dev-login entry does not name the frontend port' >&2; exit 1; }
FRONTEND_VARIANT=teamclaw
banner="$(FRONTEND_PORT=28800 GATEWAY_PORT=28801 GATEWAY_AUTH_MOCK=1 print_frontend_ready_banner)"
grep -q "/_dev/login" <<<"$banner" || { echo 'teamclaw banner missing dev-login entry' >&2; exit 1; }
FRONTEND_VARIANT=legacy
banner="$(FRONTEND_PORT=28800 GATEWAY_PORT=28801 print_frontend_ready_banner)"
if grep -q "/_dev/login" <<<"$banner"; then
    echo 'legacy banner must not advertise the dev-login entry' >&2
    exit 1
fi
printf 'PASS: nextgen ready banner advertises the gateway dev-login entry\n'
