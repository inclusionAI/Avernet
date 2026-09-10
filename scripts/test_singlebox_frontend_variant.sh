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

# The ready banner must surface the gateway dev-login entry for the nextgen
# variant: without it, "logging in" locally is a DevTools instruction. The
# legacy variant must not advertise a page it does not use.
source "$ROOT/scripts/utils.sh"
FRONTEND_VARIANT=nextgen
banner="$(FRONTEND_PORT=28800 GATEWAY_PORT=28801 GATEWAY_AUTH_MOCK=1 print_frontend_ready_banner)"
grep -q "/_dev/login" <<<"$banner" || { echo 'nextgen banner missing dev-login entry' >&2; exit 1; }
grep -q "28801" <<<"$banner" || { echo 'dev-login entry does not name the gateway port' >&2; exit 1; }
grep -q "next=28800" <<<"$banner" || { echo 'dev-login entry does not name the frontend port' >&2; exit 1; }
FRONTEND_VARIANT=legacy
banner="$(FRONTEND_PORT=28800 GATEWAY_PORT=28801 print_frontend_ready_banner)"
if grep -q "/_dev/login" <<<"$banner"; then
    echo 'legacy banner must not advertise the dev-login entry' >&2
    exit 1
fi
printf 'PASS: nextgen ready banner advertises the gateway dev-login entry\n'
