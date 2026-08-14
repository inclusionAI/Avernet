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

## Review follow-up 2 (inject the shared HttpClient; drop config-disable)

- [x] 15. `llm.py`: inject `HttpClient` (the `general` sync client); delete the
  `try/except import httpx`, the `_ORIGINAL_ASYNC_SEND` / sofa_tracer bypass, and
  the `httpx.AsyncClient` path. `_do_request` now calls `self._http.post(...)` via
  `asyncio.to_thread`.
- [x] 16. Remove the config-disable concept entirely (`_config_disabled` /
  `_disabled` gone). `chat()` short-circuits only while the token is unresolved
  (the #201 self-heal), returning the unchanged `[llm disabled]` sentinel.
- [x] 17. `_resolve_token`: drop the `base_url`/`secret_name` guard (DI always
  provides them); resolve straight through the `SecretResolver`.
- [x] 18. `harness_module._llm`: `@inject` the
  `Annotated[HttpClient, QUALIFIER_GENERAL]` and pass `http_client=`.
- [x] 19. Declare `plugin_api.http_client` in `core/harness/README.md`
  (module-boundary manifest); refresh `di/config.py` docstring.
- [x] 20. Rework tests onto a recording `HttpClient` double (exercise the real
  `_do_request`/`to_thread` path); keep recovery/retry coverage. Verify the full
  injector still eagerly resolves the harness LLM (`test_all_bindings_resolve`).
- [x] 21. ruff clean; full `tests/community` green; commit & push; PR updates.

## Review follow-up 3 (no fallback, no re-resolve, direct imports)

- [x] 22. Import `HttpClient` / `SecretResolver` directly (no `TYPE_CHECKING`) —
  verified no import cycle (`plugin_api.{http_client,secret_resolver}` →
  `plugin_api.base` only).
- [x] 23. Delete `_FALLBACK_TOKEN_B64` / `_decode_fallback` / `import base64`;
  `_resolve_token` returns `str | None` (None = unresolved, no baked credential).
- [x] 24. Remove the `chat()` re-resolve — resolution happens once at (lazy)
  construction; `chat()` only checks the token and returns `[llm disabled]` when
  it is None. Confirmed the LLM `@singleton` is not in
  `eager_check_critical_bindings`, so construction is post-boot.
- [x] 25. Rework tests: absent/raising resolver → `_token is None`; disabled
  `chat()` makes no HTTP and does not re-resolve; happy-path request-shape assert.
- [x] 26. ruff clean; full `tests/community` green; commit & push; PR updates.
