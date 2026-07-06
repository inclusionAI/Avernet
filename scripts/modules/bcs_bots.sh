#!/usr/bin/env bash
# scripts/modules/bcs_bots.sh — Composite module: BCS server + 5 local bots
[[ -n "${_BCS_BOTS_SH_LOADED:-}" ]] && return 0
_BCS_BOTS_SH_LOADED=1

bcs_bots_setup() {
    bcs_setup || return 1
    bots_setup || return 1
}

bcs_bots_start() {
    bcs_start || return 1
    bots_start || return 1
}

bcs_bots_stop() {
    local rc=0
    bots_stop || rc=$?
    bcs_stop || rc=$?
    return "$rc"
}

bcs_bots_restart() {
    bcs_bots_stop
    sleep 2
    bcs_bots_start
}

bcs_bots_clean() {
    local rc=0
    bots_clean || rc=$?
    bcs_clean || rc=$?
    return "$rc"
}

bcs_bots_status() {
    bcs_status
    bots_status
}

bcs_bots_ready() {
    bcs_ready && bots_ready
}

bcs_bots_prereqs() {
    local has_error=false
    bcs_prereqs || has_error=true
    bots_prereqs || has_error=true
    [ "$has_error" = false ]
}

bcs_bots_help() {
    echo "bcs_bots - BCS server + 5 local OpenClaw bot gateways"
}
