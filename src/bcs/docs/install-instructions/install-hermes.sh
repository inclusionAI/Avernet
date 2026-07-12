#!/usr/bin/env bash
set -euo pipefail

CN_PYPI_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"
DEFAULT_RAW_BASE_URL="https://raw.githubusercontent.com/inclusionAI/Avernet/dev/src/bcs/connectors/hermes"
DEFAULT_INSTALLER_URL="https://raw.githubusercontent.com/inclusionAI/Avernet/dev/src/bcs/docs/install-instructions/install-hermes.sh"
TEMP_DIR=""
REGISTERED_UUID=""
RESUME_COMMAND=""
PYTHON_CMD=""

resolve_pip_index() {
  if [[ -n "${PIP_INDEX_URL:-}" ]]; then
    printf '%s\n' "$PIP_INDEX_URL"
  elif [[ "${USE_CN_MIRROR:-0}" == "1" ]]; then
    printf '%s\n' "$CN_PYPI_INDEX"
  fi
}

install_connector_dependencies() {
  local python="$1" pip_index=""
  pip_index="$(resolve_pip_index)"
  if [[ -n "$pip_index" ]]; then
    "$python" -m pip install --index-url "$pip_index" 'websockets>=14,<16'
  else
    "$python" -m pip install 'websockets>=14,<16'
  fi
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

github_contents_url() {
  local url="$1"
  if [[ "$url" =~ ^https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.*)$ ]]; then
    printf 'https://api.github.com/repos/%s/%s/contents/%s?ref=%s\n' \
      "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" \
      "${BASH_REMATCH[4]}" "${BASH_REMATCH[3]}"
    return 0
  fi
  return 1
}

download_file() {
  local url="$1" output="$2" fallback_url="" primary_status=0
  if curl --ipv4 --fail --silent --show-error --location \
    --retry 1 --retry-all-errors --connect-timeout 10 --max-time 15 \
    "$url" -o "$output"; then
    return 0
  else
    primary_status=$?
  fi

  fallback_url="$(github_contents_url "$url")" || return "$primary_status"
  printf 'warning: primary download failed; retrying through GitHub Contents API\n' >&2
  curl --ipv4 --fail --silent --show-error --location \
    --retry 3 --retry-all-errors --connect-timeout 10 --max-time 30 \
    -H 'Accept: application/vnd.github.raw+json' \
    "$fallback_url" -o "$output"
}

resolve_python() {
  local candidate="" resolved=""
  local -a candidates=()
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    candidates+=("$PYTHON_BIN")
  else
    candidates+=(
      python3 python3.14 python3.13 python3.12 python3.11
      /opt/homebrew/bin/python3 /usr/local/bin/python3
    )
  fi

  for candidate in "${candidates[@]}"; do
    if [[ "$candidate" == */* ]]; then
      [[ -x "$candidate" ]] || continue
      resolved="$candidate"
    else
      resolved="$(command -v "$candidate" 2>/dev/null || true)"
      [[ -n "$resolved" ]] || continue
    fi
    if "$resolved" -c \
      'import sys; raise SystemExit(sys.version_info < (3, 11))' \
      >/dev/null 2>&1; then
      printf '%s\n' "$resolved"
      return 0
    fi
  done
  return 1
}

ensure_python() {
  [[ -n "$PYTHON_CMD" ]] && return 0
  PYTHON_CMD="$(resolve_python)" || return 1
}

preflight_dashboard_isolation() {
  local dashboard_help=""
  if ! dashboard_help="$(hermes dashboard --help 2>&1)"; then
    fail "could not inspect Hermes Dashboard capabilities"
  fi
  [[ "$dashboard_help" == *"--isolated"* ]] \
    || fail "installed Hermes does not support dashboard --isolated"
}

preflight_install_target() {
  local install_dir="$1" existed=0 preflight_dir=""
  ensure_python || return 1
  [[ -d "$install_dir" ]] && existed=1
  mkdir -p "$install_dir"
  preflight_dir="$(mktemp -d "$install_dir/.preflight.XXXXXX")"
  if ! "$PYTHON_CMD" -m venv "$preflight_dir/venv"; then
    rm -rf "$preflight_dir"
    [[ "$existed" == "1" ]] || rmdir "$install_dir" 2>/dev/null || true
    return 1
  fi
  rm -rf "$preflight_dir"
  [[ "$existed" == "1" ]] || rmdir "$install_dir" 2>/dev/null || true
}

on_exit() {
  local code=$?
  rm -rf "${TEMP_DIR:-}"
  if [[ "$code" -ne 0 && -n "${REGISTERED_UUID:-}" ]]; then
    printf 'Registration was saved for bot %s. Resume with:\n' "$REGISTERED_UUID" >&2
    printf '%s\n' "$RESUME_COMMAND" >&2
  fi
  exit "$code"
}

build_resume_command() {
  local installer_url="$1" raw_base="$2"
  shift 2
  local command="" quoted="" preserve_pip_index=0
  local -a resume_env=()
  if [[ -n "${AVERNET_RAW_BASE_URL:-}" ]]; then
    resume_env+=("AVERNET_RAW_BASE_URL=$raw_base")
  fi
  if [[ -n "${PIP_INDEX_URL:-}" ]]; then
    preserve_pip_index=1
  fi
  printf -v quoted \
    'curl --ipv4 --fail --silent --show-error --location --retry 3 --retry-all-errors --connect-timeout 10 --max-time 30 %q | ' \
    "$installer_url"
  command+="$quoted"
  if ((${#resume_env[@]} || preserve_pip_index)); then
    if ((${#resume_env[@]})); then
      printf -v quoted '%q ' env "${resume_env[@]}"
    else
      printf -v quoted '%q ' env
    fi
    command+="$quoted"
    if [[ "$preserve_pip_index" == "1" ]]; then
      command+='PIP_INDEX_URL="${PIP_INDEX_URL:?export PIP_INDEX_URL before resuming}" '
    fi
    printf -v quoted '%q ' bash -s -- "$@"
  else
    printf -v quoted '%q ' bash -s -- "$@"
  fi
  RESUME_COMMAND="${command}${quoted% }"
}

valid_session() {
  ensure_python || return 1
  "$PYTHON_CMD" - "$1" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        value = json.load(handle)
except (OSError, ValueError):
    raise SystemExit(1)
required = ("bot_uuid", "bot_token", "bcs_url")
raise SystemExit(not isinstance(value, dict) or not all(isinstance(value.get(key), str) and value[key] for key in required))
PY
}

session_bot_name() {
  ensure_python || return 1
  "$PYTHON_CMD" - "$1" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    value = json.load(handle)
name = value.get("bot_name")
print(name if isinstance(name, str) else "")
PY
}

session_bot_uuid() {
  ensure_python || return 1
  "$PYTHON_CMD" - "$1" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    value = json.load(handle)
bot_uuid = value.get("bot_uuid")
print(bot_uuid if isinstance(bot_uuid, str) else "")
PY
}

reject_profile_bot_name_mismatch() {
  local session="$1" requested_name="$2" profile="$3" stored_name=""
  stored_name="$(session_bot_name "$session")" || return 1
  if [[ -n "$stored_name" && "$stored_name" != "$requested_name" ]]; then
    fail "profile $profile is already registered as $stored_name; choose another profile"
  fi
}

reject_pending_bot_name_mismatch() {
  local session="$1" requested_name="$2" profile="$3" stored_name=""
  stored_name="$(session_bot_name "$session")" || return 1
  if [[ -z "$stored_name" ]]; then
    fail "profile $profile has pending credentials without a Bot name"
  fi
  if [[ "$stored_name" != "$requested_name" ]]; then
    fail "profile $profile has pending credentials for $stored_name; use the matching Bot name"
  fi
}

recoverable_pending_uuid() {
  local session="$1" requested_name="$2" stored_name="" bot_uuid=""
  [[ -f "$session" ]] && valid_session "$session" || return 1
  stored_name="$(session_bot_name "$session")" || return 1
  [[ -n "$stored_name" && "$stored_name" == "$requested_name" ]] || return 1
  bot_uuid="$(session_bot_uuid "$session")" || return 1
  [[ -n "$bot_uuid" ]] || return 1
  printf '%s\n' "$bot_uuid"
}

read_registration_token() {
  local registration_needed="$1" token_stdin="$2"
  [[ "$registration_needed" == "1" ]] || return 0
  if [[ "$token_stdin" == "1" ]]; then
    IFS= read -r human_token \
      || fail "could not read the human token from stdin"
  else
    if ! IFS= read -r -s -p "Human token: " human_token </dev/tty; then
      fail "use --human-token-stdin in non-interactive mode"
    fi
    printf '\n' >/dev/tty
  fi
  [[ -n "$human_token" ]] || fail "human token cannot be empty"
}

usage() {
  cat <<'EOF'
Usage: install-hermes.sh [--human-token-stdin] [options]
  --human-token-stdin    read the human token from stdin when registration is needed
  --bot-name NAME
  --profile NAME | --hermes-home PATH
  --create-profile       create a missing named Hermes profile from default
  --bcs-endpoint URL       default: http://127.0.0.1:21000
  --bcs-ws-url URL         default: ws://127.0.0.1:21000/ws/bot
  --workspace PATH
  --replace                confirm replacement of existing credentials
  --china-mirror
EOF
}

validate_named_profile() {
  local profile="$1" create_profile="$2"
  if [[ "$create_profile" == "1" && -z "$profile" ]]; then
    fail "--create-profile requires --profile"
  fi
  [[ -z "$profile" ]] && return 0
  [[ ${#profile} -le 64 && "$profile" =~ ^[a-z0-9][a-z0-9_-]*$ ]] \
    || fail "profile must match [a-z0-9][a-z0-9_-]{0,63}"
  case "$profile" in
    default|hermes|test|tmp|root|sudo)
      fail "profile name is reserved: $profile"
      ;;
  esac
}

ensure_hermes_profile() {
  local profile="$1" hermes_home="$2" create_profile="$3"
  [[ -f "$hermes_home/config.yaml" ]] && return 0
  [[ "$create_profile" == "1" ]] \
    || fail "Hermes profile is not configured: $hermes_home"
  hermes profile create "$profile" --clone-from default
  [[ -f "$hermes_home/config.yaml" ]] \
    || fail "Hermes profile creation did not produce config.yaml: $profile"
}

main() {
  local human_token="" bot_name="" profile="" explicit_home="" workspace=""
  local bcs_endpoint="http://127.0.0.1:21000"
  local bcs_ws_url="ws://127.0.0.1:21000/ws/bot"
  local replace=0 token_stdin=0 create_profile=0 answer="" hermes_home="" profile_arg=""
  while (($#)); do
    case "$1" in
      --human-token-stdin) token_stdin=1; shift ;;
      --bot-name) bot_name="${2:-}"; shift 2 ;;
      --profile) profile="${2:-}"; shift 2 ;;
      --create-profile) create_profile=1; shift ;;
      --hermes-home) explicit_home="${2:-}"; shift 2 ;;
      --bcs-endpoint) bcs_endpoint="${2:-}"; shift 2 ;;
      --bcs-ws-url) bcs_ws_url="${2:-}"; shift 2 ;;
      --workspace) workspace="${2:-}"; shift 2 ;;
      --replace) replace=1; shift ;;
      --china-mirror) USE_CN_MIRROR=1; export USE_CN_MIRROR; shift ;;
      -h|--help) usage; return 0 ;;
      *) fail "unknown argument: $1" ;;
    esac
  done

  command -v hermes >/dev/null 2>&1 || fail "hermes is required"
  command -v curl >/dev/null 2>&1 || fail "curl is required"
  ensure_python || fail "Python 3.11 or newer is required"
  preflight_dashboard_isolation
  [[ -z "$profile" || -z "$explicit_home" ]] \
    || fail "use either --profile or --hermes-home, not both"
  validate_named_profile "$profile" "$create_profile"

  if [[ -n "$explicit_home" ]]; then
    hermes_home="${explicit_home/#\~/$HOME}"
    profile_arg="--hermes-home"
  elif [[ -n "$profile" ]]; then
    [[ "$profile" != */* && "$profile" != "." && "$profile" != ".." ]] \
      || fail "profile must be a single directory name"
    hermes_home="$HOME/.hermes/profiles/$profile"
    profile_arg="--profile"
  else
    hermes_home="${HERMES_HOME:-$HOME/.hermes}"
    explicit_home="$hermes_home"
    profile_arg="--hermes-home"
  fi
  ensure_hermes_profile "$profile" "$hermes_home" "$create_profile"

  if [[ -z "$bot_name" ]]; then
    if ! read -r -p "Bot name: " bot_name </dev/tty; then
      fail "--bot-name is required in non-interactive mode"
    fi
  fi
  [[ -n "$bot_name" ]] || fail "bot name cannot be empty"

  local session="$hermes_home/bcn/session.json"
  local pending_session="$hermes_home/bcn/session.pending.json"
  local existing_valid=0 pending_valid=0 registration_needed=0
  if [[ -f "$session" ]] && valid_session "$session"; then
    existing_valid=1
  fi
  if [[ -f "$pending_session" ]] && valid_session "$pending_session"; then
    pending_valid=1
  fi
  if [[ "$existing_valid" == "1" ]]; then
    reject_profile_bot_name_mismatch "$session" "$bot_name" "$profile"
  fi
  if [[ "$pending_valid" == "1" ]]; then
    reject_pending_bot_name_mismatch "$pending_session" "$bot_name" "$profile"
  fi
  if [[ -f "$session" && "$replace" == "1" ]]; then
    if ! read -r -p "Replace existing BCS credentials? [y/N] " answer </dev/tty; then
      fail "credential replacement requires interactive confirmation"
    fi
    [[ "$answer" == "y" || "$answer" == "Y" ]] \
      || fail "credential replacement cancelled"
  fi
  if [[ "$replace" == "1" && "$pending_valid" == "1" ]]; then
    registration_needed=0
  elif [[ "$existing_valid" == "0" || "$replace" == "1" ]]; then
    registration_needed=1
  fi
  read_registration_token "$registration_needed" "$token_stdin"

  local data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
  local install_dir="$data_home/avernet/hermes-bcn"
  local connector="$install_dir/hermes_bcn.py"
  local venv="$install_dir/venv"
  local raw_base="${AVERNET_RAW_BASE_URL:-$DEFAULT_RAW_BASE_URL}"
  local installer_url="${BCS_INSTALLER_URL:-$DEFAULT_INSTALLER_URL}"
  local temp_dir="" temp_connector=""
  temp_dir="$(mktemp -d)"
  TEMP_DIR="$temp_dir"
  temp_connector="$temp_dir/hermes_bcn.py"
  trap on_exit EXIT
  download_file "$raw_base/hermes_bcn.py" "$temp_connector"
  "$PYTHON_CMD" -m py_compile "$temp_connector"
  preflight_install_target "$install_dir" \
    || fail "cannot create a virtual environment in $install_dir"

  local -a home_args=()
  if [[ "$profile_arg" == "--profile" ]]; then
    home_args=(--profile "$profile")
  else
    home_args=(--hermes-home "$hermes_home")
  fi
  local -a register_args=(
    register "${home_args[@]}" --human-token-stdin
    --bot-name "$bot_name" --bcs-endpoint "$bcs_endpoint" --bcs-url "$bcs_ws_url"
  )
  [[ -z "$workspace" ]] || register_args+=(--workspace "$workspace")
  [[ "$replace" == "0" ]] || register_args+=(--replace)

  local -a resume_args=(
    --bot-name "$bot_name" "${home_args[@]}"
    --bcs-endpoint "$bcs_endpoint" --bcs-ws-url "$bcs_ws_url"
  )
  [[ -z "$workspace" ]] || resume_args+=(--workspace "$workspace")
  [[ "${USE_CN_MIRROR:-0}" != "1" ]] || resume_args+=(--china-mirror)
  [[ "$create_profile" == "0" ]] || resume_args+=(--create-profile)
  local -a registration_resume_args=("${resume_args[@]}")
  [[ "$replace" == "0" ]] || registration_resume_args+=(--replace)
  build_resume_command \
    "$installer_url" "$raw_base" "${registration_resume_args[@]}"

  local registration="" registered_uuid="" pending_uuid=""
  if ! registration="$(
    printf '%s\n' "$human_token" | \
      "$PYTHON_CMD" "$temp_connector" "${register_args[@]}"
  )"; then
    if [[ "$replace" == "1" ]] \
      && pending_uuid="$(recoverable_pending_uuid "$pending_session" "$bot_name")"; then
      REGISTERED_UUID="$pending_uuid"
    fi
    return 1
  fi
  registered_uuid="${registration#registered }"
  REGISTERED_UUID="$registered_uuid"
  if [[ "$replace" == "1" ]]; then
    build_resume_command "$installer_url" "$raw_base" "${resume_args[@]}"
  fi

  mkdir -p "$install_dir"
  local install_temp=""
  install_temp="$(mktemp "$install_dir/.hermes_bcn.py.XXXXXX")"
  cp "$temp_connector" "$install_temp"
  chmod 700 "$install_temp"
  mv -f "$install_temp" "$connector"

  [[ -x "$venv/bin/python" ]] || "$PYTHON_CMD" -m venv "$venv"
  install_connector_dependencies "$venv/bin/python"

  "$venv/bin/python" "$connector" start "${home_args[@]}" --health-wait 45
  "$venv/bin/python" "$connector" status "${home_args[@]}" >/dev/null
  printf 'Hermes BCN connector is running for bot %s.\n' "$registered_uuid"
  REGISTERED_UUID=""
}

if [[ -z "${BASH_SOURCE[0]:-}" || "${BASH_SOURCE[0]:-}" == "$0" ]]; then
  main "$@"
fi
