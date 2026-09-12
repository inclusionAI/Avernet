#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SINGLEBOX="${ROOT}/scripts/singlebox.sh"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

# singlebox.sh must not export SERVER_ENV into the module environment.
#
# The gateway launcher (modules/gateway.sh) treats SERVER_ENV as an
# operator-controlled input with a local default: local arms the dev principal
# signing key in app.sh and selects application-local.yaml. A dispatch-level
# `export SERVER_ENV=dev` overrides both — the gateway then asks for a
# application-dev.yaml that clean checkouts do not carry and signs no principal
# at all, so every signed-upstream request fails with 500. The backend needs
# SERVER_ENV=dev only for its own process, and backend.sh provides that on its
# launch command (SERVER_ENV=dev DEPLOY_PROFILE=...), so the export at dispatch
# level is redundant for the backend and fatal for the gateway.
test_dispatch_trace_has_no_server_env_export() {
  local trace
  trace="$(bash -x "${SINGLEBOX}" status gateway 2>&1 || true)"
  if grep -E '^[[:space:]]*\+[[:space:]]+export SERVER_ENV=' <<<"${trace}" | head -1 | grep -q .; then
    fail "singlebox dispatch leaked SERVER_ENV into the module environment"
  fi
}

# The behavioral trace above only exercises the explicit-command dispatch path;
# the no-argument default flow (setup_all_and_start) runs a second copy of the
# block. A source-level invariant covers both sites.
test_source_has_no_server_env_export() {
  if grep -nE '^[[:space:]]*export[[:space:]]+SERVER_ENV=' "${ROOT}/scripts/singlebox.sh" | head -1 | grep -q .; then
    fail "${ROOT}/scripts/singlebox.sh still exports SERVER_ENV (the gateway launcher must own that variable)"
  fi
}

# The backend keeps its dev profile even after the dispatch export is gone:
# its launch command carries SERVER_ENV itself (see test_singlebox_service_guards.sh
# for the behavior-level assertion on the generated start body).
test_backend_launch_keeps_dev_profile() {
  grep -q 'SERVER_ENV=dev DEPLOY_PROFILE=' "${ROOT}/scripts/modules/backend.sh" ||
    fail "backend launch lost its SERVER_ENV=dev DEPLOY_PROFILE prefix"
}

test_dispatch_trace_has_no_server_env_export
test_source_has_no_server_env_export
test_backend_launch_keeps_dev_profile

printf 'PASS: singlebox does not leak SERVER_ENV into module launches\n'
