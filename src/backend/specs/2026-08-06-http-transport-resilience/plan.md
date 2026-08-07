# Plan — Backend HTTP Transport Resilience

Implements `spec.md`. Three code changes plus their tests and two boundary
declarations.

## Architecture

```
agentclaw.community.utils.retry            ← NEW: the reusable component
        ▲                    ▲
        │                    │
core.service_bot         core.harness
  baas_service             llm.py
  get_http_info          (aliases its private
  (adopts retry)          helpers to the shared ones)

plugins.http_client.HttpxClient            ← independent change: pooled client
```

`utils.retry` is a leaf: it imports only the stdlib and `agentclaw.community.log`.
It has no knowledge of BaaS, skills, or `HttpClient`, so any backend module can
adopt it. This is why it goes under `utils/` rather than beside either caller.

## Placement rationale

`agentclaw.community.utils` is not in `BOUNDARY_SIGNIFICANT_MODULES`
(`tests/community/architecture/test_module_boundaries.py:61-95`), so the new
module needs no `README.md` of its own. Both *importers* are boundary-significant
and must declare the new edge (see _Boundary declarations_).

Alternatives rejected:

- **`plugin_api/`** — that package is Protocol definitions only
  (`test_api_layer_is_protocols_only.py`). A concrete retry loop does not belong.
- **Inside `HttpxClient`** — would retry every outbound request including
  non-idempotent ones (file uploads, bot creation). Retry must be opt-in per call
  site.
- **Leaving the helpers in `llm.py` and importing from there** — `core.harness`
  is a domain module; making `core.service_bot` depend on it to get a transport
  utility inverts the layering.

## Change 1 — `src/agentclaw/community/utils/retry.py` (new)

Public surface:

| Name | Signature | Purpose |
| --- | --- | --- |
| `client_error_status` | `(exc: BaseException) -> int \| None` | 4xx carried on `exc.response`, else `None` |
| `is_transport_failure` | `(exc: BaseException) -> bool` | True when no 4xx response is attached |
| `describe_exception` | `(exc: BaseException) -> str` | `Type: msg \| caused by Type: msg \| request=url` |
| `retry_transport_call` | `(call, *, operation, attempts, backoff_seconds) -> T` | Run `call`, retry transport failures |
| `DEFAULT_ATTEMPTS` | `= 2` | 1 original + 1 retry |
| `DEFAULT_BACKOFF_SECONDS` | `= 0.1` | Base pause, jittered |

`int | None` on `client_error_status` is intentional per the AGENTS.md type
contract: `None` is a meaningful domain answer ("carries no client-error
response"), which is precisely how a wrapped connection failure presents.

`retry_transport_call` semantics:

1. Call `call()`; return its value unchanged on success. A completed 4xx/5xx
   response is a *success* here — it returns normally and is never retried.
2. On exception: if attempts are exhausted **or** `is_transport_failure(e)` is
   false, log at ERROR with `describe_exception(e)` and re-raise unchanged
   (bare `raise`, preserving traceback and `__cause__`).
3. Otherwise log at WARNING with the elapsed ms and the cause chain, sleep a
   jittered backoff, and retry.
4. `attempts < 1` raises `ValueError` — a programming error, not a runtime state.

Jitter (50% of the base delay) is not decoration: the observed failures arrive in
synchronized clusters, so un-jittered retries would re-converge on the same tick.

`time.sleep` is correct rather than `asyncio.sleep`: every adopter on this path is
synchronous and already runs inside `asyncio.to_thread`.

## Change 2 — `core/harness/services/llm.py`

Replace the bodies of `_client_error_status` and `_exc_detail` with aliases to
`client_error_status` / `describe_exception`. The reasoning currently written in
their docstrings moves to the new module (it is not LLM-specific).

The private names stay: `test_llm_helpers.py` reaches them as module attributes
(`llm_mod._exc_detail(...)`), and `_request_with_retry` reads as LLM-local
policy. Behavior is identical, so those tests must pass unmodified — that is the
regression check for the extraction.

`llm.py`'s own retry loop is **not** changed. Its policy (5xx retry, shrinking
`max_tokens`, escalating delays) is LLM-specific and out of scope.

## Change 3 — `core/service_bot/services/baas_service.py`

In `get_http_info` (line ~3489), wrap **only** the transport call:

```python
response = retry_transport_call(
    lambda: self._http.get(
        f"/api/v1/bots/{device_id}/http-info", params=params, timeout=timeout
    ),
    operation="BaasService.get_http_info",
)
```

Everything after — `raise_for_status()`, `.json()`, the `code != 0` check, the
`HttpConnectionInfo` construction — stays exactly as-is and outside the retried
thunk. That is what keeps status-based behavior unchanged: a 4xx/5xx response
returns from `.get()` normally and is never retried.

`timeout` remains the per-attempt deadline with its current default of `5.0`.
Worst case for a fully failing call becomes two deadlines instead of one.

In the trailing `except Exception as e` handler, format with
`describe_exception(e)` and change `raise BaasServiceError(...)` to
`raise BaasServiceError(...) from e`. The `httpx.HTTPStatusError` and
`BaasServiceError` handlers are untouched.

The `GET` is idempotent — it resolves connection info and mutates nothing — which
is the precondition for adopting the component.

## Change 4 — `plugins/http_client.py`

Construct one `httpx.Client(base_url=...)` eagerly in `__init__` and reuse it:

```python
def __init__(self, base_url: str):
    self._base_url = base_url
    self._client = httpx.Client(base_url=base_url)
```

`_request` drops the `with httpx.Client(...)` block and calls
`self._client.request(method, path, timeout=timeout, **kwargs)`.

- **Eager, not lazy.** `httpx.Client()` opens no socket, so construction is cheap
  and does no I/O. Eager construction avoids a lock entirely; `httpx.Client` is
  itself thread-safe for requests, which is what the thread-pool callers need.
- **Timeout moves from construction to the call.** Every `HttpClient` method
  already has `timeout: float = 30.0`, so a value is always passed — per-call
  timeout semantics are preserved exactly.
- **No `close()`.** These are process-lifetime DI singletons with no lifecycle
  hook to call it from; an uncalled method would be dead surface.
- **No `limits=` tuning.** httpx defaults (100 max connections, 20 keep-alive)
  are sane; picking numbers without evidence is speculative configurability.

## Boundary declarations

Both importers are boundary-significant, but only one needs a new line.
`test_module_boundaries.py` matches a declaration against an actual import with
`actual == d or actual.startswith(d + ".")`, so a bare entry covers every
submodule beneath it:

- `core/harness/README.md` declares only `agentclaw.community.utils.env_utils`,
  which does **not** cover `...utils.retry` → **add**
  `agentclaw.community.utils.retry`.
- `core/service_bot/README.md` already declares the bare
  `agentclaw.community.utils` (alongside `...utils.env_utils`), and that entry
  already matches `...utils.retry` → **no change needed**.

Verified by `tests/community/architecture/test_module_boundaries.py` (currently
3 passed — must stay green).

## Test plan

### New — `tests/community/utils/test_retry.py`

- returns the call's value; calls exactly once on success
- retries once on an exception with no `response`, then succeeds → 2 calls
- does **not** retry when the exception carries a 4xx `response` → 1 call, raises
- exhausts attempts and re-raises the **original** exception unchanged
- a completed 4xx/5xx *response object* is returned, not retried (the thunk
  returning normally is success)
- `attempts=1` performs no retry; `attempts=0` raises `ValueError`
- `client_error_status`: 4xx → int; 5xx / 2xx / missing / non-int → `None`
- `describe_exception`: bare exception; with `__cause__`; with `request.url`
- backoff is slept (patch `time.sleep`) and is non-negative

### New — `tests/.../test_baas_service_get_http_info_retry.py`

- one transport failure then success → `HttpConnectionInfo` returned, 2 calls
- persistent transport failure → `BaasServiceError` with the cause chain in the
  message and `__cause__` set
- a 4xx/5xx *response* is not retried → 1 call, existing error behavior unchanged
- happy path unchanged → 1 call

### Modified — `tests/community/plugins/test_http_client.py`

The existing five tests assert `httpx.Client(base_url=..., timeout=<per-call>)`
at construction. Pooling moves the timeout to the request, so each is updated to
assert one construction with `base_url` only and `request(..., timeout=...)` per
call. Two behaviors are additionally pinned:

- two calls on one `HttpxClient` construct the client **once** (the reuse claim)
- `None` args are still omitted from the request

### Regression — run unmodified

- `tests/community/core/harness/services/test_llm_helpers.py` (extraction is
  behavior-preserving)
- `tests/community/core/harness/services/test_llm_request_retry.py`
- `tests/community/architecture/test_module_boundaries.py`
- the `baas_service` unit suite
- `tests/community/di/` HTTP client module tests

## Risks

| Risk | Mitigation |
| --- | --- |
| A retried request already reached BaaS | `/http-info` is idempotent; it resolves and returns, mutating nothing |
| Worst-case latency doubles on total failure | Bounded at 2 deadlines; only on the path that previously failed outright |
| Long-lived client holds a stale connection | httpx's pool detects closed connections and reconnects; this is standard client behavior |
| Extraction changes LLM retry behavior | `test_llm_helpers` / `test_llm_request_retry` run unmodified as the check |
| Pooled client shared across threads | `httpx.Client` is thread-safe for requests; no shared mutable state added |

## Out of scope

Per `spec.md`: the BaaS-side blocking-I/O root cause, per-upload `/http-info`
caching, and the rollback-delete behavior in `SkillService.upload_skill`.

## Delivery

Branch `claude/skill-upload-http-logs-ee0m9h`, already checked out off
`origin/REL20260806`. PR targets `REL20260806`, opened as a draft, titled
`fix(backend): retry transport failures and reuse HTTP connections`, with the
`Problem` / `Solution` / `Validation` sections from
`.github/pull_request_template.md`.
