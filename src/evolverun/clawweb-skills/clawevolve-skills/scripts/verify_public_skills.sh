#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
default_root="$(cd "${script_dir}/.." && pwd -P)"
requested_root="${1:-${default_root}}"

[[ -d "${requested_root}" ]] || {
  echo "Public Clawevolve Skills root not found: ${requested_root}" >&2
  exit 1
}
skills_root="$(cd "${requested_root}" && pwd -P)"

legacy_version_file="$(find "${skills_root}" -mindepth 2 -maxdepth 2 -type f -name version -print -quit)"
[[ -z "${legacy_version_file}" ]] || {
  echo "Legacy per-Skill version file is not allowed: ${legacy_version_file}" >&2
  exit 1
}

required_entries=(
  "clawevolve-diagnose/scripts/run.sh"
  "clawevolve-plan/scripts/run.sh"
  "clawevolve-pack/scripts/pack.sh"
  "clawevolve-deploy/scripts/deploy.sh"
  "scripts/clawevolve_runtime_cleanup.py"
  "clawevolve-workflow/scripts/handlers/clawevolve_bench_run.py"
  "clawevolve-workflow/scripts/handlers/clawevolve_bench_plan_run.py"
  "clawevolve-workflow/scripts/handlers/clawevolve_pack_run.py"
  "clawevolve-workflow/scripts/handlers/clawevolve_optimize_run.py"
)
for entry in "${required_entries[@]}"; do
  candidate="${skills_root}/${entry}"
  [[ -f "${candidate}" && -r "${candidate}" && ! -L "${candidate}" ]] || {
    echo "Required public Skill entry not found: ${candidate}" >&2
    exit 1
  }
  resolved="$(cd "$(dirname "${candidate}")" && pwd -P)/$(basename "${candidate}")"
  [[ "${resolved}" == "${skills_root}/"* ]] || {
    echo "Public Skill entry escapes Skills root: ${entry}" >&2
    exit 1
  }
done

printf '%s\n' "${skills_root}"
