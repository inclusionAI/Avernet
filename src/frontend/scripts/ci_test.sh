#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
frontend_dir="$(cd "$script_dir/.." && pwd)"

base=""
head="HEAD"

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --base)
      if [[ "$#" -lt 2 ]]; then
        echo "error: --base requires an argument" >&2
        exit 2
      fi
      base="$2"
      shift 2
      ;;
    --head)
      if [[ "$#" -lt 2 ]]; then
        echo "error: --head requires an argument" >&2
        exit 2
      fi
      head="$2"
      shift 2
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

cd "$frontend_dir"

if [[ ! -f package.json ]]; then
  echo "frontend CI failed: package.json not found" >&2
  exit 1
fi

package_runner="${FRONTEND_CI_PACKAGE_MANAGER:-}"
if [[ -z "$package_runner" ]]; then
  if [[ -f package-lock.json ]] && command -v npm >/dev/null 2>&1; then
    package_runner=npm
  elif command -v tnpm >/dev/null 2>&1; then
    package_runner=tnpm
  elif command -v npm >/dev/null 2>&1; then
    package_runner=npm
  else
    package_runner=""
  fi
fi

if [[ -z "$package_runner" ]]; then
  echo "frontend CI failed: neither npm nor tnpm found" >&2
  exit 127
fi

if ! command -v "$package_runner" >/dev/null 2>&1; then
  echo "frontend CI failed: package runner not found: $package_runner" >&2
  exit 127
fi

if [[ ! -x node_modules/.bin/max ]]; then
  if [[ "${FRONTEND_CI_SKIP_INSTALL:-0}" == "1" ]]; then
    echo "frontend CI failed: node_modules/.bin/max missing and FRONTEND_CI_SKIP_INSTALL=1" >&2
    exit 127
  fi
  if [[ "$package_runner" == "npm" && -f package-lock.json ]]; then
    HUSKY=0 "$package_runner" ci
  else
    HUSKY=0 "$package_runner" install
  fi
fi

echo "frontend CI gate"
echo "frontend_dir: $frontend_dir"
echo "base: ${base:-<none>}"
echo "head: $head"

"$package_runner" run ci
echo "frontend CI gate passed"
