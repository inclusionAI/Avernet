# Spec: Harness LLM must not latch DISABLED when the secret backend is briefly unavailable

**Issue:** inclusionAI/Avernet#201

## Problem (WHAT is broken)

On prod, the harness LLM logs, across every `SpawnProcess-N` worker:

```
[LLM] LLM is DISABLED: base_url='https://antchat.alipay.com', token=MISSING, httpx=ok
```

The endpoint is configured and `httpx` imports fine — only the **token** is
missing, so `chat()` returns `[llm disabled]` for the whole life of the worker.

`core/harness/services/llm.py` resolves the API token **once, eagerly, in the
constructor**, then stores `self._disabled` permanently. In prod the injected
`SecretResolver` is the corp resolver, which reads the credential through the
`get_layotto_manager()` global singleton. When that lookup fails in a
`SpawnProcess` worker (the "mist parse error" in the issue title), the exception
is swallowed and the token falls through to
`os.getenv("LLM_AUTH_TOKEN") or _decode_fallback()`. Both are empty — the
committed `_FALLBACK_TOKEN_B64` was intentionally blanked when the utility was
neutralized (`0a1b2f6c28`). So `self._token == ""`, `self._disabled = True`, and
that verdict is **latched**: even after the secret backend becomes reachable, the
already-constructed singleton never re-resolves and the worker stays disabled.

## Why it matters

The harness LLM powers diagnostics (behavior boundaries, fail-first, safety
rules, tool declaration, MCP format) and patch planning. A single unlucky secret
lookup at construction time silently disables all of it for the worker's entire
lifetime, with no recovery and no error surfaced to callers — they just receive
`[llm disabled]` text. On prod this took out LLM across five worker processes.

## Desired behavior

The original latch bug had two ingredients: the token was resolved through a
fragile global (`get_layotto_manager()`) and, on failure, fell through to an
empty committed fallback — so a single miss produced a permanent `token=MISSING`.
The fix removes both ingredients and the whole fragile-timing surface:

- The token is resolved **once, at construction, solely through the injected
  `SecretResolver`**, keyed by the DI-provided `secret_name`. The constructor
  reads **no process environment** and bakes in **no fallback credential** — an
  unresolved secret yields `token is None`, which disables the LLM (`chat()`
  returns `[llm disabled]`), it never falls back to a committed token.
- Construction is **not** fragile-timing-sensitive: the DI provider binds the LLM
  as a lazy `@singleton`, so it is built on first use — well after boot, when the
  `SecretResolver` is ready — not during early startup. It is **not** in
  `eager_check_critical_bindings`. So the resolver succeeds at construction in the
  normal path; there is no need to re-resolve, and none is done — the injected
  resolver behaves identically on every call.
- HTTP goes through the injected `HttpClient` (the shared `general` sync client),
  which sofa_tracer does not patch, so the SpawnProcess `AsyncClient.send`
  workaround is gone.

## Non-goals / unchanged behavior

- The corp `ProdSecretResolver` / layotto path is **not** in this repo (corp
  overlay) and is not modified. Whether the resolver can reach the secret in a
  given worker is a deploy concern (the corp env overlay wires a worker-reachable
  `secret_name`); this class faithfully surfaces "no token ⇒ disabled".
- `chat()` request/retry/semaphore behavior is unchanged.
- The `[llm disabled]` sentinel returned to callers is unchanged.

## Constructor hardening (same change)

- `base_url` and `secret_name` are required `str` (the DI provider always
  supplies them from `LLMHarnessConfig`), not `str | None`.
- `model` / `timeout_ms` carry literal defaults (`"GLM-5.1"` / `180_000`); the
  `LLM_MODEL` / `LLM_TIMEOUT_MS` env reads are gone.
- The `auth_token` param and all `LLM_*` env reads are removed; the DI provider
  passes `llm_config.base_url` / `llm_config.secret_name` directly.
- `HttpClient` and `SecretResolver` are imported directly (no `TYPE_CHECKING`
  guard — verified no import cycle: `plugin_api.{http_client,secret_resolver}`
  depend only on `plugin_api.base`, never on `core.harness`).

## Acceptance criteria

1. A resolver that returns a secret → `_token` is the token; `chat()` posts the
   OpenAI-shaped body (model + timeout + `Authorization: Bearer <token>`) to
   `{base_url}/v1/chat/completions` through the injected `HttpClient`.
2. A resolver that reports the secret absent, or raises → `_token is None`
   (no crash, no baked fallback); `chat()` returns `[llm disabled]`, makes no HTTP
   call, and does **not** consult the resolver again (resolution happens once).
3. The constructor reads no env var and resolves the token only through the
   `SecretResolver`.
4. No committed credential in shipped source: `test_shipped_config_no_corp_identifiers.py`
   and `test_no_data_infra_vendor_in_core.py` stay green.
5. The full injector still eagerly resolves the harness LLM
   (`test_all_bindings_resolve`).
6. Full `tests/community` suite green.
