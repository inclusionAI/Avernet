#!/usr/bin/env bash
set -euo pipefail
workspace="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
skills_root="${CLAWEVOLVE_SKILLS_ROOT:-$(cd "${workspace}/../clawweb-skills/clawevolve-skills" && pwd -P)}"
if [[ "$#" != 2 || "$1" != "--config" ]]; then
  echo "Usage: bash $0 --config /absolute/path/singlebox.local.yaml" >&2
  echo "Start Avernet with its existing scripts/singlebox.sh first; this command starts ClawWeb only." >&2
  exit 2
fi
[[ -f "$2" ]] || { echo "Config not found: $2 (create it from configs/singlebox.example.yaml first)" >&2; exit 1; }
config="$(cd "$(dirname "$2")" && pwd -P)/$(basename "$2")"
node -e 'const [major, minor] = process.versions.node.split(".").map(Number); if (!(major === 20 && minor >= 19 || major === 22 && minor >= 12 || major > 22)) { console.error("Use Node 20.19+ or 22.12+"); process.exit(1); }'
cd "$workspace"
if [[ ! -d node_modules ]]; then
  echo "Install dependencies first: cd $workspace && npm ci" >&2
  exit 1
fi
npm run build --workspace @avernet/clawweb-shared
npm run build --workspace @avernet/clawevolve
# Do not source the internal launcher or restart/reset Avernet services.
export CLAWEVOLVE_SKILLS_ROOT="$(bash "${skills_root}/scripts/verify_public_skills.sh" "${skills_root}")"
echo "[clawweb] public Skills root: ${CLAWEVOLVE_SKILLS_ROOT}"
export CLAWEVOLVE_SINGLEBOX_CONFIG="$config"
export CLAWWEB_OSS_ENV=dev INSIGHT_ENV=dev SERVER_ENV=dev
exec node public/modules/clawevolve/dist/server/singlebox.js
