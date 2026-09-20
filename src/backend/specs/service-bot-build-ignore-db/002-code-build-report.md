---
agent: tc-code
status: completed
created: 2026-09-21T00:15:00+08:00
iteration: 1
---

# Build integration report

Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/service-bot-publish-ignore-ops-rel20260917`

Branch: `feat/service-bot-build-ignore-db`

## Changes

- `services/build_ignore_rules.py`: exact subtree matching, extra-root mapping and required generated/input configuration protection; shared with the API service.
- `services/bot_build_service.py`: required repository/environment injection, one immutable snapshot per build, persistence failure abort, normalization and required-file validation before copy. Apply anchored excludes to primary rsync, filter extra includes before filesystem probes/device fallback, and map or skip extra roots. Historical restore does not query current rules.
- `services/deploy/arca_snapshot_producer.py`: carry the actual successful build snapshot into artifact ext alongside existing fields.
- `tests/.../test_bot_build_ignore.py`: 18 focused cases, including real temporary-directory rsync, stale artifact cleanup, unchanged source, exact-prefix matching, extra-root isolation, skipped probe/fallback, fixed snapshots, DB failure, required configuration protection, producer success/failure and diagnostic events.

## Verification

TDD: three copy tests failed before new arguments/filtering; eight build/snapshot tests failed before snapshot capture and producer forwarding; two transfer-log cases failed before diagnostic events. All then passed.

Focused regression command (from `src/backend`):

```sh
.venv/bin/python -m pytest tests/community/core/service_bot/services/test_bot_build_ignore.py tests/community/core/service_bot/services/test_bot_build_service_rsync_excludes.py tests/community/core/service_bot/services/test_bot_build_service_extra_sync.py tests/community/core/service_bot/services/test_bot_build_service_skill_artifact.py tests/community/core/service_bot/services/test_bot_build_service_draft_restore.py tests/community/core/service_bot/test_publish_ignore_service.py tests/community/api/test_publish_ignore_router.py tests/community/core/service_bot/services/deploy/test_arca_snapshot_producer.py -q --tb=short
```

Result: **144 passed**, 17 existing Pydantic deprecation warnings. Actual rsync executed without sudo in temporary directories; no real Bot, database or environment changed.

`git diff --check` clean; Ruff on the new helper and new test module passed. Full Backend and changed-line coverage are delegated to independent pipeline validation, not claimed here.

## Diagnostics and scope

`build_ignore.snapshot` records Bot/entity/engine/version, revision/count, duration, success/failure; database failure only records exception type and raises `build_ignore_snapshot_failed` without original error chaining. `build_ignore.transfer` records aggregate copy outcome tied to the frozen revision/version and duration. Existing command-level logs remain unchanged. Tests cover successful and failed transfer and rejection of secret-bearing DB errors without logging their bodies.

No Engine/DaaS edits, no runtime exclude changes, no absolute-path feature, no deployment or commit from this worker. Existing BotBuildService fixture constructor updates were coordinated with the main agent. The already oversized build module remains on its existing allowlist; new reusable rules are isolated in the small helper.
