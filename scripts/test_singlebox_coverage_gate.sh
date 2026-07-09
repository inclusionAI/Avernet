#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="${ROOT}/scripts/ci/singlebox_coverage.sh"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

make_fake_repo() {
  local tmp="$1"
  mkdir -p "${tmp}/scripts/ci"
  cp "$SCRIPT" "${tmp}/scripts/ci/singlebox_coverage.sh"
  chmod +x "${tmp}/scripts/ci/singlebox_coverage.sh"
  cat > "${tmp}/scripts/singlebox.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
case ":${PATH}:" in
  *":${HOME}/.cargo/bin:"*) ;;
  *) echo "missing cargo path in PATH" >&2; exit 11 ;;
esac
if [ "${SINGLEBOX_MODEL_CONFIG_MODE:-}" != "mock" ]; then
  echo "singlebox coverage should force SINGLEBOX_MODEL_CONFIG_MODE=mock" >&2
  exit 12
fi
printf '%s\n' "$*" >> "${SINGLEBOX_STUB_LOG:?}"
exit 0
SH
  chmod +x "${tmp}/scripts/singlebox.sh"
}

test_default_mode_runs_real_singlebox() {
  local tmp log
  tmp="$(mktemp -d)"
  log="${tmp}/singlebox.log"
  make_fake_repo "$tmp"

  SINGLEBOX_STUB_LOG="$log" "${tmp}/scripts/ci/singlebox_coverage.sh" \
    --coverage-root "${tmp}/coverage" >/dev/null

  grep -Fx -- "--local start baas backend bcs" "$log" >/dev/null || \
    fail "default coverage mode should start the real singlebox coverage stack"
  grep -Fx -- "--local stop bcs backend baas" "$log" >/dev/null || \
    fail "default coverage mode should stop the real singlebox coverage stack"
}

test_mock_mode_is_not_supported() {
  local tmp output rc
  tmp="$(mktemp -d)"
  make_fake_repo "$tmp"

  set +e
  output="$("${tmp}/scripts/ci/singlebox_coverage.sh" --mode mock 2>&1)"
  rc=$?
  set -e

  [ "$rc" -ne 0 ] || fail "mock mode should be rejected"
  grep -F "unknown singlebox coverage mode: mock" <<<"$output" >/dev/null || \
    fail "mock mode rejection message mismatch"
}

test_default_mode_runs_real_singlebox
test_mock_mode_is_not_supported

printf 'PASS: singlebox coverage gate tests\n'
