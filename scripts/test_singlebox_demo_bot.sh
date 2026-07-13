#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODULE="${ROOT}/scripts/modules/demo_bot.sh"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

assert_eq() {
  local expected="$1"
  local actual="$2"
  local label="$3"
  [ "$expected" = "$actual" ] || fail "${label}: expected '${expected}', got '${actual}'"
}

setup_env() {
  unset SINGLEBOX_DEMO_BOT_NAME
  unset SINGLEBOX_DEMO_BOT_DESC
  unset SINGLEBOX_DEMO_ENTITY_ID
  unset SINGLEBOX_DEMO_ENTITY_TYPE
  unset SINGLEBOX_DEMO_ENGINE_TYPE
  unset SINGLEBOX_DEMO_BOT_TYPE
  unset SINGLEBOX_DEMO_TEMPLATE_TYPE
  unset SINGLEBOX_DEMO_BOT_READY_TIMEOUT_SECONDS
  unset SINGLEBOX_DEMO_BOT_READY_POLL_INTERVAL_SECONDS
  unset BCS_MOCK_USER_ID
  unset BCS_MOCK_USER_NICK_NAME
  unset CURL_ARGS_FILE
  unset CURL_RESPONSE_BODY
  unset CURL_RESPONSE_STATUS
  unset CURL_EXIT_CODE

  export PROJECT_ROOT="$ROOT"
  export SCRIPT_DIR="${ROOT}/scripts"
  export DEP_DIR="${SCRIPT_DIR}/.dependencies"
  export LOG_DIR="${DEP_DIR}/logs"
  export BCS_DIR="${ROOT}/src/bcs"
  export BCS_PORT="21000"
  mkdir -p "$LOG_DIR"

  log_info() { printf '[INFO] %s\n' "$*"; }
  log_warn() { printf '[WARN] %s\n' "$*"; }
  log_error() { printf '[ERROR] %s\n' "$*" >&2; }
  check_command() { command -v "$1" >/dev/null 2>&1; }
  backend_ready() { return 0; }
  bcs_ready() { return 0; }
}

install_verify_curl_stub() {
  local tmpbin="$1"
  cat > "${tmpbin}/curl" <<'SH'
#!/usr/bin/env bash
set -u
if [ -n "${CURL_ARGS_FILE:-}" ]; then
  printf '%s\n' "$*" > "$CURL_ARGS_FILE"
fi
output_file=""
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-o" ]; then
    shift
    output_file="$1"
  fi
  shift
done
[ -n "$output_file" ] || exit 90
printf '%s' "${CURL_RESPONSE_BODY:-}" > "$output_file"
printf '%s' "${CURL_RESPONSE_STATUS:-200}"
exit "${CURL_EXIT_CODE:-0}"
SH
  chmod +x "${tmpbin}/curl"
}

test_defaults_are_community_safe() {
  setup_env
  # shellcheck source=/dev/null
  source "$MODULE"
  demo_bot_defaults

  assert_eq "developer" "$DEMO_BOT_NAME" "bot name"
  assert_eq "Local demo bot for OpenOCB Singlebox" "$DEMO_BOT_DESC" "bot desc"
  assert_eq "mock-user" "$DEMO_ENTITY_ID" "entity id"
  assert_eq "staff" "$DEMO_ENTITY_TYPE" "entity type"
  assert_eq "openclaw" "$DEMO_ENGINE_TYPE" "engine type"
  assert_eq "personal" "$DEMO_BOT_TYPE" "bot type"
  assert_eq "normalCC" "$DEMO_TEMPLATE_TYPE" "template type"
}

test_env_overrides_are_used() {
  setup_env
  export SINGLEBOX_DEMO_BOT_NAME="product-manager"
  export SINGLEBOX_DEMO_ENTITY_ID="mock-pm"
  # shellcheck source=/dev/null
  source "$MODULE"
  demo_bot_defaults

  assert_eq "product-manager" "$DEMO_BOT_NAME" "override bot name"
  assert_eq "mock-pm" "$DEMO_ENTITY_ID" "override entity id"
}

test_bcs_bot_id_uses_backend_bot_and_entity_id() {
  setup_env
  # shellcheck source=/dev/null
  source "$MODULE"
  demo_bot_defaults
  assert_eq "default:mock-user" "$(demo_bot_bcs_bot_id "default")" "bcs bot id"
}

test_find_existing_parses_backend_list() {
  setup_env
  tmpbin="$(mktemp -d)"
  cat > "${tmpbin}/curl" <<'SH'
#!/usr/bin/env bash
printf '%s\n' '{"success":true,"data":{"bots":[{"bot_id":"default","bot_name":"developer","entity_id":"mock-user","is_delete":0,"status":"ACTIVE"}]}}'
SH
  chmod +x "${tmpbin}/curl"
  PATH="${tmpbin}:$PATH"

  # shellcheck source=/dev/null
  source "$MODULE"
  demo_bot_defaults
  assert_eq "default" "$(demo_bot_find_existing)" "existing bot id"
}

test_find_existing_ignores_malformed_response() {
  setup_env
  tmpbin="$(mktemp -d)"
  cat > "${tmpbin}/curl" <<'SH'
#!/usr/bin/env bash
printf '%s\n' '<html>bad gateway</html>'
SH
  chmod +x "${tmpbin}/curl"
  PATH="${tmpbin}:$PATH"

  # shellcheck source=/dev/null
  source "$MODULE"
  demo_bot_defaults
  err_file="$(mktemp)"
  assert_eq "" "$(demo_bot_find_existing 2>"$err_file")" "malformed list response should return empty"
  [ ! -s "$err_file" ] || fail "malformed list response should not print jq errors"
}

test_create_posts_to_local_backend() {
  setup_env
  tmpbin="$(mktemp -d)"
  cat > "${tmpbin}/curl" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" > "${CURL_ARGS_FILE}"
printf '%s\n' '{"success":true,"data":{"bot_id":"default"}}'
SH
  chmod +x "${tmpbin}/curl"
  PATH="${tmpbin}:$PATH"
  export CURL_ARGS_FILE="${tmpbin}/args.txt"

  # shellcheck source=/dev/null
  source "$MODULE"
  demo_bot_defaults
  assert_eq "default" "$(demo_bot_create)" "created bot id"
  grep -F 'http://127.0.0.1:8888/api/bots?user_id=mock-user' "$CURL_ARGS_FILE" >/dev/null || fail "backend create URL missing"
  grep -F 'x-user-id: mock-user' "$CURL_ARGS_FILE" >/dev/null || fail "x-user-id header missing"
}

test_create_ignores_malformed_response() {
  setup_env
  tmpbin="$(mktemp -d)"
  cat > "${tmpbin}/curl" <<'SH'
#!/usr/bin/env bash
printf '%s\n' '<html>bad gateway</html>'
SH
  chmod +x "${tmpbin}/curl"
  PATH="${tmpbin}:$PATH"

  # shellcheck source=/dev/null
  source "$MODULE"
  demo_bot_defaults
  err_file="$(mktemp)"
  assert_eq "" "$(demo_bot_create 2>"$err_file")" "malformed create response should return empty"
  [ ! -s "$err_file" ] || fail "malformed create response should not print jq errors"
}

test_create_handles_curl_failure_without_exiting() {
  setup_env
  tmpbin="$(mktemp -d)"
  cat > "${tmpbin}/curl" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "curl failed" >&2
exit 7
SH
  chmod +x "${tmpbin}/curl"
  PATH="${tmpbin}:$PATH"

  # shellcheck source=/dev/null
  source "$MODULE"
  demo_bot_defaults
  assert_eq "" "$(demo_bot_create)" "failed create should return empty bot id"
  grep -F 'curl failed' "${DEMO_BOT_LOG}" >/dev/null || fail "curl error should be logged"
}

test_wait_ready_polls_backend_status_success() {
  setup_env
  tmpbin="$(mktemp -d)"
  cat > "${tmpbin}/curl" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" > "${CURL_ARGS_FILE}"
printf '%s\n' '{"success":true,"data":{"bot_status":"ACTIVE","binding_status":"ACTIVE","is_ready":true}}'
SH
  chmod +x "${tmpbin}/curl"
  PATH="${tmpbin}:$PATH"
  export CURL_ARGS_FILE="${tmpbin}/args.txt"
  export SINGLEBOX_DEMO_BOT_READY_TIMEOUT_SECONDS="1"
  export SINGLEBOX_DEMO_BOT_READY_POLL_INTERVAL_SECONDS="0"

  # shellcheck source=/dev/null
  source "$MODULE"
  demo_bot_defaults
  demo_bot_wait_ready "default"
  grep -F 'http://127.0.0.1:8888/api/bots/default/status?owner_id=mock-user' "$CURL_ARGS_FILE" >/dev/null || fail "backend status URL missing"
}

test_wait_ready_fails_on_backend_failed_status() {
  setup_env
  tmpbin="$(mktemp -d)"
  cat > "${tmpbin}/curl" <<'SH'
#!/usr/bin/env bash
printf '%s\n' '{"success":true,"data":{"bot_status":"FAILED","binding_status":"FAILED","error_message":"adapter failed","is_ready":false}}'
SH
  chmod +x "${tmpbin}/curl"
  PATH="${tmpbin}:$PATH"
  export SINGLEBOX_DEMO_BOT_READY_TIMEOUT_SECONDS="1"
  export SINGLEBOX_DEMO_BOT_READY_POLL_INTERVAL_SECONDS="0"

  # shellcheck source=/dev/null
  source "$MODULE"
  demo_bot_defaults
  if demo_bot_wait_ready "default"; then
    fail "wait ready should fail when backend reports FAILED"
  fi
}

test_verify_uses_bcs_http_get() {
  setup_env
  tmpbin="$(mktemp -d)"
  install_verify_curl_stub "$tmpbin"
  PATH="${tmpbin}:$PATH"
  export CURL_ARGS_FILE="${tmpbin}/args.txt"
  export CURL_RESPONSE_BODY='{"bot_uuid":"default:mock-user","capabilities":{"name":"developer","summary":"Local demo bot for OpenOCB Singlebox"}}'

  # shellcheck source=/dev/null
  source "$MODULE"
  demo_bot_defaults
  demo_bot_verify_bcn "default"
  grep -F 'X-Mock-User-Id: 001' "$CURL_ARGS_FILE" >/dev/null || fail "mock user header missing"
  grep -F 'http://127.0.0.1:21000/bots/default:mock-user' "$CURL_ARGS_FILE" >/dev/null || fail "BCS bot URL missing"
}

test_verify_honors_custom_mock_identity() {
  setup_env
  tmpbin="$(mktemp -d)"
  install_verify_curl_stub "$tmpbin"
  PATH="${tmpbin}:$PATH"
  export CURL_ARGS_FILE="${tmpbin}/args.txt"
  export CURL_RESPONSE_BODY='{"bot_uuid":"default:mock-user","capabilities":{"name":"developer","summary":"Local demo bot for OpenOCB Singlebox"}}'
  export BCS_MOCK_USER_ID="custom-user"
  export BCS_MOCK_USER_NICK_NAME="Custom User"

  # shellcheck source=/dev/null
  source "$MODULE"
  demo_bot_defaults
  demo_bot_verify_bcn "default"
  grep -F 'X-Mock-User-Id: custom-user' "$CURL_ARGS_FILE" >/dev/null || fail "custom mock user header missing"
  grep -F 'X-Mock-Nick-Name: Custom User' "$CURL_ARGS_FILE" >/dev/null || fail "custom mock nickname header missing"
}

test_verify_fails_fast_when_mock_auth_is_rejected() {
  setup_env
  tmpbin="$(mktemp -d)"
  install_verify_curl_stub "$tmpbin"
  PATH="${tmpbin}:$PATH"
  export CURL_RESPONSE_STATUS="401"
  export CURL_RESPONSE_BODY='{"error":"Unauthorized"}'

  # shellcheck source=/dev/null
  source "$MODULE"
  demo_bot_defaults
  err_file="$(mktemp)"
  if demo_bot_verify_bcn "default" 2>"$err_file"; then
    fail "verification should fail when mock auth is rejected"
  else
    rc=$?
  fi
  assert_eq "2" "$rc" "auth rejection return code"
  grep -F 'mock human authentication must be enabled' "$err_file" >/dev/null || fail "auth rejection should explain the singlebox requirement"
}

test_verify_fails_fast_on_transport_error() {
  setup_env
  tmpbin="$(mktemp -d)"
  install_verify_curl_stub "$tmpbin"
  PATH="${tmpbin}:$PATH"
  export CURL_RESPONSE_STATUS="000"
  export CURL_EXIT_CODE="7"

  # shellcheck source=/dev/null
  source "$MODULE"
  demo_bot_defaults
  if demo_bot_verify_bcn "default" 2>/dev/null; then
    fail "verification should fail on a transport error"
  else
    rc=$?
  fi
  assert_eq "2" "$rc" "transport error return code"
}

test_verify_fails_fast_on_server_error() {
  setup_env
  tmpbin="$(mktemp -d)"
  install_verify_curl_stub "$tmpbin"
  PATH="${tmpbin}:$PATH"
  export CURL_RESPONSE_STATUS="500"
  export CURL_RESPONSE_BODY='{"error":"internal"}'

  # shellcheck source=/dev/null
  source "$MODULE"
  demo_bot_defaults
  if demo_bot_verify_bcn "default" 2>/dev/null; then
    fail "verification should fail on a BCS server error"
  else
    rc=$?
  fi
  assert_eq "2" "$rc" "server error return code"
}

test_verify_rejects_invalid_json() {
  setup_env
  tmpbin="$(mktemp -d)"
  install_verify_curl_stub "$tmpbin"
  PATH="${tmpbin}:$PATH"
  export CURL_RESPONSE_BODY='<html>bad gateway</html>'

  # shellcheck source=/dev/null
  source "$MODULE"
  demo_bot_defaults
  if demo_bot_verify_bcn "default" 2>/dev/null; then
    fail "verification should reject invalid JSON"
  else
    rc=$?
  fi
  assert_eq "2" "$rc" "invalid JSON return code"
}

test_verify_rejects_wrong_bot_identity() {
  setup_env
  tmpbin="$(mktemp -d)"
  install_verify_curl_stub "$tmpbin"
  PATH="${tmpbin}:$PATH"
  export CURL_RESPONSE_BODY='{"bot_uuid":"other:mock-user","capabilities":{"name":"developer","summary":"Local demo bot for OpenOCB Singlebox"}}'

  # shellcheck source=/dev/null
  source "$MODULE"
  demo_bot_defaults
  if demo_bot_verify_bcn "default" 2>/dev/null; then
    fail "verification should reject a different bot UUID"
  else
    rc=$?
  fi
  assert_eq "2" "$rc" "wrong bot UUID return code"
}

test_verify_reports_missing_bot_as_repairable() {
  setup_env
  tmpbin="$(mktemp -d)"
  install_verify_curl_stub "$tmpbin"
  PATH="${tmpbin}:$PATH"
  export CURL_RESPONSE_STATUS="404"
  export CURL_RESPONSE_BODY='{"error":"Not Found"}'

  # shellcheck source=/dev/null
  source "$MODULE"
  demo_bot_defaults
  if demo_bot_verify_bcn "default"; then
    fail "verification should report a missing bot"
  else
    rc=$?
  fi
  assert_eq "1" "$rc" "missing bot return code"
}

test_connect_posts_fixed_bcs_bot_id() {
  setup_env
  tmpbin="$(mktemp -d)"
  cat > "${tmpbin}/curl" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" > "${CURL_ARGS_FILE}"
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-d" ]; then
    shift
    printf '%s\n' "$1" > "${CURL_BODY_FILE}"
    break
  fi
  shift
done
printf '%s\n' '{"is_new":true,"bot_uuid":"default:mock-user","token":"test-token"}'
SH
  chmod +x "${tmpbin}/curl"
  PATH="${tmpbin}:$PATH"
  export CURL_ARGS_FILE="${tmpbin}/args.txt"
  export CURL_BODY_FILE="${tmpbin}/body.json"

  # shellcheck source=/dev/null
  source "$MODULE"
  demo_bot_defaults
  demo_bot_connect_bcs "default"
  grep -F 'http://127.0.0.1:21000/bots/connect' "$CURL_ARGS_FILE" >/dev/null || fail "BCS connect URL missing"
  jq -e '.bot_id == "default:mock-user" and .protocol_version == 2' "$CURL_BODY_FILE" >/dev/null || fail "BCS connect body mismatch"
}

test_admin_onboard_posts_demo_metadata() {
  setup_env
  tmpbin="$(mktemp -d)"
  cat > "${tmpbin}/curl" <<'SH'
#!/usr/bin/env bash
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-d" ]; then
    shift
    printf '%s\n' "$1" > "${CURL_BODY_FILE}"
    break
  fi
  shift
done
printf '%s\n' '{"bot_uuid":"default:mock-user","onboarded":true,"name":"developer"}'
SH
  chmod +x "${tmpbin}/curl"
  PATH="${tmpbin}:$PATH"
  export CURL_BODY_FILE="${tmpbin}/body.json"

  # shellcheck source=/dev/null
  source "$MODULE"
  demo_bot_defaults
  demo_bot_admin_onboard_bcs "default"
  jq -e '.bot_id == "default:mock-user" and .name == "developer" and .summary == "Local demo bot for OpenOCB Singlebox" and .hidden == true' "$CURL_BODY_FILE" >/dev/null || fail "admin onboard body mismatch"
}

test_ensure_skips_connect_when_already_visible() {
  setup_env
  tmpbin="$(mktemp -d)"
  install_verify_curl_stub "$tmpbin"
  PATH="${tmpbin}:$PATH"
  export CURL_RESPONSE_BODY='{"bot_uuid":"default:mock-user","capabilities":{"name":"developer","summary":"Local demo bot for OpenOCB Singlebox"}}'

  # shellcheck source=/dev/null
  source "$MODULE"
  demo_bot_defaults
  demo_bot_ensure_bcn "default"
}

test_ensure_onboards_minimal_runtime_entry() {
  setup_env
  # shellcheck source=/dev/null
  source "$MODULE"
  demo_bot_defaults
  : > "${DEMO_BOT_LOG}"

  sequence_file="$(mktemp)"
  verify_count=0
  demo_bot_verify_bcn() {
    verify_count=$((verify_count + 1))
    printf '%s\n' "verify" >> "$sequence_file"
    if [ "$verify_count" -eq 1 ]; then
      return 1
    fi
    return 0
  }
  demo_bot_connect_bcs() {
    printf '%s\n' "connect:$1" >> "$sequence_file"
  }
  demo_bot_admin_onboard_bcs() {
    printf '%s\n' "onboard:$1" >> "$sequence_file"
  }

  demo_bot_ensure_bcn "default"
  assert_eq $'verify\nonboard:default\nverify' "$(cat "$sequence_file")" "minimal runtime entry should still onboard"
}

test_ensure_onboards_without_connect_when_bcs_entry_missing() {
  setup_env
  # shellcheck source=/dev/null
  source "$MODULE"
  demo_bot_defaults
  : > "${DEMO_BOT_LOG}"

  sequence_file="$(mktemp)"
  verify_count=0
  demo_bot_verify_bcn() {
    verify_count=$((verify_count + 1))
    printf '%s\n' "verify" >> "$sequence_file"
    if [ "$verify_count" -eq 1 ]; then
      return 1
    fi
    return 0
  }
  demo_bot_connect_bcs() {
    printf '%s\n' "connect:$1" >> "$sequence_file"
    return 1
  }
  demo_bot_admin_onboard_bcs() {
    printf '%s\n' "onboard:$1" >> "$sequence_file"
  }

  demo_bot_ensure_bcn "default"
  assert_eq $'verify\nonboard:default\nverify' "$(cat "$sequence_file")" "missing BCS entry should onboard without pre-connect"
}

test_ensure_does_not_onboard_after_fatal_verification_error() {
  setup_env
  # shellcheck source=/dev/null
  source "$MODULE"
  demo_bot_defaults

  sequence_file="$(mktemp)"
  demo_bot_verify_bcn() {
    printf '%s\n' "verify" >> "$sequence_file"
    return 2
  }
  demo_bot_admin_onboard_bcs() {
    printf '%s\n' "onboard:$1" >> "$sequence_file"
  }

  if demo_bot_ensure_bcn "default"; then
    fail "fatal verification error should fail without onboarding"
  else
    rc=$?
  fi
  assert_eq "2" "$rc" "fatal verification return code"
  assert_eq "verify" "$(cat "$sequence_file")" "fatal verification should not mutate BCS"
}

test_ensure_fails_when_metadata_is_still_wrong_after_onboard() {
  setup_env
  # shellcheck source=/dev/null
  source "$MODULE"
  demo_bot_defaults

  sequence_file="$(mktemp)"
  demo_bot_verify_bcn() {
    printf '%s\n' "verify" >> "$sequence_file"
    return 1
  }
  demo_bot_admin_onboard_bcs() {
    printf '%s\n' "onboard:$1" >> "$sequence_file"
  }

  if demo_bot_ensure_bcn "default"; then
    fail "ensure should fail when post-onboard metadata still mismatches"
  else
    rc=$?
  fi
  assert_eq "1" "$rc" "persistent metadata mismatch return code"
  assert_eq $'verify\nonboard:default\nverify' "$(cat "$sequence_file")" "post-onboard verification sequence"
}

test_start_waits_for_backend_ready_before_bcn_onboard() {
  setup_env
  # shellcheck source=/dev/null
  source "$MODULE"
  sequence_file="$(mktemp)"

  demo_bot_find_existing() { return 0; }
  demo_bot_create() {
    printf '%s\n' "create" >> "$sequence_file"
    printf '%s\n' "default"
  }
  demo_bot_wait_ready() {
    printf '%s\n' "wait:$1" >> "$sequence_file"
  }
  demo_bot_ensure_bcn() {
    printf '%s\n' "ensure:$1" >> "$sequence_file"
  }

  demo_bot_start
  assert_eq $'create\nwait:default\nensure:default' "$(cat "$sequence_file")" "demo bot start order"
}

test_all_order_includes_bots_and_demo_before_frontend() {
  setup_env
  # shellcheck source=/dev/null
  source "${ROOT}/scripts/modules/all.sh"
  assert_eq "baas backend bcs bots demo_bot frontend" "${START_ORDER[*]}" "all start order"
  assert_eq "frontend demo_bot bots bcs backend baas" "${STOP_ORDER[*]}" "all stop order"
}

test_backend_ready_function_exists() {
  setup_env
  # shellcheck source=/dev/null
  source "${ROOT}/scripts/modules/backend.sh"
  assert_eq "function" "$(type -t backend_ready)" "backend_ready type"
}

test_defaults_are_community_safe
test_env_overrides_are_used
test_bcs_bot_id_uses_backend_bot_and_entity_id
test_find_existing_parses_backend_list
test_find_existing_ignores_malformed_response
test_create_posts_to_local_backend
test_create_ignores_malformed_response
test_create_handles_curl_failure_without_exiting
test_wait_ready_polls_backend_status_success
test_wait_ready_fails_on_backend_failed_status
test_verify_uses_bcs_http_get
test_verify_honors_custom_mock_identity
test_verify_fails_fast_when_mock_auth_is_rejected
test_verify_fails_fast_on_transport_error
test_verify_fails_fast_on_server_error
test_verify_rejects_invalid_json
test_verify_rejects_wrong_bot_identity
test_verify_reports_missing_bot_as_repairable
test_connect_posts_fixed_bcs_bot_id
test_admin_onboard_posts_demo_metadata
test_ensure_skips_connect_when_already_visible
test_ensure_onboards_minimal_runtime_entry
test_ensure_onboards_without_connect_when_bcs_entry_missing
test_ensure_does_not_onboard_after_fatal_verification_error
test_ensure_fails_when_metadata_is_still_wrong_after_onboard
test_start_waits_for_backend_ready_before_bcn_onboard
test_all_order_includes_bots_and_demo_before_frontend
test_backend_ready_function_exists

printf 'PASS: singlebox demo bot module tests\n'
