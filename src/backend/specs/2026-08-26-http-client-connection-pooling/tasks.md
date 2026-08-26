# Tasks — Outbound HTTP Connection Pooling for `HttpClient`

Groups are ordered so the suite stays green after each group. Group 1 is the
whole behavior change and is self-contained (the constructor keeps working
defaults, so nothing else has to move with it); Group 2 adds the config path;
Group 3 validates.

## Group 1 — Pool the client

- [ ] 1.1 `plugins/http_client.py`: add `threading` import, `LifecycleBase`
      import, and the module-level `DEFAULT_MAX_CONNECTIONS = 100`,
      `DEFAULT_MAX_KEEPALIVE_CONNECTIONS = 20`, `DEFAULT_KEEPALIVE_EXPIRY = 5.0`
      constants (mirroring `HttpClientPoolConfig`'s field defaults).
- [ ] 1.2 `HttpxClient` declares `LifecycleBase` as a base
      (`class HttpxClient(LifecycleBase, HttpClient)`); `__init__` gains the
      three keyword-only pool arguments, builds `self._limits =
      httpx.Limits(...)`, and initialises `self._lock = threading.Lock()` /
      `self._client: httpx.Client | None = None`. `base_url` and `transport`
      keep their current meaning.
- [ ] 1.3 Add `_pooled_client()` — double-checked lazy construction under the
      lock, passing `base_url` + `limits` (+ `transport` only when set). The
      lock guards construction only and is never held across a request.
- [ ] 1.4 Add `close()` (swap `self._client` to `None` under the lock, close the
      old client *outside* the lock, idempotent) and `async def teardown()`
      delegating to it.
- [ ] 1.5 `_request`: delete the `client_kwargs` / `with httpx.Client(...)`
      block; return
      `self._pooled_client().request(method, path, timeout=timeout, **kwargs)`.
      The `None`-omitting `kwargs` assembly above it is unchanged.
- [ ] 1.6 `stream`: same substitution —
      `with self._pooled_client().stream(method, path, timeout=timeout, **kwargs) as resp: yield resp`.
      The client-closing outer `with` is removed; the inner one stays so the
      connection returns to the pool.
- [ ] 1.7 Rewrite the module docstring: pooled design, the `keepalive_expiry`
      vs upstream-idle-timeout hazard, streams occupying pool slots, and why
      HTTP/2 is deliberately absent (ALPN-only negotiation, plaintext
      upstreams). Update the class docstring and the `stream` docstring line
      that still says "Mirrors `post`'s short-lived-client pattern".
- [ ] 1.8 Rewrite `tests/community/plugins/test_http_client.py` per the plan's
      test list: pool reuse, configured limits, per-request timeout, absolute
      URL bypasses `base_url`, `None`-omission, multipart kwargs, verb
      dispatch, error propagation, stream-leaves-pool-open, idempotent close,
      teardown closes, concurrent first calls build one client.
- [ ] 1.9 Run `tests/community/plugins/test_http_client.py` and
      `tests/community/core/harness/services/test_http_client_stream.py`
      (the latter unedited — it is the regression guard). Both green.

## Group 2 — Make the ceilings configurable

- [ ] 2.1 `di/config.py`: add the `# ── Outbound HTTP transport ──` section
      between the CORS block and `# ── Object storage ──`, holding frozen
      `HttpClientPoolConfig(max_connections=100, max_keepalive_connections=20,
      keepalive_expiry=5.0)`. Docstring states: ceilings are per upstream
      client not process-wide; exceeding `max_connections` surfaces as
      `HttpClientTimeoutError` (`httpx.PoolTimeout`); `keepalive_expiry` must
      stay below the upstream idle timeout. No `http2` field.
- [ ] 2.2 `di/modules/config_module.py`: add the `http_client_pool` provider
      next to `masa_agent_eval`, reading `_block("http_client")` with
      dataclass-default fallbacks per the file's existing idiom.
- [ ] 2.3 `di/modules/http_client_module.py`: all four providers take
      `pool: cfg.HttpClientPoolConfig` and forward the three values to
      `HttpxClient`; `general_http_client` gains `@inject`. Extend each
      `logger.info` to record the pool ceiling next to the base_url.
- [ ] 2.4 Update the two direct-call test sites broken by 2.3 —
      `tests/community/di/modules/test_http_client_module_bcn.py` and
      `tests/community/di/modules/test_infrastructure_module.py` — to pass an
      explicit `cfg.HttpClientPoolConfig()`. In the BCN file, add a case
      asserting a non-default config reaches the client's `_limits`.
- [ ] 2.5 `configs/application-community.yaml`: commented `http_client` block
      under `user_config`, documenting the three keys and their defaults.
- [ ] 2.6 Run the DI module tests plus
      `tests/community/di/test_profile_and_modules_for.py` — the container, not
      just direct calls, satisfies the new provider argument.

## Group 3 — Validate the blast radius

- [ ] 3.1 Run the consumer tests that exercise this seam:
      `tests/community/contracts/test_http_client.py`,
      `tests/community/core/service_bot/services/` (BaasService),
      `tests/community/core/harness/services/` (LLM + streaming),
      `tests/community/core/bot_management/services/` (BCN),
      `tests/community/core/quality/test_task_processor.py`,
      `tests/community/core/bot_dormant/`.
- [ ] 3.2 Run `tests/community/endpoints/test_session_resources.py` — the one
      test that drives a real `HttpxClient` over a real socket, so it is the
      only local end-to-end proof the pooled path works outside a mock
      transport.
- [ ] 3.3 Run `tests/community/architecture/` — module boundaries (the new
      `kernel.lifecycle` import from `plugins/`), lifecycle discovery, and the
      Rule 20/21 plugin-pairing test.
- [ ] 3.4 Run the full backend unit suite (`scripts/ci_test.sh` or
      `pytest tests/community`) and `ruff check` on the touched files. Record
      the counts; state explicitly anything that could not be run and why.

## Out of scope (do not do)

- HTTP/2 / `h2` dependency / `http2` config knob — see `spec.md`.
- Retry or circuit-breaking on `RemoteProtocolError` from a stale keep-alive
  connection. The seam's "swallows nothing" invariant stands.
- Per-qualifier pool tuning.
- Touching `plugin_api/http_client.py`, `LocalHttpClient`, or the `test` /
  `corp_test` profile bindings.
