# Tasks — Outbound HTTP Connection Pooling and HTTP/2 for `HttpClient`

Groups are ordered so the suite stays green after each group. Group 0 lands the
dependency on its own so a lock mistake is caught before any code depends on it;
Group 1 is the pooling behavior change and is self-contained; Group 2 adds the
config path including the HTTP/2 flag; Group 3 validates.

## Group 0 — Dependency: `httpx[http2]`

- [x] 0.1 `pyproject.toml`: `"httpx>=0.27.0"` → `"httpx[http2]>=0.27.0"`, keeping
      its position in the alphabetised shared-base list. Note in the commit
      message that the block is documented as mirroring the corp manifest, so
      corp needs the same edit when this lands there.
- [x] 0.2 `uv.lock` — hand-edit, four changes (do NOT run `uv lock`; the pinned
      aliyun registry is unreachable from the sandbox and regenerating would
      rewrite every URL in the file):
      (a) root `dependencies`: `{ name = "httpx" }` → `{ name = "httpx", extra = ["http2"] }`;
      (b) root `[package.metadata] requires-dist`: add `extras = ["http2"]` to the httpx line;
      (c) the `httpx` package entry gains `[package.optional-dependencies]` with
      `http2 = [{ name = "h2" }]`, in the shape the `uvicorn` `standard` extra already uses;
      (d) three new alphabetically-placed `[[package]]` blocks — `h2` 4.4.1
      (dependencies: `hpack`, `hyperframe`), `hpack` 4.2.0, `hyperframe` 6.1.0 —
      each `source = { registry = "https://mirrors.aliyun.com/pypi/simple" }`
      with the sdist + wheel URLs and sha256 hashes recorded in `plan.md`
      Component 0.
- [x] 0.3 Verify the lock is internally consistent: `uv lock --check`. If the
      mirror is unreachable for that command too, fall back to
      `uv pip install "httpx[http2]"` in the sandbox venv and state plainly in
      the PR that `uv lock --check` could not be run locally and CI is the gate.
- [x] 0.4 Confirm `import h2` works and `httpx.Client(http2=True)` constructs in
      the venv.

## Group 1 — Pool the client

- [x] 1.1 `plugins/http_client.py`: add `threading` import, `LifecycleBase`
      import, and the module-level `DEFAULT_MAX_CONNECTIONS = 100`,
      `DEFAULT_MAX_KEEPALIVE_CONNECTIONS = 20`, `DEFAULT_KEEPALIVE_EXPIRY = 5.0`,
      `DEFAULT_HTTP2 = False` constants (mirroring `HttpClientPoolConfig`).
- [x] 1.2 `HttpxClient` declares `LifecycleBase` as a base
      (`class HttpxClient(LifecycleBase, HttpClient)`); `__init__` **drops the
      `transport` parameter**, gains the four keyword-only policy arguments,
      builds `self._limits = httpx.Limits(...)`, stores `self._http2`, and
      initialises `self._lock = threading.Lock()` /
      `self._client: httpx.Client | None = None`. `base_url` is unchanged.
- [x] 1.3 Add `_pooled_client()` — double-checked lazy construction under the
      lock, passing `base_url` + `limits` + `http2`. No `client_kwargs` dict and
      no conditional guard remain. The lock guards construction only and is never
      held across a request.
- [x] 1.4 Add `close()` (swap `self._client` to `None` under the lock, close the
      old client *outside* the lock, idempotent) and `async def teardown()`
      delegating to it.
- [x] 1.5 `_request`: delete the `client_kwargs` / `with httpx.Client(...)`
      block; return
      `self._pooled_client().request(method, path, timeout=timeout, **kwargs)`.
      The `None`-omitting `kwargs` assembly above it is unchanged.
- [x] 1.6 `stream`: same substitution —
      `with self._pooled_client().stream(method, path, timeout=timeout, **kwargs) as resp: yield resp`.
      The client-closing outer `with` is removed; the inner one stays so the
      connection returns to the pool.
- [x] 1.7 Rewrite the module docstring: pooled design, the `keepalive_expiry`
      vs upstream-idle-timeout hazard, streams occupying pool slots, and the
      HTTP/2 semantics (ALPN-only negotiation, inert against plaintext
      upstreams, off by default). Update the class docstring and the `stream`
      docstring line that still says "Mirrors `post`'s short-lived-client
      pattern".
- [ ] 1.8 Rewrite `tests/community/plugins/test_http_client.py` per the plan's
      test list: pool reuse, configured limits, http2 default-off + forwarded,
      per-request timeout, absolute URL bypasses `base_url`, `None`-omission,
      multipart kwargs, verb dispatch, error propagation,
      stream-leaves-pool-open, idempotent close, teardown closes, concurrent
      first calls build one client.
- [ ] 1.8b `plugin_api/http_client.py`: fix the now-false impl note in the module
      docstring — "``prod`` → ``HttpxClient(base_url)`` — a real ``httpx.Client``
      per call." becomes a pooled-client description. Docstring only; the
      Protocol, its signatures and the conformance contract are untouched, so
      this is not a contract change.
- [ ] 1.8c Delete `tests/community/core/harness/services/test_http_client_stream.py`.
      Its two `HttpxClient` cases depend on the removed `transport` seam. First
      move its third case, `test_local_http_client_stream_raises` (which never
      used `transport`), into `tests/community/contracts/test_http_client.py`
      alongside the rest of the local-impl contract, so it is not lost with the
      file. Confirm nothing else imports from the deleted module.
- [ ] 1.9 Run `tests/community/plugins/test_http_client.py` and
      `tests/community/contracts/test_http_client.py`. Both green. Note in the
      PR that `HttpxClient.stream()` now has no automated coverage — a
      deliberate trade recorded in `spec.md`, not an oversight.

## Group 2 — Make the ceilings and the protocol configurable

- [ ] 2.1 `di/config.py`: add the `# ── Outbound HTTP transport ──` section
      between the CORS block and `# ── Object storage ──`, holding frozen
      `HttpClientPoolPolicy(max_connections=100, max_keepalive_connections=20,
      keepalive_expiry=5.0, http2=False)` and frozen `HttpClientPoolConfig`
      (`defaults` + `overrides` mapping + `for_qualifier(name)`). Policy
      docstring states: ceilings are per upstream client not process-wide;
      exceeding `max_connections` surfaces as `HttpClientTimeoutError`
      (`httpx.PoolTimeout`); `keepalive_expiry` must stay below the upstream idle
      timeout; `http2` engages only against TLS upstreams offering `h2` via ALPN.
      Config docstring states that `for_qualifier` resolves whole-policy, and why
      (a half-specified override must not drift as shared defaults change).
- [ ] 2.2 `di/modules/config_module.py`: add the module-level `_pool_policy`
      helper and the `http_client_pool` provider next to `masa_agent_eval`.
      Provider reads `_block("http_client")` for the shared defaults, then builds
      each `overrides.<qualifier>` policy *starting from those defaults* so a
      sparse override resolves total. Non-dict override bodies are skipped.
- [ ] 2.3 `di/modules/http_client_module.py`: all four providers take
      `pool: cfg.HttpClientPoolConfig`, call `pool.for_qualifier(<their own
      QUALIFIER_* constant>)`, and forward the four policy values to
      `HttpxClient`; `general_http_client` gains `@inject`. Extend each
      `logger.info` to record the resolved ceiling and HTTP/2 flag next to the
      base_url — this is both how a pre environment confirms an `http2` flip took
      effect and the mitigation for a mistyped override key silently doing
      nothing.
- [ ] 2.4 Update the two direct-call test sites broken by 2.3 —
      `tests/community/di/modules/test_http_client_module_bcn.py` and
      `tests/community/di/modules/test_infrastructure_module.py` — to pass an
      explicit `cfg.HttpClientPoolConfig()`. In the BCN file, add cases asserting
      a non-default `defaults` policy (limits *and* `http2=True`) reaches the
      client, and that an override keyed to a *different* qualifier does not.
- [ ] 2.4b New `tests/community/di/modules/test_config_module_http_client.py`:
      missing block ⇒ dataclass defaults everywhere; a top-level block sets the
      shared defaults; a sparse override inherits unset fields from those
      defaults rather than from the dataclass; `for_qualifier` on an unlisted
      qualifier returns the defaults; a non-dict override body is ignored.
- [ ] 2.5 `configs/application-community.yaml`: commented `http_client` block
      under `user_config` documenting the four keys and their defaults, plus a
      commented `overrides:` example showing a per-qualifier flip
      (`baas: { http2: true }`) and the four valid qualifier keys. One-line note
      that `http2` engages only against TLS upstreams offering `h2`.
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
- [ ] 3.2 Run `tests/community/endpoints/test_session_resources.py` — now the
      **only** test that drives a real `HttpxClient` over a real socket, and
      therefore the sole end-to-end proof the pooled path works at all. Treat a
      failure here as blocking, not incidental.
- [ ] 3.3 Run `tests/community/architecture/` — module boundaries (the new
      `kernel.lifecycle` import from `plugins/`), lifecycle discovery, the
      Rule 20/21 plugin-pairing test, and `test_local_no_external_deps.py`
      (the new `h2` dependency).
- [ ] 3.4 Run the full backend unit suite (`scripts/ci_test.sh` or
      `pytest tests/community`) and `ruff check` on the touched files. Record
      the counts; state explicitly anything that could not be run and why.

## Follow-ups for the human (not code tasks)

- [x] F1 Confirm the origins offer `h2`. **`secbaas-prod.alipay.com` → `ALPN
      protocol: h2`**, probed from inside the corp network. (A sandbox probe is
      worthless here — its TLS is terminated by an egress gateway that reports
      its own ALPN.)
- [x] F1b Same probe against `agentclawproxy-prod.alipay.com` — **`ALPN
      protocol: h2`**. Both target upstreams negotiate HTTP/2, so F3's flip has a
      known payoff. Scope note: agentclawproxy is a proxy, so this covers the
      backend→proxy hop (the one this pool holds), not proxy→container.
- [ ] F2 Mirror the `pyproject.toml` httpx-extra change into the corp manifest,
      which the community manifest documents itself as tracking by hand.
- [ ] F3 Roll `http2: true` out per environment AND per qualifier (pre first),
      watching for interactions with the out-of-repo httpx send-hook wrapper,
      which community and singlebox CI cannot exercise. Suggested order:
      `overrides.baas` first (single origin, plain request/response), then the
      other single-origin bindings, and `general` last since it carries the LLM
      SSE streams alongside container calls.

## Out of scope (do not do)

- Cleartext `h2c` / prior-knowledge HTTP/2 (would require disabling HTTP/1.1).
- Retry or circuit-breaking on `RemoteProtocolError` from a stale keep-alive
  connection. The seam's "swallows nothing" invariant stands.
- Touching `plugin_api/http_client.py`, `LocalHttpClient`, or the `test` /
  `corp_test` profile bindings.
