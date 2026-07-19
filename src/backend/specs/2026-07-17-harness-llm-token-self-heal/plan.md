# Plan: lazy, self-healing token resolution in the harness LLM

## Root cause recap

`LLM.__init__` collapses two independent facts into one latched boolean:

- **Config-off** (permanent): no `base_url`, or `httpx` unimportable. Nothing to
  recover to.
- **Token-missing** (potentially transient): the secret backend failed *this
  once*. Recoverable.

Today both feed `self._disabled = not self._base_url or not self._token or httpx
is None`, computed once. A transient token miss is thus indistinguishable from a
permanent feature-off, and it sticks for the worker's lifetime.

## Change

### `core/harness/services/llm.py`

**1. Separate the two states.**

- Store the token's secret key on the instance so the token can be re-resolved
  later: `self._secret_name` (the required `secret_name` arg).
- Compute a permanent config flag once:
  `self._config_disabled = httpx is None or not self._base_url`.

**2. Extract resolution into `_resolve_token() -> str`.**

Resolves the token solely through the injected `SecretResolver`, keyed by
`secret_name`:

```
(base_url and secret_name) ? SecretResolver.get_secret(secret_name) : skip
  → _decode_fallback()   # empty in shipped source
```

Resolver exceptions and `None` results both fall through to the fallback (never
raised). Returns `""` when nothing resolves. The constructor reads no env var —
endpoint/secret key/model/timeout all arrive from the DI provider.

**3. Best-effort eager resolve + honest logging in `__init__`.**

```python
self._token = self._resolve_token()
if self._config_disabled:
    logger.warning("[LLM] LLM is DISABLED (feature-off): base_url=%r, httpx=%s", ...)
elif self._token:
    logger.info("[LLM] LLM enabled: base_url=%s, model=%s", ...)
else:
    # endpoint present, token not yet resolvable — recoverable, not latched
    logger.warning(
        "[LLM] token unresolved at init (base_url=%r) — will retry on first use",
        self._base_url,
    )
```

**4. `chat()` self-heals.**

```python
if self._config_disabled:
    return "[llm disabled]"
if not self._token:
    self._token = self._resolve_token()   # retry; caches on success
    if not self._token:
        logger.warning("[LLM] token still unresolved, returning [llm disabled]")
        return "[llm disabled]"
...
```

Once a retry succeeds, `self._token` is cached and no further resolution happens.
The permanent config-off path returns the identical sentinel with no HTTP call.

**5. Keep a read-only `self._disabled` property** for backward compatibility with
any external reader (none in-tree today, but the log/message contract referenced
it): `self._config_disabled or not self._token`. This reflects *current*
resolvability rather than a latched snapshot.

No change to `_decode_fallback`, `_FALLBACK_TOKEN_B64` (stays `""`), the retry
loop, the semaphore, or the sofa-tracer bypass.

**6. Constructor hardening (review follow-up).** Drop the `str | None` / env-read
surface: `base_url` and `secret_name` become required `str` (the DI provider
always supplies them); `model` / `timeout_ms` get literal defaults
(`"GLM-5.1"` / `180_000`); the `auth_token` param and all `LLM_*` env reads are
removed. `import os` drops out of the module.

### `di/modules/harness_module.py` — `_llm` provider

Pass config values directly, no env override and no `or None`:

```python
return LLM(
    base_url=llm_config.base_url,
    secret_name=llm_config.secret_name,
    secret_resolver=secret_resolver,
)
```

Stale `LLM_*` env-var mentions in `di/config.py`, `application-community.yaml`,
and a commented router block are updated to describe the resolver path.

### Tests — `tests/community/core/harness/test_llm_secret_resolver.py`

- **Keep** `test_llm_loads_token_from_secret_resolver` (resolver-hit).
- **Rework** the resolver-None case into
  `test_llm_token_empty_when_resolver_absent_no_baked_fallback`: resolver returns
  `None`, no baked fallback → empty token / disabled, resolver consulted exactly
  once (proves no env read).
- **Add** `test_llm_recovers_when_resolver_becomes_available`: a resolver whose
  `get_secret` raises on call 1 and returns a real secret on call 2. Assert not
  latched after init, then drive `chat()` (HTTP send stubbed) and assert the token
  resolved and the request carried `Authorization: Bearer <real>`.
- **Add** `test_llm_config_off_stays_disabled`: `base_url=""` → `chat()` returns
  `[llm disabled]`, resolver never consulted.
- **Add** `test_llm_missing_token_retries_not_latched`: resolver returns `None`
  until a flag flips → first `chat()` returns `[llm disabled]`; flip the backend
  on; next `chat()` resolves and sends. Proves no latch, no env.

All recovery/retry tests are red on unfixed HEAD (latched `_disabled`) and green
with the fix.

## Review follow-up 2 — inject the shared `HttpClient`, drop config-disable

Per review, route HTTP through the DI transport seam and delete the tracer
workaround and the config-disable concept:

- `LLM.__init__` takes `http_client: HttpClient` (the `general` sync client,
  `Annotated[HttpClient, QUALIFIER_GENERAL]`). The module-level
  `try/except import httpx`, the `_ORIGINAL_ASYNC_SEND` / `sofa_tracer` bypass,
  and the `httpx.AsyncClient` path are all removed — the sync `httpx.Client`
  the plugin uses is not tracer-patched, so there is nothing to work around.
- `_do_request` becomes `await asyncio.to_thread(self._http.post, url, json=…,
  headers=…, timeout=…)` — the sync call runs off the event loop; the semaphore
  and retry loop are unchanged. The `general` client has an empty base_url, so we
  pass the absolute `f"{base_url}/v1/chat/completions"`.
- The config-disable states are gone: `httpx is None` can't happen (plugin always
  injected), and feature-off collapses into the token path — an unconfigured
  deployment resolves no secret, so `chat()` returns `[llm disabled]` on the
  empty-token check. `_config_disabled` and the `_disabled` property are deleted;
  `_resolve_token` drops its `base_url`/`secret_name` guard.
- `harness_module._llm` `@inject`s the qualified client and passes `http_client=`.
  `core/harness/README.md` declares the new `plugin_api.http_client` dependency
  (module-boundary manifest).

Tests move onto a recording `HttpClient` double so `chat()` exercises the real
`_do_request` / `to_thread` path; `test_all_bindings_resolve` still eagerly
resolves the harness LLM in the full injector.

## Review follow-up 3 — drop the fallback and the re-resolve; direct imports

Final shape after review. The self-healing re-resolve is removed as unnecessary,
and the baked fallback is deleted outright:

- **No re-resolve.** The LLM `@singleton` is bound lazily and is *not* in
  `eager_check_critical_bindings`, so it is constructed on first use — after boot,
  when the injected `SecretResolver` is ready. The resolver therefore succeeds at
  construction in the normal path, and since the injected resolver returns the
  same answer on every call, re-resolving in `chat()` cannot change the outcome.
  `chat()` now only checks the token and returns `[llm disabled]` when it is None.
- **No fallback.** `_FALLBACK_TOKEN_B64` / `_decode_fallback` / `import base64`
  are deleted. `_resolve_token` returns `str | None` (None = secret absent or the
  lookup raised). A baked fallback is a committed credential by another name;
  removing it means an unresolved secret simply disables the LLM.
- **Direct imports.** `HttpClient` / `SecretResolver` move out of the
  `TYPE_CHECKING` guard to real module-level imports — verified acyclic
  (`plugin_api.{http_client,secret_resolver}` import only `plugin_api.base`).

## Risk / blast radius

- The `[llm disabled]` sentinel and the request path are preserved; the only
  behavioral change vs the previous round is that a construction-time miss is no
  longer retried on later `chat()` calls (by design — retrying the injected
  resolver can't change its answer).
- `_token` is `str | None`; None is an intentional "disabled" state, so the
  optional type is contract-faithful (not a stray `T | None`).
- No shipped credential; architecture guards unaffected. The harness reads no
  `LLM_*` env var and constructs no `httpx` client directly.
