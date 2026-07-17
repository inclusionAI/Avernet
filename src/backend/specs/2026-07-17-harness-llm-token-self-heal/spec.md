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

- A **transient** secret-resolution failure (backend not ready yet) must not
  permanently disable the LLM. Once the backend is reachable, the next `chat()`
  resolves the token and the LLM works — no worker restart required
  (self-healing).
- A **genuine config-off** state — no `base_url`, or `httpx` not importable —
  still disables the LLM exactly as today (feature-off is intentional and
  permanent; there is nothing to recover to).
- The token resolution **priority is unchanged**: explicit constructor arg →
  injected `SecretResolver` (when a `base_url` and secret name are configured) →
  `LLM_AUTH_TOKEN` env var → encoded fallback (empty in shipped source).
- **No credential may be committed** to shipped source. The neutralized
  `_FALLBACK_TOKEN_B64 = ""` stays empty; `LLM_AUTH_TOKEN` remains the
  deployment-supplied, worker-visible fallback. The fix is resilience, not a
  re-embedded secret.

## Non-goals / unchanged behavior

- The corp `ProdSecretResolver` / layotto path is **not** in this repo (corp
  overlay) and is not modified. This change makes the harness LLM *tolerant* of
  that path failing transiently; a permanent worker-side backend outage is a
  deploy concern addressed by setting `LLM_AUTH_TOKEN`.
- `chat()` request/retry/semaphore/sofa-tracer-bypass behavior is unchanged.
- The `[llm disabled]` sentinel returned to callers is unchanged.
- No change to the DI provider signature or `LLMHarnessConfig`.

## Acceptance criteria

1. Given a `base_url` and secret name, a resolver that raises on the first
   `get_secret` call but succeeds on a later call: the LLM is **not** permanently
   disabled, and a subsequent `chat()` resolves the real token and issues the
   request (regression test, red on unfixed HEAD).
2. Given a `base_url` but no resolvable token from any source: `chat()` returns
   `[llm disabled]` and makes no HTTP request (unchanged), but the instance keeps
   re-attempting resolution on later calls rather than latching.
3. Given no `base_url` (or `httpx is None`): the LLM is disabled permanently, as
   today.
4. Token resolution priority (explicit → resolver → env → fallback) is preserved:
   the two existing `test_llm_secret_resolver.py` cases still pass.
5. No committed credential in shipped source: `test_shipped_config_no_corp_identifiers.py`
   and `test_no_data_infra_vendor_in_core.py` stay green.
6. Full `tests/community` suite green.
