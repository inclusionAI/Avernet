#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd -P)"
clawweb_root="$(cd "$script_dir/.." && pwd -P)"
avernet_root="$(git -C "$clawweb_root" rev-parse --show-toplevel)"
ocb_root=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --ocb)
      [ "$#" -ge 2 ] || { echo "--ocb requires a path" >&2; exit 2; }
      ocb_root="$(cd "$2" && pwd -P)"
      shift 2
      ;;
    *)
      echo "unknown option: $1" >&2
      exit 2
      ;;
  esac
done

node -e '
  const [major, minor] = process.versions.node.split(".").map(Number);
  const supported = (major === 20 && minor >= 19) || (major === 22 && minor >= 12) || major > 22;
  if (!supported) {
    console.error(`Node.js ${process.versions.node} is unsupported; use 20.19.0 or >=22.12.0`);
    process.exit(1);
  }
'

echo "[ClawWeb tests] installing Avernet dependencies"
cd "$clawweb_root"
npm ci --include=dev --include=optional --no-audit --no-fund

test_root="$clawweb_root"
if [ -n "$ocb_root" ]; then
  ocb_clawweb="$ocb_root/src/evolverun/clawweb"
  [ -f "$ocb_clawweb/package.json" ] || {
    echo "OCB ClawWeb workspace not found: $ocb_clawweb" >&2
    exit 1
  }

  mkdir -p "$ocb_clawweb/.build"
  link_path="$ocb_clawweb/.build/avernet"
  if [ -e "$link_path" ] && [ ! -L "$link_path" ]; then
    echo "refusing to replace non-symlink workspace path: $link_path" >&2
    exit 1
  fi
  ln -sfn "$avernet_root" "$link_path"

  cd "$ocb_clawweb"
  node scripts/ci/verify_clawweb_workspace.mjs "$avernet_root"
  echo "[ClawWeb tests] installing OCB composition dependencies"
  npm install --include=dev --include=optional --no-audit --no-fund
  test_root="$ocb_clawweb"
fi

cd "$test_root"
echo "[ClawWeb tests] building packages"
npm run ci:build
echo "[ClawWeb tests] checking packages"
npm run ci:check
echo "[ClawWeb tests] running package tests"
# Following the workspace runner's exit code keeps existing test failures visible.
npm run test:ci
