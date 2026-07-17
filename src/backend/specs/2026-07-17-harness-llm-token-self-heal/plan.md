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

- Store resolution inputs on the instance so the token can be re-resolved later:
  `self._explicit_token` (the `auth_token` arg), and `self._secret_name`
  (`secret_name` arg or `LLM_SECRET_NAME` env).
- Compute a permanent config flag once:
  `self._config_disabled = httpx is None or not self._base_url`.

**2. Extract resolution into `_resolve_token() -> str`.**

Moves the existing chain out of `__init__`, unchanged in priority:

```
explicit arg
  → (base_url and secret_name) ? SecretResolver.get_secret(secret_name) : skip
  → LLM_AUTH_TOKEN env
  → _decode_fallback()   # empty in shipped source
```

Resolver exceptions and `None` results both fall through to env/fallback (same as
today). Returns `""` when nothing resolves.

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

### Tests — `tests/community/core/harness/test_llm_secret_resolver.py`

- **Keep** the two existing cases (resolver-hit, resolver-None→env) — they pin the
  unchanged priority chain.
- **Add** `test_llm_recovers_when_resolver_becomes_available`: a resolver stub
  whose `get_secret` raises on call 1 and returns a real secret on call 2. Assert
  the instance is not latched disabled after init, then drive `chat()` (with the
  HTTP send stubbed) and assert the token resolved to the real value and the
  request carried `Authorization: Bearer <real>`.
- **Add** `test_llm_config_off_stays_disabled`: no `base_url` → `chat()` returns
  `[llm disabled]`, resolver never consulted.
- **Add** `test_llm_missing_token_retries_not_latched`: `base_url` set, resolver
  always returns `None`, no env token → first `chat()` returns `[llm disabled]`;
  set `LLM_AUTH_TOKEN`; next `chat()` resolves and sends. Proves no latch.

All new tests are red on unfixed HEAD (latched `_disabled`) and green with the
fix.

## Risk / blast radius

- Behavior change is strictly a **superset** of today's success path plus a new
  recovery path; the disabled sentinel and priority order are preserved.
- The only added cost is a re-resolution attempt per `chat()` **while** the token
  is unresolved (bounded: stops the moment resolution succeeds and caches).
- No shipped credential; architecture guards unaffected.
