#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

DATE_STAMP="$(date +%Y%m%d)"
VERSION_ARG="${1:-}"
VERSION_FILE="${REPO_ROOT}/clawbench-base/version"
VERSION_FILES=(
  "${REPO_ROOT}/clawbench-base/version"
  "${REPO_ROOT}/clawbench-report/version"
  "${REPO_ROOT}/clawbench-template/version"
)

next_auto_version() {
  local max_version=0
  local candidate
  shopt -s nullglob
  for candidate in "${DIST_DIR}/clawbench-${DATE_STAMP}-v"*.zip; do
    candidate="$(basename "${candidate}")"
    candidate="${candidate#clawbench-${DATE_STAMP}-v}"
    candidate="${candidate%.zip}"
    if [[ "${candidate}" =~ ^[0-9]+$ ]] && (( candidate > max_version )); then
      max_version="${candidate}"
    fi
  done
  shopt -u nullglob

  if [[ -f "${VERSION_FILE}" ]]; then
    candidate="$(<"${VERSION_FILE}")"
    candidate="${candidate#clawbench-${DATE_STAMP}-v}"
    if [[ "${candidate}" =~ ^[0-9]+$ ]] && (( candidate > max_version )); then
      max_version="${candidate}"
    fi
  fi

  echo "v$((max_version + 1))"
}

DIST_DIR="${REPO_ROOT}/dist"
PACKAGE_DIRS=(
  "clawbench-base"
  "clawbench-report"
  "clawbench-template"
)

for dir in "${PACKAGE_DIRS[@]}"; do
  if [[ ! -d "${REPO_ROOT}/${dir}" ]]; then
    echo "Missing package directory: ${REPO_ROOT}/${dir}" >&2
    exit 1
  fi
done

if ! command -v zip >/dev/null 2>&1; then
  echo "zip command not found" >&2
  exit 1
fi

if [[ -z "${VERSION_ARG}" ]]; then
  VERSION="$(next_auto_version)"
elif [[ "${VERSION_ARG}" =~ ^clawbench-[0-9]{8}-v[0-9]+$ ]]; then
  PACKAGE_PREFIX="${VERSION_ARG}"
elif [[ "${VERSION_ARG}" =~ ^[0-9]+$ ]]; then
  VERSION="v${VERSION_ARG}"
else
  VERSION="${VERSION_ARG}"
fi

if [[ -z "${PACKAGE_PREFIX:-}" ]]; then
  PACKAGE_PREFIX="clawbench-${DATE_STAMP}-${VERSION}"
fi

if [[ ! "${PACKAGE_PREFIX}" =~ ^clawbench-[0-9]{8}-v[0-9]+$ ]]; then
  echo "Invalid package version: ${PACKAGE_PREFIX}. Expected clawbench-YYYYMMDD-vN." >&2
  exit 1
fi

OUTPUT_PATH="${DIST_DIR}/${PACKAGE_PREFIX}.zip"

sync_workflow_version() {
  local workflow_root="${REPO_ROOT}/clawbench-workflow/clawbench-pack/workflows"

  if [[ ! -d "${workflow_root}" ]]; then
    return
  fi

  python3 - "${workflow_root}" "${PACKAGE_PREFIX}" <<'PY'
import re
import sys
from pathlib import Path

workflow_root = Path(sys.argv[1])
package_prefix = sys.argv[2]
minimum_version = "clawbench-20260707-v4"

for path in workflow_root.glob("agentbench-runner_*.yaml"):
    text = path.read_text(encoding="utf-8")
    text = re.sub(
        r"LATEST_CLAWBENCH_VERSION:\s*clawbench-\d{8}-v\d+",
        f"LATEST_CLAWBENCH_VERSION: {package_prefix}",
        text,
    )
    if "MIN_CLAWBENCH_VERSION:" not in text:
        text = re.sub(
            r"(LATEST_CLAWBENCH_VERSION:\s*clawbench-\d{8}-v\d+)",
            lambda match: f"{match.group(1)}\n      MIN_CLAWBENCH_VERSION: {minimum_version}",
            text,
            count=1,
        )
    path.write_text(text, encoding="utf-8")

readme = workflow_root / "README.md"
if readme.is_file():
    text = readme.read_text(encoding="utf-8")
    text = re.sub(
        r"(当前 workflow 配置的最新版本为：\n\n```text\n)clawbench-\d{8}-v\d+(\n```)",
        lambda match: f"{match.group(1)}{package_prefix}{match.group(2)}",
        text,
    )
    if "最低要求版本为" not in text:
        text = re.sub(
            r"(当前 workflow 配置的最新版本为：\n\n```text\nclawbench-\d{8}-v\d+\n```)",
            lambda match: f"{match.group(1)}\n\n最低要求版本为：\n\n```text\n{minimum_version}\n```",
            text,
            count=1,
        )
    readme.write_text(text, encoding="utf-8")
PY
}

mkdir -p "${DIST_DIR}"
for version_file in "${VERSION_FILES[@]}"; do
  printf '%s\n' "${PACKAGE_PREFIX}" > "${version_file}"
done
sync_workflow_version
rm -f "${OUTPUT_PATH}"

(
  cd "${REPO_ROOT}"
  zip -qr "${OUTPUT_PATH}" "${PACKAGE_DIRS[@]}" \
    -x "*/__pycache__/*" \
    -x "*/.pytest_cache/*" \
    -x "*.pyc" \
    -x ".DS_Store" \
    -x "*/.DS_Store"
)

echo "${OUTPUT_PATH}"
