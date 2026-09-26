#!/usr/bin/env bash
# Self-contained entry point for the agency-agent launcher.
set -euo pipefail

# Move an inline --token out of argv before any longer-lived process starts.
# The launcher chain (bash -> uv -> python) runs in the foreground for the
# whole session, and a token that stays in any argv is visible via `ps`.
# Persist it to <state-dir>/.token (mode 0600) and re-exec without the flag.
ARGS=()
TOKEN_SEEN=0
TOKEN_VALUE=''
TOKEN_FILE_SEEN=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --token)
            if [ "$#" -ge 2 ]; then
                TOKEN_SEEN=1; TOKEN_VALUE="$2"; shift 2
            else
                ARGS+=("$1"); shift  # Missing value: let argparse report it.
            fi
            ;;
        --token=*) TOKEN_SEEN=1; TOKEN_VALUE="${1#--token=}"; shift ;;
        --token-file|--token-file=*) TOKEN_FILE_SEEN=1; ARGS+=("$1"); shift ;;
        *) ARGS+=("$1"); shift ;;
    esac
done
if [ "$TOKEN_SEEN" -eq 1 ]; then
    if [ "$TOKEN_FILE_SEEN" -eq 1 ]; then
        ARGS+=(--token "$TOKEN_VALUE")  # Keep both flags; argparse rejects the combination.
    elif [ -n "$TOKEN_VALUE" ]; then
        STATE_DIR="$HOME/.avernet/bcs/agency-agent"
        i=0
        while [ "$i" -lt "${#ARGS[@]}" ]; do
            case "${ARGS[$i]}" in
                --state-dir)
                    j=$((i + 1))
                    if [ "$j" -lt "${#ARGS[@]}" ]; then
                        STATE_DIR="${ARGS[$j]}"
                    fi
                    ;;
                --state-dir=*) STATE_DIR="${ARGS[$i]#--state-dir=}" ;;
            esac
            i=$((i + 1))
        done
        case "$STATE_DIR" in
            '~') STATE_DIR="$HOME" ;;
            '~/'*) STATE_DIR="$HOME/${STATE_DIR#'~/'}" ;;
        esac
        if ! ( umask 077
               mkdir -p "$STATE_DIR" || exit
               TOKEN_TMP="$(mktemp "$STATE_DIR/.token-writing.XXXXXX")" || exit
               printf '%s\n' "$TOKEN_VALUE" > "$TOKEN_TMP" && \
                   mv -f "$TOKEN_TMP" "$STATE_DIR/.token" || { rm -f "$TOKEN_TMP"; exit 1; } ); then
            printf '%s\n' 'Cannot persist the --token value to <state-dir>/.token; refusing to run with it in the process list.' >&2
            exit 1
        fi
    fi
fi
set -- ${ARGS[@]+"${ARGS[@]}"}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE_ROOT="${HOME}/.avernet/bcs/agency-agent/.bundle"
CACHE_VERSION="v1"
BUNDLE_DIR="$CACHE_ROOT/$CACHE_VERSION"
if [[ ! -f "$SCRIPT_DIR/agency_launcher.py" ]]; then
    if [[ ! -f "$BUNDLE_DIR/agency_launcher.py" ]]; then
        mkdir -p "$CACHE_ROOT"
        TMP_DIR="$(mktemp -d "$CACHE_ROOT/.download.XXXXXX")"
        trap 'rm -rf "$TMP_DIR"' EXIT
        # Use a fixed ref so the launcher never pulls a moving branch unexpectedly.
        curl -fsSL "https://github.com/inclusionAI/Avernet/archive/refs/heads/dev.tar.gz" -o "$TMP_DIR/avernet.tar.gz"
        tar -xzf "$TMP_DIR/avernet.tar.gz" -C "$TMP_DIR"
        cp -R "$TMP_DIR/Avernet-dev/src/bcs/third-party/agency-agent/"* "$TMP_DIR/bundle/"
        for module in agency_launcher.py agency_console.py agency_parallel.py agency_profiles.py agency_runtime.py; do
            [[ -f "$TMP_DIR/bundle/$module" ]] || { printf 'Missing %s in downloaded bundle\n' "$module" >&2; exit 1; }
        done
        mkdir -p "$BUNDLE_DIR"
        mv "$TMP_DIR/bundle"/* "$BUNDLE_DIR/"
    fi
    SCRIPT_DIR="$BUNDLE_DIR"
fi
if [[ -n "${AGENCY_PYTHON:-}" ]]; then
    exec "$AGENCY_PYTHON" "$SCRIPT_DIR/agency_launcher.py" "$@"
fi
if command -v uv >/dev/null 2>&1; then
    exec uv run --script "$SCRIPT_DIR/agency_launcher.py" "$@"
fi
if python3 -c 'import sys, yaml; assert sys.version_info >= (3, 11)' >/dev/null 2>&1; then
    exec python3 "$SCRIPT_DIR/agency_launcher.py" "$@"
fi
printf '%s\n' 'Install uv, or Python 3.11+ with PyYAML 6.0.3, then rerun.' >&2
exit 1
