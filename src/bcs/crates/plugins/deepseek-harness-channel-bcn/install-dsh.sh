#!/usr/bin/env bash
set -euo pipefail
set +x

profile="web"
endpoint=""
bot_name="DeepSeek Harness Bot"
package_spec="@avernet-plugin/deepseek-harness-channel-bcn"
start_dsh=true

usage() {
  echo "Usage: install-dsh.sh --endpoint <url> [--profile <name>] [--bot-name <name>] [--package <npm-spec-or-path>] [--no-start]"
}

require_value() {
  if [[ $# -lt 2 || -z "${2:-}" || "${2:-}" == --* ]]; then
    echo "install-dsh.sh: $1 requires a value" >&2
    exit 2
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --endpoint)
      require_value "$@"
      endpoint="$2"
      shift 2
      ;;
    --profile)
      require_value "$@"
      profile="$2"
      shift 2
      ;;
    --bot-name)
      require_value "$@"
      bot_name="$2"
      shift 2
      ;;
    --package)
      require_value "$@"
      package_spec="$2"
      shift 2
      ;;
    --no-start)
      start_dsh=false
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "install-dsh.sh: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$endpoint" ]]; then
  echo "install-dsh.sh: --endpoint is required" >&2
  exit 2
fi
if [[ ! "$profile" =~ ^[a-z0-9][a-z0-9-]{0,63}$ ]]; then
  echo "install-dsh.sh: invalid DSH profile name" >&2
  exit 2
fi
if ! command -v dsh >/dev/null 2>&1; then
  echo "install-dsh.sh: dsh is not available on PATH" >&2
  exit 1
fi

# Keep the short-lived registration Token out of package-manager and config-helper environments.
onboarding_token="${BCN_ONBOARDING_TOKEN:-}"
unset BCN_ONBOARDING_TOKEN

dsh plugin --profile "$profile" add -- "$package_spec"
dsh plugin --profile "$profile" exec dsh-bcn-configure -- \
  --profile "$profile" \
  --endpoint "$endpoint" \
  --bot-name "$bot_name"

if [[ "$start_dsh" != true ]]; then
  echo "Configured DSH profile $profile without starting it."
  if [[ -n "$onboarding_token" ]]; then
    echo "BCN_ONBOARDING_TOKEN was not persisted; provide it again on the first DSH start."
  fi
  exit 0
fi

echo "Starting DSH profile $profile."
if [[ -n "$onboarding_token" ]]; then
  export BCN_ONBOARDING_TOKEN="$onboarding_token"
fi
exec dsh --profile "$profile"
