#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
FAILS=0

fail() {
    echo "FAIL: $*" >&2
    FAILS=$((FAILS + 1))
}

write_rule_manifest() {
    local directory="$1"
    mkdir -p "$directory"
    cat > "${directory}/bots.json" <<'JSON'
{
  "version": 2,
  "name": "rules",
  "bots": [
    {
      "profile": "friendly",
      "name": "友善助手",
      "summary": "fixed",
      "domains": "utility",
      "skills": "reply",
      "runtime": {
        "type": "rule",
        "behavior": {
          "type": "fixed",
          "replies": ["你好"]
        }
      }
    },
    {
      "profile": "echo",
      "name": "复读机",
      "summary": "echo",
      "domains": "utility",
      "skills": "echo",
      "runtime": {
        "type": "rule",
        "behavior": {
          "type": "echo",
          "repeat": 1000000
        }
      }
    }
  ]
}
JSON
}

with_rule_test_env() {
    local temporary="$1"
    BCS_DIR="${PROJECT_ROOT}/src/bcs"
    LOG_DIR="${temporary}/logs"
    DEP_DIR="${temporary}/dependencies"
    BCS_PORT=21000
    BOTS_PROFILE_DIR="${temporary}/profile"
    OPENCLAW_PROFILE_ROOT="${temporary}/profiles"
    OPENCLAW_PROFILE_PREFIX=""
    mkdir -p "$LOG_DIR" "$DEP_DIR" "$OPENCLAW_PROFILE_ROOT"
    . "${SCRIPT_DIR}/utils.sh"
    . "${SCRIPT_DIR}/modules/bcs.sh"
    . "${SCRIPT_DIR}/modules/bots.sh"
}

test_rule_manifest_has_no_ports_or_sources() {
    local temporary
    temporary="$(mktemp -d)"
    write_rule_manifest "${temporary}/profile"
    (
        with_rule_test_env "$temporary"
        bots_dynamic_validate_manifest
        local specs
        specs="$(bots_dynamic_specs)"
        [ "$(printf '%s\n' "$specs" | wc -l | tr -d ' ')" = "2" ]
        printf '%s\n' "$specs" | awk -F '\t' '
            NF != 9 { exit 1 }
            $3 != "-" { exit 1 }
            $4 != "-" { exit 1 }
            $9 != "rule" { exit 1 }
        '
        ! bots_dynamic_has_runtime openclaw
        bots_dynamic_has_runtime rule
    ) || fail "version 2 rule-only manifest should not require ports or source directories"
    rm -rf "$temporary"
}

test_rule_prereqs_do_not_require_openclaw() {
    local temporary
    temporary="$(mktemp -d)"
    write_rule_manifest "${temporary}/profile"
    (
        with_rule_test_env "$temporary"
        check_openclaw_installed() {
            echo "OpenClaw prerequisite must not be checked for a rule-only profile" >&2
            return 1
        }
        check_bcs_cli_binary() { return 0; }
        bcs_cli_path() { printf '/usr/bin/true\n'; }
        bots_dynamic_rule_binary() { printf '/usr/bin/true\n'; }
        bots_dynamic_prereqs
    ) || fail "rule-only prerequisites should not require OpenClaw, Node.js, npm, or ports"
    rm -rf "$temporary"
}

test_version_two_rejects_unknown_fields_early() {
    local temporary
    temporary="$(mktemp -d)"
    write_rule_manifest "${temporary}/profile"
    jq '.unexpected = true' "${temporary}/profile/bots.json" \
        > "${temporary}/profile/bots.invalid.json"
    mv "${temporary}/profile/bots.invalid.json" "${temporary}/profile/bots.json"
    (
        with_rule_test_env "$temporary"
        ! bots_dynamic_validate_manifest
    ) || fail "version 2 manifests must reject unknown root fields before startup"
    rm -rf "$temporary"
}

test_version_one_defaults_to_openclaw() {
    local temporary
    temporary="$(mktemp -d)"
    (
        with_rule_test_env "$temporary"
        BOTS_PROFILE_DIR="${PROJECT_ROOT}/scripts/5bots_profile"
        bots_dynamic_validate_manifest
        bots_dynamic_has_runtime openclaw
        ! bots_dynamic_has_runtime rule
        bots_dynamic_specs | awk -F '\t' '
            NF != 9 { exit 1 }
            $3 !~ /^[0-9]+$/ { exit 1 }
            $4 == "-" { exit 1 }
            $9 != "openclaw" { exit 1 }
        '
    ) || fail "version 1 manifests should retain the existing OpenClaw defaults"
    rm -rf "$temporary"
}

test_rule_manifest_has_no_ports_or_sources
test_rule_prereqs_do_not_require_openclaw
test_version_two_rejects_unknown_fields_early
test_version_one_defaults_to_openclaw

if [ "$FAILS" -eq 0 ]; then
    echo "ALL PASS"
else
    echo "${FAILS} FAILURE(S)"
    exit 1
fi
