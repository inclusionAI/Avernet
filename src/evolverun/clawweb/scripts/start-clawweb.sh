#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd -P)"
clawweb_root="$(cd "$script_dir/.." && pwd -P)"
avernet_root="$(git -C "$clawweb_root" rev-parse --show-toplevel)"
ocb_root=""
machine_env=""
config_path=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --ocb)
      [ "$#" -ge 2 ] || { echo "--ocb requires a path" >&2; exit 2; }
      ocb_root="$(cd "$2" && pwd -P)"
      shift 2
      ;;
    --env)
      [ "$#" -ge 2 ] || { echo "--env requires dev, pre, or prod" >&2; exit 2; }
      machine_env="$2"
      shift 2
      ;;
    --config)
      [ "$#" -ge 2 ] || { echo "--config requires a file" >&2; exit 2; }
      config_path="$(cd "$(dirname "$2")" && pwd -P)/$(basename "$2")"
      [ -f "$config_path" ] || { echo "config not found: $config_path" >&2; exit 1; }
      shift 2
      ;;
    *)
      echo "unknown option: $1" >&2
      exit 2
      ;;
  esac
done

if [ -z "$ocb_root" ]; then
  echo "--ocb <path> is required; V10 local validation only supports the internal ClawWeb host" >&2
  exit 2
fi

if [ -n "$machine_env" ]; then
  machine_env="$(printf '%s' "$machine_env" | tr '[:upper:]' '[:lower:]')"
  case "$machine_env" in
    dev|local) machine_env="dev" ;;
    pre|prepub) machine_env="pre" ;;
    prod|gray) machine_env="prod" ;;
    *) echo "unsupported environment: $machine_env" >&2; exit 2 ;;
  esac
fi

ocb_clawweb="$ocb_root/src/evolverun/clawweb"
[ -f "$ocb_clawweb/package.json" ] || { echo "OCB ClawWeb workspace not found: $ocb_clawweb" >&2; exit 1; }
mkdir -p "$ocb_clawweb/.build"
link_path="$ocb_clawweb/.build/avernet"
if [ -e "$link_path" ] && [ ! -L "$link_path" ]; then
  echo "refusing to replace non-symlink workspace path: $link_path" >&2
  exit 1
fi
ln -sfn "$avernet_root" "$link_path"

cd "$ocb_clawweb"
bash scripts/ci/verify_clawweb_workspace.sh "$avernet_root"

# Keep the original ClawWeb local behavior: use meshboot/MOSN to connect to
# the dev ZDAS database. Callers may explicitly select SQLite when needed.
if [ -z "${DATABASE_MODE:-}" ]; then
  export DATABASE_MODE="zdas"
fi
export ZDAS_HOST="${ZDAS_HOST:-127.0.0.1}"
export ZDAS_PORT="${ZDAS_PORT:-11306}"
export ZDAS_DATABASE="${ZDAS_DATABASE:-clawweb_ds}"
export NO_PROXY="${NO_PROXY:-localhost,127.0.0.1,::1}"
export no_proxy="${no_proxy:-localhost,127.0.0.1,::1}"

zdas_proxy_ready() {
  if command -v nc >/dev/null 2>&1; then
    nc -z "$ZDAS_HOST" "$ZDAS_PORT" >/dev/null 2>&1
  else
    lsof -nP -iTCP:"$ZDAS_PORT" -sTCP:LISTEN >/dev/null 2>&1
  fi
}

case "$DATABASE_MODE" in
  zdas|prod)
    if [ "$ZDAS_HOST" = "127.0.0.1" ] || [ "$ZDAS_HOST" = "localhost" ]; then
      if ! zdas_proxy_ready; then
        command -v meshboot >/dev/null 2>&1 || {
          echo "meshboot is required for the local dev ZDAS database" >&2
          exit 1
        }
        echo "[meshboot] starting clawweb MOSN proxy..."
        meshboot start -m binary -a clawweb
        for _ in 1 2 3 4 5 6 7 8 9 10; do
          zdas_proxy_ready && break
          sleep 1
        done
        zdas_proxy_ready || {
          echo "ZDAS proxy is not listening on ${ZDAS_HOST}:${ZDAS_PORT}" >&2
          exit 1
        }
      fi
      echo "[database] using dev ZDAS through ${ZDAS_HOST}:${ZDAS_PORT}/${ZDAS_DATABASE}"
    fi
    ;;
esac

if [ "$DATABASE_MODE" = "sqlite" ] && [ -z "${SQLITE_PATH:-}" ]; then
  local_state_dir="$ocb_clawweb/.build/local"
  mkdir -p "$local_state_dir"
  export SQLITE_PATH="$local_state_dir/engine.db"
fi

npm install
npm run build

export CLAWWEB_DEPLOY_PROFILE="internal"
export CLAWWEB_PUBLIC_CONFIG_ROOT="$clawweb_root"
export CLAWWEB_INTERNAL_CONFIG_ROOT="$ocb_clawweb/internal/configs"
args=(--bootstrap @ocb/clawweb-internal)
[ -z "$machine_env" ] || args+=(--env "$machine_env")
[ -z "$config_path" ] || args+=(--config "$config_path")
exec node node_modules/@avernet/clawweb/dist/server/cli.js "${args[@]}"
