#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SINGLEBOX="${ROOT}/scripts/singlebox.sh"
TEMP="$(mktemp -d)"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

# Section 5 spawns a throwaway listener that must be killed even when an
# assertion fails mid-flight. A single EXIT trap must cover that cleanup, the
# env.local restore, and the mktemp dir: a second `trap ... EXIT` would
# silently replace the first, and the earlier `rm -rf "$TEMP"` trap would leak
# the temp dir (and, worse, the listener).
stop_foreign_listener_if_any() {
  [ -n "${foreign_listener_pid:-}" ] && kill "${foreign_listener_pid}" 2>/dev/null || true
}

# A broken FRONTEND_VARIANT config (typo'd value, or a teamclaw TEAMCLAW_DIR
# that was moved/deleted) must never brick the toolbox: read-only/lifecycle
# commands keep working — a live stack must always keep a script path to stop
# it — while setup/start still fail fast with the selection error before
# anything is built.
#
# .env.local values override environment variables (load_repo_env_file sources
# the file with `set -a`), so the broken-variant probes below are only
# meaningful when the checkout has no .env.local of its own. Hide it for the
# duration of the probes and restore it on exit; on CI the file does not exist.
if [ -f "${ROOT}/.env.local" ]; then
    mv "${ROOT}/.env.local" "${ROOT}/.env.local.hidden-by-variant-gate-test"
    restore_env_local() {
        [ -f "${ROOT}/.env.local.hidden-by-variant-gate-test" ] &&
            mv -f "${ROOT}/.env.local.hidden-by-variant-gate-test" "${ROOT}/.env.local" || true
    }
else
    restore_env_local() { :; }
fi
trap 'stop_foreign_listener_if_any; restore_env_local; rm -rf "$TEMP"' EXIT

status_probe() {
    FRONTEND_VARIANT="$1" TEAMCLAW_DIR="${2:-}" bash "${SINGLEBOX}" status all 2>&1
}
start_probe() {
    FRONTEND_VARIANT="$1" TEAMCLAW_DIR="${2:-}" bash "${SINGLEBOX}" start frontend 2>&1
}

# 1. A dangling TEAMCLAW_DIR must not block the read-only path: status still
#    runs to completion. The selection error itself is expected output (the
#    source-time select runs and fails before falling back); "degraded, not
#    bricked" means rc 0 plus a real status section under it.
out="$(status_probe teamclaw "${TEMP}/no-such-teamclaw-dir")" ||
    fail "status must stay usable when TEAMCLAW_DIR dangles; got: ${out}"
grep -q "Service Status" <<<"$out" ||
    fail "status produced no status section under a broken variant; got: ${out}"
grep -q "TEAMCLAW_DIR does not exist" <<<"$out" ||
    fail "the degraded status should still surface the selection error; got: ${out}"

# 2. An invalid variant string must not block it either.
out="$(status_probe invalid-variant "")" ||
    fail "status must stay usable under an invalid FRONTEND_VARIANT; got: ${out}"

# 3. The same broken configs must still fail START paths at the strict gate,
#    before any service work: nonzero rc, the selection error, and none of
#    the mode banner the dispatch only prints after the gate has passed.
out="$(start_probe teamclaw "${TEMP}/no-such-teamclaw-dir")" &&
    fail "start must fail fast when TEAMCLAW_DIR dangles"
grep -q "TEAMCLAW_DIR does not exist" <<<"$out" ||
    fail "start failure must name the selection error; got: ${out}"
if grep -q "STANDALONE MODE ENABLED" <<<"$out"; then
    fail "start did not fail fast (it reached the mode banner): ${out}"
fi

out="$(start_probe invalid-variant "")" &&
    fail "start must fail fast under an invalid FRONTEND_VARIANT"
grep -q "FRONTEND_VARIANT must be legacy, nextgen or teamclaw" <<<"$out" ||
    fail "start failure must name the invalid-variant error; got: ${out}"

# 4. bcs_frontend refuses non-legacy variants up front: the composite forces
#    the legacy panel scripts (devs:local:oss / devs:dev, which exist in no
#    other frontend) and composes no gateway for the non-legacy UI's
#    OpenAPI/auth routes. Loud refusal over a half-started stack. The guard
#    runs before any service call, so a function-level probe suffices.
export PROJECT_ROOT="$ROOT" LOG_DIR="$TEMP" DEP_DIR="$TEMP" FRONTEND_PORT=28800 BCS_PORT=28801
# shellcheck source=/dev/null
source "${ROOT}/scripts/utils.sh"
# shellcheck source=/dev/null
source "${ROOT}/scripts/modules/frontend.sh"
# shellcheck source=/dev/null
source "${ROOT}/scripts/modules/bcs_frontend.sh"
FRONTEND_VARIANT=nextgen frontend_select_variant
if out="$(FRONTEND_VARIANT=nextgen bcs_frontend_setup 2>&1)"; then
    fail "bcs_frontend_setup must refuse non-legacy variants"
fi
grep -q "serves the legacy BCS panel" <<<"$out" ||
    fail "bcs_frontend_setup refusal must explain the legacy constraint; got: ${out}"
if out="$(FRONTEND_VARIANT=teamclaw bcs_frontend_start 2>&1)"; then
    fail "bcs_frontend_start must refuse non-legacy variants"
fi
grep -q "serves the legacy BCS panel" <<<"$out" ||
    fail "bcs_frontend_start refusal must explain the legacy constraint; got: ${out}"

# 5. gateway_stop must never kill a foreign listener on its port. It once
#    delegated to app.sh stop first, and src/gateway/scripts/app.sh do_stop —
#    when tmp/app.port is absent (the steady state after any clean stop, since
#    do_stop itself removes it) — resolves whatever PID holds the app port
#    and kills it blindly (kill, 1s wait, kill -9) with no ownership check.
#    The fake app.sh below reproduces exactly that blind kill, so this section
#    fails loudly if the delegation is ever reintroduced: the listener
#    survives only while every gateway_stop kill stays inside the
#    owned-process contract.
if command -v python3 >/dev/null 2>&1; then
    gw_dir="${TEMP}/foreign-gw"
    mkdir -p "${gw_dir}/scripts" "${TEMP}/unrelated-cwd"
    cat > "${gw_dir}/scripts/app.sh" <<'EOF'
#!/usr/bin/env bash
# Mimics src/gateway/scripts/app.sh do_stop's blind port kill (no ownership
# check): the port holder's PID is resolved and killed unconditionally.
PORT_PID="$(lsof -t -nP -iTCP:"${APP_PORT:-${GATEWAY_PORT:-8889}}" -sTCP:LISTEN 2>/dev/null | head -1)"
[ -n "$PORT_PID" ] || exit 0
kill "$PORT_PID" 2>/dev/null
sleep 1
kill -0 "$PORT_PID" 2>/dev/null && kill -9 "$PORT_PID" 2>/dev/null
exit 0
EOF
    chmod +x "${gw_dir}/scripts/app.sh"

    foreign_listener_pid=""
    foreign_port=28910
    while port_is_listening "$foreign_port"; do foreign_port=$((foreign_port + 1)); done
    ( cd "${TEMP}/unrelated-cwd" && exec python3 -m http.server "$foreign_port" --bind 127.0.0.1 ) >/dev/null 2>&1 &
    foreign_listener_pid=$!
    for _ in $(seq 1 20); do
        port_is_listening "$foreign_port" && break
        sleep 0.1
    done
    port_is_listening "$foreign_port" ||
        { kill "$foreign_listener_pid" 2>/dev/null || true
          fail "test setup failed: foreign listener never bound port ${foreign_port}"; }

    stop_rc=0
    stop_out="$(
        GATEWAY_DIR="$gw_dir" GATEWAY_PORT="$foreign_port" LOG_DIR="$TEMP" DEP_DIR="$TEMP" \
        PROJECT_ROOT="$ROOT" bash -c '
            # shellcheck source=/dev/null
            source "${1}/utils.sh"
            # shellcheck source=/dev/null
            source "${1}/modules/gateway.sh"
            gateway_stop
        ' _ "${ROOT}/scripts" 2>&1
    )" || stop_rc=$?
    [ "$stop_rc" -eq 0 ] ||
        fail "gateway_stop must tolerate a foreign port holder (rc ${stop_rc}); got: ${stop_out}"
    kill -0 "$foreign_listener_pid" 2>/dev/null ||
        fail "gateway_stop killed a foreign listener on its port (owned-process contract broken); got: ${stop_out}"
    port_is_listening "$foreign_port" ||
        fail "foreign listener survived but stopped listening after gateway_stop; got: ${stop_out}"
    grep -q "outside this checkout" <<<"$stop_out" ||
        fail "gateway_stop must warn that the foreign port holder lives outside this checkout; got: ${stop_out}"

    kill "$foreign_listener_pid" 2>/dev/null || true
    wait "$foreign_listener_pid" 2>/dev/null || true
    foreign_listener_pid=""
else
    # python3 (the only stdlib one-liner listener this harness relies on; no
    # nc assumption) is unavailable in this environment: section 5's foreign
    # listener cannot be spawned, so it is skipped rather than faked.
    printf 'SKIP: section 5 (gateway_stop foreign-listener refusal) — python3 not found\n'
fi

# 6. The help surface lists the frontend-pull command under Commands.
help_out="$(bash "${SINGLEBOX}" --help 2>&1)" ||
    fail "help must stay usable; got: ${help_out}"
grep -q "frontend-pull" <<<"$help_out" ||
    fail "help Commands section does not list frontend-pull; got: ${help_out}"

# 7. frontend-pull is a command, not a service: it must route to frontend_pull
#    with the variant selected — its own refusal for a non-git TEAMCLAW_DIR is
#    the proof the router arm ran — rather than dying at arg parsing
#    ("Unknown option") or falling through the router's default arm and
#    kicking off the full setup-and-start.
fp_dir="${TEMP}/fp-not-a-repo"
mkdir -p "${fp_dir}"
out="$(FRONTEND_VARIANT=teamclaw TEAMCLAW_DIR="${fp_dir}" \
    bash "${SINGLEBOX}" frontend-pull 2>&1)" &&
    fail "frontend-pull must fail on a non-git TEAMCLAW_DIR"
grep -q "not a git checkout" <<<"$out" ||
    fail "frontend-pull did not reach its own refusal; got: ${out}"
if grep -q "Unknown option" <<<"$out"; then
    fail "frontend-pull was rejected by arg parsing: ${out}"
fi
if grep -q "STANDALONE MODE ENABLED" <<<"$out"; then
    fail "frontend-pull fell through to the full-setup default arm: ${out}"
fi

printf 'PASS: variant gate keeps lifecycle commands usable, starts fail-fast, gateway_stop keeps the owned-process contract, and frontend-pull routes as a command\n'
