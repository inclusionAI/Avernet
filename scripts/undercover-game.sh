#!/usr/bin/env bash
set -euo pipefail

# Start the six-bot Undercover game stack through the existing singlebox
# lifecycle, while staging profile-local skills into the OpenClaw workspaces.
#
# Use the standard singlebox standalone root so the game bots are visible in
# the same OpenClaw workspace tree as other singlebox-managed bots.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SINGLEBOX_SCRIPT="${PROJECT_ROOT}/scripts/singlebox.sh"
PROFILE_DIR="${PROJECT_ROOT}/scripts/6bots_undercover_game_profile"
RUNTIME_ROOT="${UNDERCOVER_STANDALONE_OPENCLAW_ROOT:-${PROJECT_ROOT}/.standalone-openclaw}"

usage() {
    cat <<USAGE
Usage: $0 <command>

Commands:
  start    Start Avernet BCS + frontend, stage game skills, and start/onboard six bots
  stop     Stop the six game bots, then stop Avernet BCS + frontend
  clean    Stop the stack and remove its runtime/cache files and logs
  restart  Run stop, clean, and start in that order

Environment:
  UNDERCOVER_STANDALONE_OPENCLAW_ROOT
      Override the OpenClaw runtime root (default: .standalone-openclaw).
USAGE
}

require_file() {
    local path="$1"
    if [ ! -f "$path" ]; then
        printf 'error: required file not found: %s\n' "$path" >&2
        exit 1
    fi
}

require_dir() {
    local path="$1"
    if [ ! -d "$path" ]; then
        printf 'error: required directory not found: %s\n' "$path" >&2
        exit 1
    fi
}

validate_inputs() {
    require_file "$SINGLEBOX_SCRIPT"
    require_file "${PROFILE_DIR}/bots.json"
    require_dir "$PROFILE_DIR"

    if ! command -v jq >/dev/null 2>&1; then
        printf 'error: jq is required to stage the profile-local skills\n' >&2
        exit 1
    fi

    if ! jq -e '.version == 1 and (.bots | type == "array" and length == 6)' \
        "${PROFILE_DIR}/bots.json" >/dev/null 2>&1; then
        printf 'error: expected a version 1 six-bot manifest: %s\n' \
            "${PROFILE_DIR}/bots.json" >&2
        exit 1
    fi
}

singlebox() {
    # Keep all calls in the same isolated standalone root. Passing the profile
    # directory on every invocation also lets singlebox select the same
    # dynamic-bot group for stop/clean operations.
    STANDALONE_OPENCLAW_ROOT="$RUNTIME_ROOT" \
        "$SINGLEBOX_SCRIPT" "$@" --profile-dir "$PROFILE_DIR"
}

stage_profile_skills() {
    local bot_profile source source_dir workspace_dir skills_dir

    while IFS=$'\t' read -r bot_profile source; do
        source_dir="${PROFILE_DIR}/${source}/skills"
        workspace_dir="${RUNTIME_ROOT}/workspaces/${bot_profile}"
        skills_dir="${workspace_dir}/skills"

        if [ ! -d "$source_dir" ]; then
            printf 'error: skill directory not found for %s: %s\n' \
                "$bot_profile" "$source_dir" >&2
            return 1
        fi

        # Make the profile-local skill tree authoritative, while leaving
        # bcs-coordination for singlebox's own setup step.
        rm -rf "$skills_dir"
        mkdir -p "$skills_dir"
        cp -R "${source_dir}/." "$skills_dir/"
        printf 'staged skills for %s: %s\n' "$bot_profile" "$source_dir"
    done < <(jq -r '.bots[] | [.profile, .source] | @tsv' "${PROFILE_DIR}/bots.json")
}

start_stack() {
    printf '==> Starting Avernet BCS + frontend\n'
    singlebox start bcs_frontend

    printf '==> Staging Undercover profile-local skills\n'
    if ! stage_profile_skills; then
        singlebox stop bcs_frontend || true
        return 1
    fi

    printf '==> Starting and onboarding Undercover game bots\n'
    if ! singlebox start bots; then
        singlebox stop bots || true
        singlebox stop bcs_frontend || true
        return 1
    fi
}

stop_stack() {
    local rc=0

    printf '==> Stopping Undercover game bots\n'
    singlebox stop bots || rc=$?

    printf '==> Stopping Avernet BCS + frontend\n'
    singlebox stop bcs_frontend || rc=$?

    return "$rc"
}

clean_runtime() {
    local rc=0

    printf '==> Cleaning Undercover game bot runtime\n'
    singlebox clean bots || rc=$?

    printf '==> Cleaning Avernet BCS runtime\n'
    singlebox clean bcs_frontend || rc=$?

    # bcs_frontend_clean delegates to BCS clean and intentionally does not
    # remove frontend logs. Remove only logs owned by this singlebox stack.
    rm -f \
        "${PROJECT_ROOT}/scripts/.dependencies/frontend.pid" \
        "${PROJECT_ROOT}/scripts/.dependencies/logs/frontend.log" \
        "${PROJECT_ROOT}/scripts/.dependencies/logs/bcs.log"

    # OpenClaw's own runtime logs live below the standard standalone root.
    # Remove only the log directory; per-bot profiles/workspaces were removed
    # by `singlebox clean bots` above.
    rm -rf "${RUNTIME_ROOT}/logs"

    return "$rc"
}

start() {
    validate_inputs
    start_stack
}

stop() {
    validate_inputs
    stop_stack
}

clean() {
    validate_inputs
    stop_stack || true
    clean_runtime
}

restart() {
    validate_inputs
    stop_stack || true
    clean_runtime
    start_stack
}

main() {
    local command="${1:-}"

    case "$command" in
        start)
            [ "$#" -eq 1 ] || { usage >&2; exit 2; }
            start
            ;;
        stop)
            [ "$#" -eq 1 ] || { usage >&2; exit 2; }
            stop
            ;;
        clean)
            [ "$#" -eq 1 ] || { usage >&2; exit 2; }
            clean
            ;;
        restart)
            [ "$#" -eq 1 ] || { usage >&2; exit 2; }
            restart
            ;;
        -h|--help|help)
            [ "$#" -eq 1 ] || { usage >&2; exit 2; }
            usage
            ;;
        *)
            usage >&2
            exit 2
            ;;
    esac
}

main "$@"
