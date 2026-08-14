# Tasks: rollback stored channel overrides (#168)

- [x] 1. Apply the fix to `publish_flow/rollback_ops_mixin.py::execute_rollback` —
  stored-slot composition via `_artifact_for_stage` before `upgrade_async`.
- [x] 2. Unit tests in `test_publish_flow_service.py`: new
  `test_execute_rollback_delivers_stored_online_overrides_not_live`; rework
  `test_execute_rollback_with_config_artifact` into the dict-artifact
  backward-compat case.
- [x] 3. Endpoint test in `test_service_bot_rollback.py`: seed helper
  (`_seed_v2_success_with_v1_channel_overrides`), `/update`-payload assertion,
  scenario `rollback_delivers_stored_online_channel_overrides`.
- [x] 4. Verify red→green: new tests fail on unfixed HEAD (demonstrated during
  triage), pass with the fix (119 unit / 12 endpoint scenarios green).
- [x] 5. Run targeted suites: publish-flow unit tests + rollback & per-stage
  channel endpoint scenarios.
- [x] 6. Run full `tests/community` suite (7803 passed, 3 skipped).
- [ ] 7. Commit to `claude/jolly-ptolemy-86ti1s` and push
  (`AVERNET_PRE_PUSH_MERGE_TARGET=origin/REL20260715`).
- [ ] 8. Open draft PR → base `REL20260715`, `Fixes #168`; subscribe to PR
  activity.
