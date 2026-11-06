#!/usr/bin/env bash
set -euo pipefail

# Prewarm the frontend-nextgen review environment so a code review starts from a
# ready workspace instead of installing dependencies when the pull request lands.
#
# The warm tree lives outside the worktree and is keyed by package-lock.json, so
# every worktree of the same commit reuses one install, and a lockfile change
# naturally produces a new key. Worktrees then link `node_modules` from a
# review worktree with the documented sibling flag below.
#
# Usage:
#   prewarm_review_env.sh                 # prepare/refresh the warm tree for HEAD
#   prewarm_review_env.sh --print-key     # print the cache key only
#   prewarm_review_env.sh --path          # print the warm tree path only
#   prewarm_review_env.sh --link-only     # link the warm tree into this worktree
#
# Environment:
#   PREWARM_CACHE_DIR   warm tree root (default: ${XDG_CACHE_HOME:-~/.cache}/ocb-review-env)

module_rel="apps/frontend-nextgen"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cache_root="${PREWARM_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/ocb-review-env}"
lock="$repo_root/$module_rel/package-lock.json"

mode="prepare"
case "${1:-}" in
  "") ;;
  --print-key) mode="key" ;;
  --path) mode="path" ;;
  --link-only) mode="link" ;;
  *) echo "usage: $(basename "$0") [--print-key|--path|--link-only]" >&2; exit 2 ;;
esac

[[ -f "$lock" ]] || { echo "missing lockfile: $lock" >&2; exit 1; }

# The install depends only on the lockfile and the Node ABI used to build native
# addons, so both parts of the key that a review can hit are represented.
lock_digest="$(node -e '
  const c = require("crypto");
  const f = require("fs");
  process.stdout.write(c.createHash("sha256").update(f.readFileSync(process.argv[1])).digest("hex").slice(0, 16));
' "$lock")"
node_series="$(node -p 'process.versions.node.split(".").slice(0, 2).join(".")')"
cache_key="$(printf '%s' "node$node_series-$lock_digest")"
warm_root="$cache_root/$cache_key"
warm_module="$warm_root/$module_rel"

case "$mode" in
  key) echo "$cache_key"; exit 0 ;;
  path) echo "$warm_module"; exit 0 ;;
esac

link_warm() {
  local target="$repo_root/$module_rel/node_modules"
  if [[ -L "$target" ]]; then
    echo "node_modules already linked: $(readlink "$target")"
  elif [[ -e "$target" ]]; then
    echo "refusing to replace a real $module_rel/node_modules; remove it first" >&2
    exit 1
  else
    ln -s "$warm_module/node_modules" "$target"
    echo "linked $module_rel/node_modules -> $warm_module/node_modules"
  fi
  # `max setup` writes generated code relative to the worktree, never shared.
  ( cd "$repo_root/$module_rel" && npm run setup --silent )
}

if [[ "$mode" == "link" ]]; then
  [[ -d "$warm_module/node_modules" ]] || {
    echo "warm tree missing for $cache_key; run $(basename "$0") first" >&2
    exit 1
  }
  link_warm
  exit 0
fi

if [[ -d "$warm_module/node_modules" ]]; then
  echo "warm tree already present: $warm_module"
  [[ "$repo_root" == "$warm_root" ]] || link_warm
  exit 0
fi

# Install in a scratch checkout placed at the repo-relative path. `max setup`
# resolves `@avernet/a2a-chat-client` as `../../packages/a2a-chat-client`, so a
# copy of the module alone cannot run its postinstall.
echo "preparing warm tree $cache_key"
mkdir -p "$cache_root"
scratch="$(mktemp -d "$cache_root/.scratch.XXXXXX")"
trap 'rm -rf "$scratch"' EXIT
mkdir -p "$scratch/$module_rel" "$scratch/packages"
# Copy the working tree, not HEAD, so a checkout of any commit or branch warms
# the lockfile it actually carries. Sources are excluded because npm ci only
# needs the manifests; the generated .umi tree belongs to each worktree.
rsync -a --exclude=node_modules --exclude=.umi --exclude=dist \
  "$repo_root/$module_rel/" "$scratch/$module_rel/"
rsync -a --exclude=node_modules "$repo_root/packages/a2a-chat-client/" "$scratch/packages/a2a-chat-client/"
( cd "$scratch/$module_rel" && npm ci --no-audit --no-fund )
mkdir -p "$warm_root/$(dirname "$module_rel")"
mv "$scratch/$module_rel" "$warm_module"
echo "warm tree ready: $warm_module"

if [[ ! -e "$repo_root/$module_rel/node_modules" ]]; then
  [[ "$repo_root" == "$warm_root" ]] || link_warm
fi