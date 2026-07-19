# Tasks: harness LLM token self-heal (#201)

- [x] 1. Refactor `core/harness/services/llm.py`: split permanent config-off
  (`_config_disabled`) from recoverable token-missing; extract `_resolve_token()`
  (unchanged priority chain); best-effort eager resolve + honest init logging.
- [x] 2. Make `chat()` self-heal: re-resolve the token when missing (caches on
  success), return the unchanged `[llm disabled]` sentinel with no HTTP call when
  config-off or still unresolved.
- [x] 3. Add `_disabled` read-only property reflecting current resolvability
  (config-off or no token) for backward compat.
- [x] 4. Tests in `test_llm_secret_resolver.py`: add recovery,
  config-off-stays-disabled, and missing-token-retries-not-latched cases; keep the
  two existing priority-chain cases.
- [x] 5. Verify red→green: new recovery/retry tests fail on unfixed HEAD, pass
  with the fix.
- [x] 6. Run targeted suite: `tests/community/core/harness/` +
  `tests/community/architecture/` (shipped-config + no-vendor guards).
- [x] 7. Run full `tests/community` suite.
- [x] 8. Commit to `claude/issue-201-sdd-o845st` and push
  (`AVERNET_PRE_PUSH_MERGE_TARGET=origin/REL20260717`).
- [x] 9. Open draft PR → base `REL20260717`, `Fixes #201`; subscribe to PR
  activity.

## Review follow-up (constructor hardening)

- [x] 10. `llm.py`: `base_url` / `secret_name` required `str`; `model` /
  `timeout_ms` literal defaults; drop `auth_token` param and all `LLM_*` env
  reads (token resolved only via `SecretResolver`); remove `import os`.
- [x] 11. `harness_module._llm`: pass `llm_config.base_url` / `secret_name`
  directly (no env, no `or None`).
- [x] 12. Refresh stale `LLM_*` env-var docs in `di/config.py`,
  `application-community.yaml`, and the commented router block.
- [x] 13. Update tests for the new signature (required `base_url`, resolver-only
  token, no env); keep red→green recovery/retry coverage.
- [x] 14. Re-run targeted + full `tests/community`; ruff clean; commit & push;
  PR updates in place.
