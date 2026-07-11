#!/usr/bin/env bash
set -euo pipefail

CN_PYPI_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"
DEFAULT_RAW_BASE_URL="https://raw.githubusercontent.com/inclusionAI/Avernet/dev/src/bcs/connectors/hermes"
DEFAULT_INSTALLER_URL="https://raw.githubusercontent.com/inclusionAI/Avernet/dev/src/bcs/docs/install-instructions/install-hermes.sh"

resolve_pip_index() {
  if [[ -n "${PIP_INDEX_URL:-}" ]]; then
    printf '%s\n' "$PIP_INDEX_URL"
  elif [[ "${USE_CN_MIRROR:-0}" == "1" ]]; then
    printf '%s\n' "$CN_PYPI_INDEX"
  fi
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

valid_session() {
  python3 - "$1" <<'PY'
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

usage() {
  cat <<'EOF'
Usage: install-hermes.sh [--token TOKEN] [options]
  --bot-name NAME
  --profile NAME | --hermes-home PATH
  --bcs-endpoint URL       default: http://127.0.0.1:21000
  --bcs-ws-url URL         default: ws://127.0.0.1:21000/ws/bot
  --workspace PATH
  --replace                confirm replacement of existing credentials
  --china-mirror
EOF
}

main() {
  local human_token="" bot_name="" profile="" explicit_home="" workspace=""
  local bcs_endpoint="http://127.0.0.1:21000"
  local bcs_ws_url="ws://127.0.0.1:21000/ws/bot"
  local replace=0 answer="" hermes_home="" profile_arg=""
  while (($#)); do
    case "$1" in
      --token) human_token="${2:-}"; shift 2 ;;
      --bot-name) bot_name="${2:-}"; shift 2 ;;
      --profile) profile="${2:-}"; shift 2 ;;
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
  command -v python3 >/dev/null 2>&1 || fail "python3 is required"
  command -v curl >/dev/null 2>&1 || fail "curl is required"
  python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' \
    || fail "Python 3.11 or newer is required"
  [[ -z "$profile" || -z "$explicit_home" ]] \
    || fail "use either --profile or --hermes-home, not both"

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
  [[ -d "$hermes_home" && -f "$hermes_home/config.yaml" ]] \
    || fail "Hermes profile is not configured: $hermes_home"

  if [[ -z "$bot_name" ]]; then
    [[ -t 0 ]] || fail "--bot-name is required in non-interactive mode"
    read -r -p "Bot name: " bot_name
  fi
  [[ -n "$bot_name" ]] || fail "bot name cannot be empty"

  local session="$hermes_home/bcn/session.json"
  local existing_valid=0
  if [[ -f "$session" ]] && valid_session "$session"; then
    existing_valid=1
  fi
  if [[ "$existing_valid" == "0" || "$replace" == "1" ]]; then
    [[ -n "$human_token" ]] || fail "--token is required for registration"
  fi
  if [[ -f "$session" && "$replace" == "1" ]]; then
    [[ -t 0 ]] || fail "credential replacement requires interactive confirmation"
    read -r -p "Replace existing BCS credentials? [y/N] " answer
    [[ "$answer" == "y" || "$answer" == "Y" ]] \
      || fail "credential replacement cancelled"
  fi

  local data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
  local install_dir="$data_home/avernet/hermes-bcn"
  local connector="$install_dir/hermes_bcn.py"
  local venv="$install_dir/venv"
  local raw_base="${AVERNET_RAW_BASE_URL:-$DEFAULT_RAW_BASE_URL}"
  local temp_dir="" temp_connector=""
  temp_dir="$(mktemp -d)"
  temp_connector="$temp_dir/hermes_bcn.py"
  trap 'rm -rf "${temp_dir:-}"' EXIT
  curl -fsSL "$raw_base/hermes_bcn.py" -o "$temp_connector"
  python3 -m py_compile "$temp_connector"

  local -a home_args=()
  if [[ "$profile_arg" == "--profile" ]]; then
    home_args=(--profile "$profile")
  else
    home_args=(--hermes-home "$hermes_home")
  fi
  local -a register_args=(
    register "${home_args[@]}" --human-token "${human_token:-reuse-existing}"
    --bot-name "$bot_name" --bcs-endpoint "$bcs_endpoint" --bcs-url "$bcs_ws_url"
  )
  [[ -z "$workspace" ]] || register_args+=(--workspace "$workspace")
  [[ "$replace" == "0" ]] || register_args+=(--replace)

  local registration="" registered_uuid=""
  registration="$(python3 "$temp_connector" "${register_args[@]}")"
  registered_uuid="${registration#registered }"
  trap 'code=$?; rm -rf "${temp_dir:-}"; if [[ $code -ne 0 && -n "${registered_uuid:-}" ]]; then printf "Registration was saved for bot %s. Resume with:\n" "$registered_uuid" >&2; printf "curl -fsSL %q | bash -s -- --bot-name %q %q %q --bcs-endpoint %q --bcs-ws-url %q\n" "$DEFAULT_INSTALLER_URL" "$bot_name" "${home_args[0]}" "${home_args[1]}" "$bcs_endpoint" "$bcs_ws_url" >&2; fi; exit $code' EXIT

  mkdir -p "$install_dir"
  local install_temp=""
  install_temp="$(mktemp "$install_dir/.hermes_bcn.py.XXXXXX")"
  cp "$temp_connector" "$install_temp"
  chmod 700 "$install_temp"
  mv -f "$install_temp" "$connector"

  [[ -x "$venv/bin/python" ]] || python3 -m venv "$venv"
  local pip_index=""
  local -a pip_args=()
  pip_index="$(resolve_pip_index)"
  [[ -z "$pip_index" ]] || pip_args+=(--index-url "$pip_index")
  "$venv/bin/python" -m pip install "${pip_args[@]}" 'websockets>=14,<16'

  "$venv/bin/python" "$connector" start "${home_args[@]}"
  "$venv/bin/python" "$connector" status "${home_args[@]}" >/dev/null
  printf 'Hermes BCN connector is running for bot %s.\n' "$registered_uuid"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
