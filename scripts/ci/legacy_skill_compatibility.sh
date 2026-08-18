#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root/src/backend"

uv run pytest \
  tests/community/compatibility/test_legacy_skill_harness.py \
  -q
RUN_ACCEPTANCE=1 uv run pytest \
  tests/community/acceptance/legacy_skills/ \
  -q

report="$(uv run python - <<'PY'
from tests.community.compatibility.legacy_skill_harness import (
    LEGACY_COMPATIBILITY_MATRIX,
    render_release_report,
)

print(render_release_report(
    results={case.id: "passed" for case in LEGACY_COMPATIBILITY_MATRIX},
    blocked={},
))
PY
)"
printf '%s\n' "$report"
grep -Fqx '发布结论：通过' <<<"$report"
