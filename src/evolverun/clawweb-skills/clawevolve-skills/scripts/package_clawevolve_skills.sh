#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${CLAWEVOLVE_SKILLS_ROOT:-${SCRIPT_DIR}/..}" && pwd -P)"
DIST_DIR="${CLAWEVOLVE_SKILLS_DIST_DIR:-${PROJECT_DIR}/dist}"
SKILLS=()
while IFS= read -r skill_file; do
  SKILLS+=("$(basename "$(dirname "$skill_file")")")
done < <(find "$PROJECT_DIR" -mindepth 2 -maxdepth 2 -type f -name SKILL.md | LC_ALL=C sort)
(( ${#SKILLS[@]} > 0 )) || {
  echo "no skills found under project root: ${PROJECT_DIR}" >&2
  exit 1
}

next_release_version() {
  local today sequence candidate
  today="$(date +%Y%m%d)"
  sequence=1
  while :; do
    candidate="clawevolve-${today}-v${sequence}"
    [[ -e "${DIST_DIR}/${candidate}" ]] || { printf '%s\n' "$candidate"; return; }
    sequence=$((sequence + 1))
  done
}

RELEASE_VERSION="${1:-$(next_release_version)}"
[[ "$RELEASE_VERSION" =~ ^[A-Za-z0-9._-]{1,128}$ ]] || {
  echo "invalid release version: ${RELEASE_VERSION}" >&2
  exit 2
}
[[ "$RELEASE_VERSION" =~ ^clawevolve-([0-9]{8})-v([0-9]+)$ ]] || {
  echo "release version must match clawevolve-YYYYMMDD-vN: ${RELEASE_VERSION}" >&2
  exit 2
}
RELEASE_DATE="${BASH_REMATCH[1]}"
RELEASE_SEQUENCE="${BASH_REMATCH[2]}"
RELEASE_DIR="${DIST_DIR}/${RELEASE_VERSION}"
RELEASE_BUILD_DIR="${DIST_DIR}/.${RELEASE_VERSION}.incoming.$$"
ARCHIVE_VERSION="${RELEASE_VERSION#clawevolve-}"
ARCHIVE_NAME="clawevolve-skills-${ARCHIVE_VERSION}.tar"
OUTPUT="${RELEASE_BUILD_DIR}/${ARCHIVE_NAME}"

skill_digest() {
  local source_dir="$1"
  (
    cd "$source_dir"
    find . -type d \( -name '__pycache__' -o -name '.pytest_cache' -o -name '.git' -o -path './tasks' \) -prune -o \
      -type f ! -name 'version' ! -name '.clawevolve-version' \
      ! -name '.DS_Store' ! -name '.nfs*' ! -name '*.pyc' ! -name '*.log' -print \
      | LC_ALL=C sort \
      | while IFS= read -r file; do
          printf '%s  ' "$file"
          shasum -a 256 "$file" | awk '{print $1}'
        done
  ) | shasum -a 256 | awk '{print $1}'
}

version_for_release() {
  local version="$1" prefix
  if [[ "$version" =~ ^(.+)-([0-9]{8})-v([0-9]+)$ ]]; then
    prefix="${BASH_REMATCH[1]}"
    printf '%s-%s-v%s\n' "$prefix" "$RELEASE_DATE" "$RELEASE_SEQUENCE"
    return
  fi
  echo "skill version must match <prefix>-YYYYMMDD-vN: ${version}" >&2
  return 1
}

mkdir -p "$DIST_DIR"
[[ ! -e "$RELEASE_DIR" ]] || { echo "release already exists: ${RELEASE_DIR}" >&2; exit 2; }
mkdir -p "$RELEASE_BUILD_DIR"
STAGING_DIR="$(mktemp -d /tmp/clawevolve-skills-package.XXXXXX)"
trap 'rm -rf "$STAGING_DIR" "$RELEASE_BUILD_DIR"' EXIT
mkdir -p "$STAGING_DIR/skills"
: > "$STAGING_DIR/manifest.tsv"

for name in "${SKILLS[@]}"; do
  source_dir="${PROJECT_DIR}/${name}"
  version_file="${source_dir}/version"
  [[ -f "${source_dir}/SKILL.md" ]] || { echo "missing skill: ${source_dir}" >&2; exit 1; }
  [[ -f "$version_file" ]] || { echo "missing skill version: ${version_file}" >&2; exit 1; }
  version="$(tr -d '[:space:]' < "$version_file")"
  [[ "$version" =~ ^[A-Za-z0-9._-]{1,128}$ ]] || { echo "invalid skill version: ${name}=${version}" >&2; exit 1; }
  digest="$(skill_digest "$source_dir")"
  version="$(version_for_release "$version")"

  cp -R "$source_dir" "$STAGING_DIR/skills/$name"
  find "$STAGING_DIR/skills/$name" -name '.DS_Store' -delete
  find "$STAGING_DIR/skills/$name" -type d \( -name '__pycache__' -o -name '.pytest_cache' -o -name '.git' \) -prune -exec rm -rf {} +
  rm -rf "$STAGING_DIR/skills/$name/tasks"
  find "$STAGING_DIR/skills/$name" -type f \( -name '*.pyc' -o -name '*.log' -o -name '.nfs*' \) -delete
  find "$STAGING_DIR/skills/$name/scripts/handlers" -type f -empty -delete 2>/dev/null || true
  printf '%s\n' "$version" > "$STAGING_DIR/skills/$name/version"
  printf '%s\n' "$version" > "$STAGING_DIR/skills/$name/.clawevolve-version"
  printf '%s\t%s\t%s\n' "$name" "$version" "$digest" >> "$STAGING_DIR/manifest.tsv"
done

TMP_OUTPUT="${OUTPUT}.tmp.$$"
python3 - "$STAGING_DIR" "$TMP_OUTPUT" <<'PY'
import sys
import tarfile
from pathlib import Path

staging_dir = Path(sys.argv[1])
output = Path(sys.argv[2])
with tarfile.open(output, "w", format=tarfile.GNU_FORMAT, dereference=False) as archive:
    archive.add(staging_dir / "skills", arcname="skills", recursive=True)
PY
mv "$TMP_OUTPUT" "$OUTPUT"
cp "${SCRIPT_DIR}/clawevolve_async_runner.sh" "${RELEASE_BUILD_DIR}/clawevolve_async_runner.sh"
chmod +x "${RELEASE_BUILD_DIR}/clawevolve_async_runner.sh"
cp "${SCRIPT_DIR}/clawevolve_message_initializer.sh" "${RELEASE_BUILD_DIR}/clawevolve_message_initializer.sh"
chmod +x "${RELEASE_BUILD_DIR}/clawevolve_message_initializer.sh"
cp "${SCRIPT_DIR}/adapt_openclaw_environment.py" "${RELEASE_BUILD_DIR}/adapt_openclaw_environment.py"
chmod +x "${RELEASE_BUILD_DIR}/adapt_openclaw_environment.py"
cp "${SCRIPT_DIR}/cleanup_clawevolve_openclaw_runtime.py" "${RELEASE_BUILD_DIR}/cleanup_clawevolve_openclaw_runtime.py"
chmod +x "${RELEASE_BUILD_DIR}/cleanup_clawevolve_openclaw_runtime.py"
cp "${SCRIPT_DIR}/clawevolve_runtime_cleanup.py" "${RELEASE_BUILD_DIR}/clawevolve_runtime_cleanup.py"
chmod +x "${RELEASE_BUILD_DIR}/clawevolve_runtime_cleanup.py"
cp "${SCRIPT_DIR}/clawevolve_task_launcher.sh" "${RELEASE_BUILD_DIR}/clawevolve_task_launcher.sh"
chmod +x "${RELEASE_BUILD_DIR}/clawevolve_task_launcher.sh"
cp "${SCRIPT_DIR}/clawevolve_task_log_runner.sh" "${RELEASE_BUILD_DIR}/clawevolve_task_log_runner.sh"
chmod +x "${RELEASE_BUILD_DIR}/clawevolve_task_log_runner.sh"
cp "${SCRIPT_DIR}/collect_clawevolve_task_logs.py" "${RELEASE_BUILD_DIR}/collect_clawevolve_task_logs.py"
chmod +x "${RELEASE_BUILD_DIR}/collect_clawevolve_task_logs.py"
ARCHIVE_SHA256="$(shasum -a 256 "$OUTPUT" | awk '{print $1}')"
{
  printf 'format_version\t1\n'
  printf 'release_version\t%s\n' "$RELEASE_VERSION"
  printf 'archive_file\t%s\n' "$ARCHIVE_NAME"
  printf 'archive_sha256\t%s\n' "$ARCHIVE_SHA256"
  while IFS=$'\t' read -r name version digest; do
    printf 'skill\t%s\t%s\t%s\n' "$name" "$version" "$digest"
  done < "$STAGING_DIR/manifest.tsv"
} > "${RELEASE_BUILD_DIR}/RELEASE_VERSION"
mv "$RELEASE_BUILD_DIR" "$RELEASE_DIR"
OUTPUT="${RELEASE_DIR}/${ARCHIVE_NAME}"
echo "created: $OUTPUT"
echo "release: ${RELEASE_DIR}"
awk -F '\t' '$1 == "skill" { print $2 "\t" $3 "\t" $4 }' "${RELEASE_DIR}/RELEASE_VERSION"
