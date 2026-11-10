#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="${ROOT}/singlebox/ci/singlebox_coverage.sh"
REPORTER="${ROOT}/singlebox/ci/singlebox_coverage_report.py"
MANIFEST_CHECKER="${ROOT}/singlebox/ci/singlebox_coverage_manifest_check.py"
MANIFEST="${ROOT}/singlebox/ci/singlebox_coverage_modules.yaml"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

make_fake_repo() {
  local tmp="$1"
  mkdir -p "${tmp}/singlebox/ci"
  mkdir -p \
    "${tmp}/apps/backend/tests/community/acceptance/devices" \
    "${tmp}/apps/backend/tests/community/acceptance/cron" \
    "${tmp}/apps/backend/src/agentclaw/community/core/devices" \
    "${tmp}/apps/backend/src/agentclaw/community/core/cron" \
    "${tmp}/apps/backend/src/agentclaw/community/plugin_api" \
    "${tmp}/apps/baas" \
    "${tmp}/apps/bcs/scripts/e2e-test"
  cp "$SCRIPT" "${tmp}/singlebox/ci/singlebox_coverage.sh"
  cp "$REPORTER" "${tmp}/singlebox/ci/singlebox_coverage_report.py"
  cp "$MANIFEST_CHECKER" "${tmp}/singlebox/ci/singlebox_coverage_manifest_check.py"
  cat > "${tmp}/singlebox/ci/singlebox_coverage_modules.yaml" <<'YAML'
modules:
  devices:
    acceptance_targets:
      - tests/community/acceptance/devices
    system: backend
    core_paths:
      - src/agentclaw/community/core/devices/
    router_api:
      items:
        - GET /api/v1/devices
        - GET /api/v1/devices/{binding_id:int}
        - GET /api/v1/devices/by-id/{device_id}
    plugin_api:
      status: not_applicable
      reason: No device plugin denominator.
      items: []
    thresholds:
      core_min_percent: 40
      router_min_percent: 30
  cron:
    acceptance_targets:
      - tests/community/acceptance/cron
    system: backend
    core_paths:
      - src/agentclaw/community/core/cron/
    router_api:
      items:
        - GET /api/cron
    plugin_api:
      status: not_applicable
      reason: No cron plugin denominator in this fixture.
      items: []
    thresholds:
      core_min_percent: 40
      router_min_percent: 100
YAML
  chmod +x "${tmp}/singlebox/ci/singlebox_coverage.sh"
  # Provisioning is network-free in this orchestration test; the helper's
  # real HTTP, security and parsing behavior has its own focused unit suite.
  cat > "${tmp}/singlebox/ci/collaboration_repair_provider.py" <<'PYTEST'
import argparse
import json
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("action")
parser.add_argument("--bcs-url")
parser.add_argument("--mock-url")
parser.add_argument("--fixture", type=Path, required=True)
args = parser.parse_args()
assert os.environ["SINGLEBOX_COVERAGE"] == "1"
assert args.action == "provision"
assert args.bcs_url == "http://127.0.0.1:21000"
assert args.mock_url == "http://127.0.0.1:39090"
args.fixture.write_text(json.dumps({"provider_id": "prv_fixture", "provider_admin_token": "test-token"}))
args.fixture.chmod(0o600)
PYTEST
  mkdir -p "${tmp}/apps/bcs/scripts/ci"
  cat > "${tmp}/apps/bcs/scripts/ci/companion_coverage.py" <<'PYTEST'
import argparse
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument("--bcs-dir")
parser.add_argument("--report-dir", type=Path)
args = parser.parse_args()
args.report_dir.mkdir(parents=True, exist_ok=True)
(args.report_dir / "companion-timings.json").write_text('{"status":"passed"}')
PYTEST
  cat > "${tmp}/apps/bcs/scripts/e2e-test/mock_services.sh" <<'SH'
#!/usr/bin/env bash
bcs_e2e_mock_start() {
  BCS_E2E_MOCK_BASE_URL="http://127.0.0.1:39090"
  BCS_E2E_JUDGE_API_KEY="local-e2e-key"
  export BCS_E2E_MOCK_BASE_URL BCS_E2E_JUDGE_API_KEY
}
bcs_e2e_mock_stop() { :; }
SH
  cat > "${tmp}/singlebox/singlebox.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
case ":${PATH}:" in
  *":${HOME}/.cargo/bin:"*) ;;
  *) echo "missing cargo path in PATH" >&2; exit 11 ;;
esac
if [ "${SINGLEBOX_MODEL_CONFIG_MODE:-}" != "${SINGLEBOX_EXPECTED_MODEL_CONFIG_MODE:-mock}" ]; then
  echo "singlebox coverage model config mode mismatch" >&2
  exit 12
fi
if [ "${BCS_E2E_MOCK_BASE_URL:-}" != "http://127.0.0.1:39090" ]; then
  echo "singlebox coverage should expose the Provider/Judge mock to BCS" >&2
  exit 16
fi
case "${STANDALONE_OPENCLAW_ROOT:-}" in
  "${SINGLEBOX_EXPECTED_COVERAGE_ROOT:-${PWD}/singlebox/.dependencies/coverage/singlebox}/standalone-openclaw") ;;
  *) echo "singlebox coverage should use an isolated standalone OpenClaw root" >&2; exit 13 ;;
esac
case "${STANDALONE_RUNTIME_DIR:-}" in
  "${SINGLEBOX_EXPECTED_COVERAGE_ROOT:-${PWD}/singlebox/.dependencies/coverage/singlebox}/standalone-runtime") ;;
  *) echo "singlebox coverage should use an isolated standalone runtime dir" >&2; exit 14 ;;
esac
printf '%s\n' "$*" >> "${SINGLEBOX_STUB_LOG:?}"
if [ "$*" = "--standalone setup all" ] && [ "${BCS_SKIP_DEBUG_BUILD:-}" != "1" ]; then
  echo "singlebox coverage setup should skip the unused non-instrumented BCS build" >&2
  exit 17
fi
if [ "$*" = "--standalone --with-bcs-coverage restart bcs" ]; then
  [ "${BCS_BIN:-}" = "${PWD}/apps/bcs/target/cov-e2e/llvm-cov-target/debug/bcs" ] || exit 24
  [ "${BCS_CLI_BIN:-}" = "${PWD}/apps/bcs/target/cov-e2e/llvm-cov-target/debug/bcs-cli" ] || exit 25
  [ "${LLVM_PROFILE_FILE:-}" = "${PWD}/apps/bcs/target/cov-e2e/llvm-cov-target/bcs-%m-%p.profraw" ] || exit 26
  [ "${SINGLEBOX_COVERAGE:-}" = "1" ] || exit 18
  [ -s "${SINGLEBOX_ACCEPTANCE_REPAIR_PROVIDER_FILE:?}" ] || exit 19
  [ ! -f "${PWD}/fail-bcs-restart" ] || exit 20
fi
if [ "$*" = "--standalone --with-bcs-coverage start all" ]; then
  mkdir -p "${SINGLEBOX_COVERAGE_DIR:?}/backend" "${SINGLEBOX_COVERAGE_DIR:?}/baas"
  printf '%s\n' '{}' > "${SINGLEBOX_COVERAGE_DIR}/backend/.coverage.fake"
  printf '%s\n' '{}' > "${SINGLEBOX_COVERAGE_DIR}/baas/.coverage.fake"
  if [ "${SINGLEBOX_STUB_DEVICE_HITS:-}" = "1" ]; then
    cat > "${SINGLEBOX_COVERAGE_DIR}/backend/router_hits.jsonl" <<'JSONL'
{"key":"GET /api/v1/devices"}
{"key":"GET /api/v1/devices/{binding_id:int}"}
{"key":"GET /api/v1/devices/by-id/{device_id}"}
{"key":"GET /api/v1/devices/{binding_id:int}/connection"}
{"key":"GET /api/v1/devices/connectable"}
{"key":"GET /api/v1/devices/{binding_id:int}/instances"}
{"key":"POST /api/v1/devices/{binding_id:int}/restart"}
{"key":"POST /api/v1/devices/{binding_id:int}/release"}
JSONL
  else
    cat > "${SINGLEBOX_COVERAGE_DIR}/backend/router_hits.jsonl" <<'JSONL'
{"key":"GET /api/v1/devices"}
{"key":"GET /api/cron"}
JSONL
  fi
  printf '%s\n' '{"key":"GET /health"}' > "${SINGLEBOX_COVERAGE_DIR}/baas/router_hits.jsonl"
fi
exit 0
SH
  chmod +x "${tmp}/singlebox/singlebox.sh"
cat > "${tmp}/apps/bcs/scripts/e2e_coverage.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
expected_bot_data_dir="${SINGLEBOX_EXPECTED_COVERAGE_ROOT:-${PWD}/singlebox/.dependencies/coverage/singlebox}/standalone-openclaw/profiles"
if [[ "${BCS_E2E_CHANGED_LINE_MIN:-}" != "65" ]]; then
  echo "BCS E2E should enforce changed-line coverage above 65%" >&2
  exit 17
fi
if [[ "${BCS_BOTS_DATA_DIR:-}" != "$expected_bot_data_dir" ]]; then
  echo "BCS E2E should read bot sessions from the coverage standalone profile root" >&2
  exit 15
fi
printf '%s\n' "$*" >> "${PWD}/bcs-e2e.log"
if [[ "$*" == *--shared-tests-only* ]]; then
  [[ ! -f "${PWD}/fail-bcs-e2e" ]] || exit 7
  [[ ! -f "${PWD}/fail-bcs-e2e-without-artifacts" ]] || exit 9
  [[ ! -f "${PWD}/fail-bcs-e2e-with-malformed-artifacts" ]] || exit 8
  exit 0
fi
cov_dir="${PWD}/apps/bcs/target/cov-e2e"
[[ -f "${PWD}/fail-bcs-e2e-without-artifacts" ]] && exit 9
mkdir -p "$cov_dir"
cat > "$cov_dir/summary.json" <<'JSON'
{"data":[{"totals":{"lines":{"covered":45,"count":100},"functions":{"covered":40,"count":100},"regions":{"covered":30,"count":100},"branches":{"covered":0,"count":0}}}]}
JSON
cat > "$cov_dir/oauth_e2e.json" <<'JSON'
{"status":"passed","scenarios":["mixed-sqlite","mixed-memory","compatibility-and-chain-selection","pending-capacity","startup-validation"],"checks":[{"path":"/auth/url","status":200}],"provider_calls":{"authorize":1,"exchange":1,"userinfo":1}}
JSON
printf '%s\n' '<coverage line-rate="0.45"></coverage>' > "$cov_dir/cobertura.xml"
printf '%s\n' 'TOTAL 100 55 45.00%' > "$cov_dir/coverage.txt"
printf '%s\n' '<endpointCoverage><overall covered="12" total="12" uncovered="0" percent="100.0"/></endpointCoverage>' > "$cov_dir/endpoint_coverage.xml"
printf '%s\n' 'Endpoint coverage: 12 / 12 (100.0%)' > "$cov_dir/endpoint_coverage.txt"
printf '%s\n' 'bcs-cli leaf command coverage: 8 / 8 (100.0%)' > "$cov_dir/cli_command_coverage.txt"
if [[ -f "${PWD}/fail-bcs-e2e-with-malformed-artifacts" ]]; then
  printf '%s\n' '{"data":[{"totals":{"lines":[],"functions":40}}]}' > "$cov_dir/summary.json"
  exit 8
fi
[[ -f "${PWD}/fail-bcs-e2e" ]] && exit 7
exit 0
SH
  chmod +x "${tmp}/apps/bcs/scripts/e2e_coverage.sh"
  mkdir -p "${tmp}/fake-bin"
  cat > "${tmp}/fake-bin/uv" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "${UV_STUB_LOG:?}"
if [ "${1:-}" = "run" ] && [ "${2:-}" = "pytest" ]; then
  [ -s "${SINGLEBOX_ACCEPTANCE_REPAIR_PROVIDER_FILE:?}" ] || exit 23
  junit=""
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --junitxml=*) junit="${1#--junitxml=}" ;;
      --junitxml)
        junit="$2"
        shift
        ;;
    esac
    shift
  done
  [ -n "$junit" ] || exit 21
  mkdir -p "$(dirname "$junit")"
  printf '%s\n' '<testsuite tests="1" failures="0"></testsuite>' > "$junit"
  printf '%s\n' '1 passed'
  exit 0
fi
if [ "${1:-}" = "run" ] && [ "${2:-}" = "coverage" ]; then
  case "${3:-}" in
    combine)
      [ -n "${COVERAGE_FILE:-}" ] || exit 22
      mkdir -p "$(dirname "$COVERAGE_FILE")"
      printf '%s\n' 'combined' > "$COVERAGE_FILE"
      ;;
    json)
      output=""
      while [ "$#" -gt 0 ]; do
        if [ "$1" = "-o" ]; then
          output="$2"
          break
        fi
        shift
      done
      [ -n "$output" ] || exit 23
      mkdir -p "$(dirname "$output")"
      printf '%s\n' '{"totals":{"percent_covered":100},"files":{"src/agentclaw/community/core/devices/services/device_service.py":{"summary":{"covered_lines":50,"num_statements":100}},"src/agentclaw/community/core/cron/services/cron_service.py":{"summary":{"covered_lines":50,"num_statements":100}}}}' > "$output"
      ;;
    html)
      output_dir=""
      while [ "$#" -gt 0 ]; do
        if [ "$1" = "-d" ]; then
          output_dir="$2"
          break
        fi
        shift
      done
      [ -n "$output_dir" ] || exit 24
      mkdir -p "$output_dir"
      printf '%s\n' '<html>coverage</html>' > "$output_dir/index.html"
      ;;
    report)
      printf '%s\n' 'Name Stmts Miss Cover'
      ;;
    *)
      exit 25
      ;;
  esac
  exit 0
fi
exit 26
SH
  chmod +x "${tmp}/fake-bin/uv"
}

test_default_mode_runs_real_singlebox() {
  local tmp log bcs_log
  tmp="$(mktemp -d)"
  log="${tmp}/singlebox.log"
  bcs_log="${tmp}/bcs-e2e.log"
  uv_log="${tmp}/uv.log"
  make_fake_repo "$tmp"

  (
    cd "$tmp"
    PATH="${tmp}/fake-bin:$PATH" \
      PYTHON="${ROOT}/apps/backend/.venv/bin/python" \
      SINGLEBOX_STUB_LOG="$log" \
      UV_STUB_LOG="$uv_log" \
      "${tmp}/singlebox/ci/singlebox_coverage.sh" >/dev/null
  )

  [ ! -e "${tmp}/singlebox/.dependencies/coverage/singlebox/collaboration-repair-provider.json" ] || \
    fail "normal cleanup must remove Provider credentials before artifacts are uploaded"
  grep -Fx -- "--standalone --with-bcs-coverage start all" "$log" >/dev/null || \
    fail "default coverage mode should start instrumented BCS in the full standalone stack"
  grep -Fx -- "--standalone --with-bcs-coverage restart bcs" "$log" >/dev/null || \
    fail "coverage should restart only BCS after Provider provisioning"
  grep -Fx -- "--standalone stop all" "$log" >/dev/null || \
    fail "default coverage mode should stop the full standalone singlebox stack"
  grep -F "run pytest tests/community/acceptance/devices tests/community/acceptance/cron" "$uv_log" >/dev/null || \
    fail "default coverage mode should run every manifest acceptance target"
  [ "$(grep -Fc -- '--standalone --with-bcs-coverage start all' "$log")" -eq 1 ] || \
    fail "all modules should share one singlebox startup"
  grep -Fx -- "--skip-start --shared-tests-only --bcs-line-min 54 --bcs-method-min 48" "$bcs_log" >/dev/null || \
    fail "default coverage mode should run BCS E2E against the shared stack"
  grep -F "run coverage combine" "$uv_log" >/dev/null || \
    fail "default coverage mode should combine coverage"
  [ -s "${tmp}/singlebox/.dependencies/coverage/singlebox/reports/summary.json" ] || \
    fail "summary.json artifact missing"
  [ -s "${tmp}/singlebox/.dependencies/coverage/singlebox/reports/summary.md" ] || \
    fail "summary.md artifact missing"
  [ -s "${tmp}/singlebox/.dependencies/coverage/singlebox/reports/dashboard.html" ] || \
    fail "dashboard.html artifact missing"
  for artifact in e2e.log oauth_e2e.json cobertura.xml coverage.txt summary.json endpoint_coverage.xml endpoint_coverage.txt cli_command_coverage.txt; do
    [ -s "${tmp}/singlebox/.dependencies/coverage/singlebox/reports/bcs/${artifact}" ] || \
      fail "BCS artifact missing: ${artifact}"
  done
  "${ROOT}/apps/backend/.venv/bin/python" - "${tmp}/singlebox/.dependencies/coverage/singlebox/reports/summary.json" <<'PY'
import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
assert list(summary["modules"]) == ["devices", "cron"]
assert summary["systems"]["bcs"]["runtime_line"]["percent"] == 45.0
assert summary["systems"]["bcs"]["method"]["percent"] == 40.0
assert summary["systems"]["bcs"]["router_api"]["percent"] == 100.0
assert summary["systems"]["bcs"]["cli_command"]["percent"] == 100.0
PY
}

test_failed_restart_trap_removes_provider_fixture() {
  local tmp log uv_log rc
  tmp="$(mktemp -d)"
  log="${tmp}/singlebox.log"
  uv_log="${tmp}/uv.log"
  make_fake_repo "$tmp"
  touch "${tmp}/fail-bcs-restart"
  set +e
  (
    cd "$tmp"
    PATH="${tmp}/fake-bin:$PATH" \
      PYTHON="${ROOT}/apps/backend/.venv/bin/python" \
      SINGLEBOX_STUB_LOG="$log" UV_STUB_LOG="$uv_log" \
      "${tmp}/singlebox/ci/singlebox_coverage.sh" >/dev/null 2>&1
  )
  rc=$?
  set -e
  [ "$rc" -eq 20 ] || fail "BCS restart failure must retain its exit status"
  [ ! -e "${tmp}/singlebox/.dependencies/coverage/singlebox/collaboration-repair-provider.json" ] || \
    fail "EXIT trap must remove Provider credentials after failed startup"
  grep -Fx -- "--standalone stop all" "$log" >/dev/null || \
    fail "failed startup must stop the stack before artifact collection"
}

test_model_config_mode_can_use_home() {
  local tmp log uv_log summary
  tmp="$(mktemp -d)"
  log="${tmp}/singlebox.log"
  uv_log="${tmp}/uv.log"
  summary="${tmp}/singlebox/.dependencies/coverage/singlebox/reports/summary.json"
  make_fake_repo "$tmp"

  (
    cd "$tmp"
    PATH="${tmp}/fake-bin:$PATH" \
      PYTHON="${ROOT}/apps/backend/.venv/bin/python" \
      SINGLEBOX_COVERAGE_MODEL_CONFIG_MODE=home \
      SINGLEBOX_EXPECTED_MODEL_CONFIG_MODE=home \
      SINGLEBOX_STUB_LOG="$log" \
      UV_STUB_LOG="$uv_log" \
      "${tmp}/singlebox/ci/singlebox_coverage.sh" >/dev/null
  )

  "${ROOT}/apps/backend/.venv/bin/python" - "$summary" <<'PY'
import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
assert summary["model_config_mode"] == "home"
PY
}

test_bcs_e2e_failure_preserves_reports_and_fails_gate() {
  local tmp log bcs_log uv_log output rc
  tmp="$(mktemp -d)"
  log="${tmp}/singlebox.log"
  bcs_log="${tmp}/bcs-e2e.log"
  uv_log="${tmp}/uv.log"
  make_fake_repo "$tmp"
  touch "${tmp}/fail-bcs-e2e"

  set +e
  output="$({
    cd "$tmp"
    PATH="${tmp}/fake-bin:$PATH" \
      PYTHON="${ROOT}/apps/backend/.venv/bin/python" \
      SINGLEBOX_STUB_LOG="$log" UV_STUB_LOG="$uv_log" \
      "${tmp}/singlebox/ci/singlebox_coverage.sh"
  } 2>&1)"
  rc=$?
  set -e

  [ "$rc" -eq 7 ] || fail "BCS E2E failure should be the final gate status"
  [ ! -e "${tmp}/singlebox/.dependencies/coverage/singlebox/collaboration-repair-provider.json" ] || \
    fail "failed coverage cleanup must remove Provider credentials"
  grep -F "BCS e2e failed with exit code 7" <<<"$output" >/dev/null || \
    fail "BCS E2E failure should be reported"
  [ -s "${tmp}/singlebox/.dependencies/coverage/singlebox/reports/backend-coverage.json" ] || \
    fail "Backend coverage should still be generated after BCS E2E failure"
  [ -s "${tmp}/singlebox/.dependencies/coverage/singlebox/reports/bcs/e2e.log" ] || \
    fail "BCS E2E log should be preserved after failure"
}

test_bcs_e2e_failure_without_artifacts_preserves_original_status() {
  local tmp log uv_log output rc summary
  tmp="$(mktemp -d)"
  log="${tmp}/singlebox.log"
  uv_log="${tmp}/uv.log"
  make_fake_repo "$tmp"
  touch "${tmp}/fail-bcs-e2e-without-artifacts"

  set +e
  output="$({
    cd "$tmp"
    PATH="${tmp}/fake-bin:$PATH" \
      PYTHON="${ROOT}/apps/backend/.venv/bin/python" \
      SINGLEBOX_STUB_LOG="$log" UV_STUB_LOG="$uv_log" \
      "${tmp}/singlebox/ci/singlebox_coverage.sh"
  } 2>&1)"
  rc=$?
  set -e

  [ "$rc" -eq 9 ] || fail "missing BCS artifacts should not replace the original E2E status"
  grep -F "BCS e2e failed with exit code 9" <<<"$output" >/dev/null || \
    fail "missing-artifact BCS failure should report the original status"
  summary="${tmp}/singlebox/.dependencies/coverage/singlebox/reports/summary.json"
  [ -s "$summary" ] || fail "top-level summary should survive missing BCS artifacts"
  "${ROOT}/apps/backend/.venv/bin/python" - "$summary" <<'PY'
import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
assert summary["status"] == "failed"
assert summary["systems"]["bcs"]["e2e_status"] == "failed"
assert summary["systems"]["bcs"]["artifact_errors"]
PY
}

test_bcs_e2e_failure_with_malformed_artifacts_preserves_original_status() {
  local tmp log uv_log output rc summary
  tmp="$(mktemp -d)"
  log="${tmp}/singlebox.log"
  uv_log="${tmp}/uv.log"
  make_fake_repo "$tmp"
  touch "${tmp}/fail-bcs-e2e-with-malformed-artifacts"

  set +e
  output="$({
    cd "$tmp"
    PATH="${tmp}/fake-bin:$PATH" \
      PYTHON="${ROOT}/apps/backend/.venv/bin/python" \
      SINGLEBOX_STUB_LOG="$log" UV_STUB_LOG="$uv_log" \
      "${tmp}/singlebox/ci/singlebox_coverage.sh"
  } 2>&1)"
  rc=$?
  set -e

  [ "$rc" -eq 8 ] || fail "malformed BCS artifacts should not replace the original E2E status"
  summary="${tmp}/singlebox/.dependencies/coverage/singlebox/reports/summary.json"
  [ -s "$summary" ] || fail "top-level summary should survive malformed BCS artifacts"
  "${ROOT}/apps/backend/.venv/bin/python" - "$summary" <<'PY'
import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
assert summary["systems"]["bcs"]["runtime_line"]["total"] == 0
assert summary["systems"]["bcs"]["artifact_errors"]
PY
}

test_mock_mode_is_not_supported() {
  local tmp output rc
  tmp="$(mktemp -d)"
  make_fake_repo "$tmp"

  set +e
  output="$("${tmp}/singlebox/ci/singlebox_coverage.sh" --mode mock 2>&1)"
  rc=$?
  set -e

  [ "$rc" -ne 0 ] || fail "mock mode should be rejected"
  grep -F "unknown singlebox coverage mode: mock" <<<"$output" >/dev/null || \
    fail "mock mode rejection message mismatch"
}

test_module_mode_reports_device_metrics() {
  local tmp log uv_log summary
  tmp="$(mktemp -d)"
  log="${tmp}/singlebox.log"
  uv_log="${tmp}/uv.log"
  make_fake_repo "$tmp"

  (
    cd "$tmp"
    PATH="${tmp}/fake-bin:$PATH" \
      PYTHON="${ROOT}/apps/backend/.venv/bin/python" \
      SINGLEBOX_STUB_LOG="$log" \
      SINGLEBOX_STUB_DEVICE_HITS=1 \
      UV_STUB_LOG="$uv_log" \
      "${tmp}/singlebox/ci/singlebox_coverage.sh" \
        --module devices >/dev/null
  )

  grep -F "run pytest tests/community/acceptance/devices" "$uv_log" >/dev/null || \
    fail "selected devices acceptance target was not executed"
  summary="${tmp}/singlebox/.dependencies/coverage/singlebox/reports/summary.json"
  "${ROOT}/apps/backend/.venv/bin/python" - "$summary" <<'PY'
import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
devices = summary["modules"]["devices"]
assert devices["core"]["percent"] == 50.0
assert devices["router_api"]["covered"] == 3
assert devices["router_api"]["total"] == 3
assert devices["plugin_api"]["status"] == "not_applicable"
PY
}

test_relative_coverage_root_is_absolutized() {
  local tmp output
  tmp="$(mktemp -d)"
  make_fake_repo "$tmp"

  (
    cd "$tmp"
    SINGLEBOX_EXPECTED_COVERAGE_ROOT="${tmp}/relative-cov" PATH="${tmp}/fake-bin:$PATH"       PYTHON="${ROOT}/apps/backend/.venv/bin/python"       SINGLEBOX_STUB_LOG="${tmp}/singlebox.log"       UV_STUB_LOG="${tmp}/uv.log"       "${tmp}/singlebox/ci/singlebox_coverage.sh"         --coverage-root relative-cov >"${tmp}/run.log"
  )
  output="$(cat "${tmp}/run.log")"

  # Generated service configs embed this root while the launchers cd into
  # module directories; it must never stay relative to the caller's cwd.
  grep -Fx "coverage_root: ${tmp}/relative-cov" <<<"$output" >/dev/null || \
    fail "relative --coverage-root must be absolutized before use"
  [ -f "${tmp}/relative-cov/reports/summary.json" ] || \
    fail "reports must be written under the requested coverage root"
}

test_reporter_selection_failure_is_preserved() {
  local tmp output rc
  tmp="$(mktemp -d)"
  make_fake_repo "$tmp"
  cat > "${tmp}/singlebox/ci/singlebox_coverage_modules.yaml" <<'YAML'
modules:
  empty:
YAML

  set +e
  output="$(
    cd "$tmp"
    PYTHON="${ROOT}/apps/backend/.venv/bin/python" \
      "${tmp}/singlebox/ci/singlebox_coverage.sh" 2>&1
  )"
  rc=$?
  set -e

  [ "$rc" -eq 1 ] || fail "invalid manifest should return validation error"
  grep -F "empty: module config must be a mapping" <<<"$output" >/dev/null || \
    fail "manifest validation error should be preserved"
  if grep -F "singlebox coverage manifest selected no modules" <<<"$output" >/dev/null; then
    fail "reporter failure should not be replaced by an empty-selection error"
  fi
}

test_default_mode_runs_real_singlebox
test_failed_restart_trap_removes_provider_fixture
test_model_config_mode_can_use_home
test_bcs_e2e_failure_preserves_reports_and_fails_gate
test_bcs_e2e_failure_without_artifacts_preserves_original_status
test_bcs_e2e_failure_with_malformed_artifacts_preserves_original_status
test_mock_mode_is_not_supported
test_module_mode_reports_device_metrics
test_relative_coverage_root_is_absolutized
test_reporter_selection_failure_is_preserved

printf 'PASS: singlebox coverage gate tests\n'
