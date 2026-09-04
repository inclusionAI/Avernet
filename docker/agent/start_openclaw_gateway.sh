#!/usr/bin/env bash
# start_openclaw_gateway.sh — OpenClaw gateway process wrapper for supervisord.
#
# Used by [program:openclaw] in docker/agent/avernet-supervisord.conf (installed
# to /usr/local/bin by avernet.dockerfile). Derives a V8 old-space limit from
# the container's cgroup memory limit, then execs
# `openclaw gateway run --verbose`. Keeping the heap below the cgroup limit
# leaves room for native allocations, stacks, buffers, subprocesses, and the
# other container services (supervisord, engine, claude_relay).
#
# Ported from the arca-openclaw image's /opt/bin/start_openclaw.sh, heap-limit
# part only: the Skills Pool symlink-trust step is NOT ported (the Avernet
# agent image does not use the skills-pool layout).
#
# The exec replaces the wrapper process, so supervisord signal handling
# (stopasgroup/killasgroup) reaches the real gateway process directly.
# Heap-limit decisions are printed to stderr ("[openclaw-start] ..." lines),
# which land in /home/admin/logs/openclaw_err.log.
#
# Environment overrides (all optional):
#   OPENCLAW_BIN                          openclaw binary (default
#                                         /usr/local/bin/openclaw — the npm
#                                         global prefix in this image)
#   OPENCLAW_CGROUP_V2_MEMORY_MAX_FILE    cgroup v2 memory.max path
#   OPENCLAW_CGROUP_V1_MEMORY_LIMIT_FILE  cgroup v1 memory.limit_in_bytes path
#   NODE_OPTIONS with an explicit --max-old-space-size (or --max_old_space_size)
#                                         preserved untouched; no limit added

set -u

readonly OPENCLAW_HEAP_RATIO_NUMERATOR=5
readonly OPENCLAW_HEAP_RATIO_DENOMINATOR=8
readonly OPENCLAW_MAX_REASONABLE_MEMORY_BYTES=1125899906842624 # 1 PiB

_trim_whitespace() {
    local value="$1"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    printf '%s' "$value"
}

_normalize_bounded_memory_limit() {
    local value
    value="$(_trim_whitespace "$1")"

    [[ "$value" =~ ^[0-9]+$ ]] || return 1

    while [[ ${#value} -gt 1 && "${value:0:1}" == "0" ]]; do
        value="${value:1}"
    done
    [[ "$value" != "0" ]] || return 1

    # cgroup v1 commonly represents "unlimited" with a value close to INT64_MAX.
    # Reject implausibly large values before using shell integer arithmetic.
    if [[ ${#value} -gt ${#OPENCLAW_MAX_REASONABLE_MEMORY_BYTES} ]]; then
        return 1
    fi
    if ((10#$value > OPENCLAW_MAX_REASONABLE_MEMORY_BYTES)); then
        return 1
    fi

    printf '%s' "$value"
}

_read_memory_limit() {
    local v2_file="${OPENCLAW_CGROUP_V2_MEMORY_MAX_FILE:-/sys/fs/cgroup/memory.max}"
    local v1_file="${OPENCLAW_CGROUP_V1_MEMORY_LIMIT_FILE:-/sys/fs/cgroup/memory/memory.limit_in_bytes}"
    local value

    if [[ -r "$v2_file" ]]; then
        value="$(cat -- "$v2_file" 2>/dev/null || true)"
        if value="$(_normalize_bounded_memory_limit "$value")"; then
            printf '%s\t%s\n' "$value" "$v2_file"
            return 0
        fi
    fi

    if [[ -r "$v1_file" ]]; then
        value="$(cat -- "$v1_file" 2>/dev/null || true)"
        if value="$(_normalize_bounded_memory_limit "$value")"; then
            printf '%s\t%s\n' "$value" "$v1_file"
            return 0
        fi
    fi

    return 1
}

_has_explicit_old_space_limit() {
    local options="${NODE_OPTIONS:-}"
    [[ "$options" =~ (^|[[:space:]])--max-old-space-size($|=|[[:space:]]) ]] \
        || [[ "$options" =~ (^|[[:space:]])--max_old_space_size($|=|[[:space:]]) ]]
}

_configure_node_heap_limit() {
    local detected limit_bytes source limit_mb heap_mb

    if _has_explicit_old_space_limit; then
        printf '[openclaw-start] preserving explicit V8 heap limit from NODE_OPTIONS\n' >&2
        return 0
    fi

    if ! detected="$(_read_memory_limit)"; then
        printf '[openclaw-start] cgroup memory limit unavailable; using Node.js default heap limit\n' >&2
        return 0
    fi

    IFS=$'\t' read -r limit_bytes source <<< "$detected"
    limit_mb=$((limit_bytes / 1024 / 1024))
    heap_mb=$((limit_mb * OPENCLAW_HEAP_RATIO_NUMERATOR / OPENCLAW_HEAP_RATIO_DENOMINATOR))

    if ((heap_mb < 1)); then
        printf '[openclaw-start] cgroup memory limit is too small to derive a heap limit; using Node.js default\n' >&2
        return 0
    fi

    NODE_OPTIONS="${NODE_OPTIONS:+${NODE_OPTIONS} }--max-old-space-size=${heap_mb}"
    export NODE_OPTIONS
    printf '[openclaw-start] cgroup_limit=%sMB source=%s max_old_space_size=%sMB\n' \
        "$limit_mb" "$source" "$heap_mb" >&2
}

main() {
    _configure_node_heap_limit
    exec "${OPENCLAW_BIN:-/usr/local/bin/openclaw}" gateway run --verbose "$@"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
