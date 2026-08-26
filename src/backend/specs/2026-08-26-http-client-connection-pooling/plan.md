# Plan — Outbound HTTP Connection Pooling for `HttpClient`

Implements `spec.md`. Five source files, four test files. No protocol change:
`plugin_api/http_client.py` is untouched, so no consumer and no conformance
contract moves.

## Verified assumptions

Checked against the pinned libraries (httpx 0.28.1 / httpcore 1.0.9) before
writing this plan, because each one would otherwise be a rewrite risk:

| Assumption | Result |
| --- | --- |
| `class HttpxClient(LifecycleBase, HttpClient)` linearizes | MRO `HttpxClient → LifecycleBase → HttpClient → Plugin → Protocol → Generic → object`; `isinstance` holds for both `Lifecycle` and `HttpClient` |
| `Client.request(..., timeout=float)` works per-request | Yes |
| `Client.stream(..., timeout=float)` works per-request | Yes |
| An absolute URL still bypasses `base_url` (the `general` contract) | Yes — `base_url="http://svc.test"` + `"http://other.test:20010/x"` requests the absolute URL |
| `httpx.PoolTimeout` classifies as an existing boundary error | `issubclass(httpx.PoolTimeout, httpx.TimeoutException)` is `True`, so `HttpClientTimeoutError` already covers it |
| A custom `transport=` makes `limits=` inert rather than an error | Yes — `Client._init_transport` returns the given transport before building `HTTPTransport`, so the `MockTransport` tests are unaffected |

## Component 1 — `plugins/http_client.py`: the pooled client

The whole behavior change lives here.

**Construction.** `__init__` stops being a two-field assignment and instead
builds the `httpx.Limits` it will use, plus the lazy-init state:

```python
class HttpxClient(LifecycleBase, HttpClient):
    def __init__(
        self,
        base_url: str,
        *,
        transport: Any | None = None,
        max_connections: int = DEFAULT_MAX_CONNECTIONS,
        max_keepalive_connections: int = DEFAULT_MAX_KEEPALIVE_CONNECTIONS,
        keepalive_expiry: float = DEFAULT_KEEPALIVE_EXPIRY,
    ):
        self._base_url = base_url
        self._transport = transport
        self._limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive_connections,
            keepalive_expiry=keepalive_expiry,
        )
        self._lock = threading.Lock()
        self._client: httpx.Client | None = None
```

The module-level `DEFAULT_*` constants mirror `HttpClientPoolConfig`'s field
defaults. They exist for direct constructions only (the singlebox endpoint
fixture, the streaming test); the composition root always passes explicit
values, so the two default sets can never silently diverge in production.

**Lazy, thread-safe pool.** `_pooled_client()` double-checks under a
`threading.Lock`. The lock guards *construction only* and is never held across a
request — `httpx.Client` handles concurrent requests itself, which is what makes
this safe from `asyncio.to_thread` worker threads:

```python
def _pooled_client(self) -> httpx.Client:
    client = self._client
    if client is not None:
        return client
    with self._lock:
        if self._client is None:
            kwargs: dict[str, Any] = {
                "base_url": self._base_url,
                "limits": self._limits,
            }
            if self._transport is not None:
                kwargs["transport"] = self._transport
            self._client = httpx.Client(**kwargs)
        return self._client
```

Lazy rather than eager because `discover_lifecycle_participants` resolves every
binding at boot; eager construction would open pools for upstreams a given
deployment never calls.

**Request path.** `_request` keeps its `None`-omitting `kwargs` assembly
verbatim — that is what acceptance criterion 3 pins — and changes only its last
two lines. The `with httpx.Client(...) as client:` block goes away; `timeout`
moves from the constructor to the call:

```python
return self._pooled_client().request(method, path, timeout=timeout, **kwargs)
```

A bare float expands to the same connect/read/write/pool budget httpx applied
when it was a constructor argument, so criterion 4 holds without a translation
step.

**Streaming.** Same substitution, and critically *no* client close:

```python
with self._pooled_client().stream(method, path, timeout=timeout, **kwargs) as resp:
    yield resp
```

The inner `with` still returns the connection to the pool at block exit; only
the outer client-closing `with` is removed.

**Teardown.** `close()` swaps `self._client` to `None` under the lock and closes
the old client outside it (`client.close()` can block; the lock must not be held
across it). It is idempotent, and a call arriving after `close()` lazily builds
a fresh pool rather than raising — deliberately forgiving, because `teardown()`
runs in shutdown phase 2 and a straggler call should not turn into a
`RuntimeError` during shutdown.

`async def teardown(self)` calls `close()`. Discovery is automatic: the four
qualified bindings are `@singleton`, `discover_lifecycle_participants` walks
`injector.binder._bindings` and keeps any instance satisfying `Lifecycle`, so
nothing has to be registered by hand. `test_lifecycle_discovery` asserts a
*minimum* participant set and explicitly allows extras, so it does not need
touching — and in the `TEST` profile these keys bind `LocalHttpClient` anyway,
which is not a `Lifecycle`.

**Docstring.** The module docstring currently states the short-lived-client
design as the contract ("every call opens a short-lived `httpx.Client`"). It is
rewritten to state the pooled design, the two operational consequences from the
spec's risk section (`keepalive_expiry` vs upstream idle timeout; streams
occupying pool slots), and why HTTP/2 is absent — so the next reader does not
re-litigate the multiplexing question from scratch.

## Component 2 — `di/config.py`: `HttpClientPoolConfig`

A new frozen dataclass in the existing style, placed under a new
`# ── Outbound HTTP transport ──` section between the CORS block and
`# ── Object storage ──`:

```python
@dataclass(frozen=True)
class HttpClientPoolConfig:
    max_connections: int = 100
    max_keepalive_connections: int = 20
    keepalive_expiry: float = 5.0
```

Defaults are httpx's own. The docstring carries the operational meaning the
numbers do not: that the ceilings are **per upstream client**, not process-wide;
that exceeding `max_connections` yields `HttpClientTimeoutError`
(`httpx.PoolTimeout`) once the per-call timeout elapses; and that
`keepalive_expiry` must stay below the upstream's idle timeout to avoid the
stale-connection `RemoteProtocolError`.

No `http2` field — per spec, the knob is not added.

## Component 3 — `di/modules/config_module.py`: the provider

One `@singleton @provider` reading the `http_client` block, following the
file's established `_block(...)` + dataclass-defaults idiom exactly:

```python
@singleton
@provider
def http_client_pool(self) -> cfg.HttpClientPoolConfig:
    block = _block("http_client")
    defaults = cfg.HttpClientPoolConfig()
    return cfg.HttpClientPoolConfig(
        max_connections=int(block.get("max_connections", defaults.max_connections)),
        max_keepalive_connections=int(
            block.get("max_keepalive_connections", defaults.max_keepalive_connections)
        ),
        keepalive_expiry=float(block.get("keepalive_expiry", defaults.keepalive_expiry)),
    )
```

Placed next to `masa_agent_eval`. Missing block ⇒ dataclass defaults, so no
deployment needs a config change to adopt this.

## Component 4 — `di/modules/http_client_module.py`: wiring

Each of the four providers gains a `pool: cfg.HttpClientPoolConfig` parameter
and forwards it. `general_http_client` picks up `@inject` (it currently has no
dependencies). The forwarding is identical in all four:

```python
return HttpxClient(
    base_url=base_url,
    max_connections=pool.max_connections,
    max_keepalive_connections=pool.max_keepalive_connections,
    keepalive_expiry=pool.keepalive_expiry,
)
```

The existing `logger.info` line per provider is extended to record the pool
ceiling alongside the base_url, so a deployment's effective limits are visible
in boot logs rather than having to be inferred from config.

**Breaking-call-site note:** `test_http_client_module_bcn.py` and
`test_infrastructure_module.py` invoke these providers *directly*
(`HttpClientModule().bcn_http_client(cfg.BcnConfig(...))`,
`module.general_http_client()`). Adding a required parameter breaks both. They
are updated to pass an explicit `cfg.HttpClientPoolConfig()` rather than the
parameter being given a default — a defaulted injector provider argument would
hide a missing binding at boot, which is worse than two test edits.

## Component 5 — `configs/application-community.yaml`: documented knob

A commented `http_client` block under `user_config`, matching how the file
documents other optional blocks (e.g. the commented LLM `base_url`). Commented,
not active, so the dataclass defaults remain the single source of the values.

## Test plan

**`tests/community/plugins/test_http_client.py` (rewritten).** The existing
tests patch `httpx.Client` and assert `ctor.assert_called_once_with(base_url=…,
timeout=…)` plus a `request(...)` call with no `timeout` — both encode the
per-call-client design and must move. The file is restructured around a
`MockTransport`-driven real client (which exercises the actual pooling code
rather than a mock of it) plus a narrow `httpx.Client`-patching helper where
construction arguments are the thing under test:

- `test_pool_is_reused_across_calls` — two calls, one underlying client
  instance; `httpx.Client` constructed exactly once (criterion 1).
- `test_client_is_built_with_configured_limits` — `limits` carries the three
  configured values (criterion 2).
- `test_none_args_are_omitted_from_the_request` — preserved (criterion 3).
- `test_post_with_files_and_data_passes_multipart_kwargs` — preserved
  (criterion 3).
- `test_get_and_put_dispatch_correct_methods` — preserved.
- `test_timeout_is_passed_per_request_not_per_client` — `request` receives
  `timeout=T`; the client is not constructed with one (criterion 4).
- `test_absolute_url_bypasses_base_url` — the `general` client's contract, newly
  pinned because pooling is the change most likely to disturb it (criterion 3).
- `test_response_and_transport_errors_propagate` — preserved (criterion 5).
- `test_stream_shares_the_pool_and_leaves_it_open` — after a `stream` block
  exits, a following `get` succeeds on the same client (criterion 6).
- `test_close_is_idempotent_and_rebuilds_on_next_use` — `close()` twice does not
  raise; a later call works (criterion 7).
- `test_teardown_closes_the_pool` — `await client.teardown()` closes the
  underlying client (criterion 7).
- `test_concurrent_first_calls_build_exactly_one_client` — N threads racing
  through `_pooled_client()` produce one instance (criterion 8).

**`tests/community/di/modules/test_http_client_module_bcn.py`** — pass
`cfg.HttpClientPoolConfig()`; add an assertion that a non-default config reaches
the constructed client's `_limits`.

**`tests/community/di/modules/test_infrastructure_module.py`** — pass
`cfg.HttpClientPoolConfig()` to `general_http_client()`.

**`tests/community/core/harness/services/test_http_client_stream.py`** — expected
to pass unchanged; it constructs `HttpxClient("http://llm.local",
transport=transport)` and drives a real `stream`. Treated as the regression
guard that the streaming rewrite did not change observable behavior. Run, not
edited.

**Not added:** pooling assertions in `tests/community/contracts/test_http_client.py`.
That file is the Rule 25 conformance test for the *protocol* and its local impl;
pooling is a prod-impl detail behind an unchanged protocol, so it belongs in the
unit file.

## Risk-driven checks before the suite

- `test_profile_and_modules_for.py` resolves the real `HttpxClient` bindings
  across profiles — confirms the new required provider argument is satisfied by
  the container, not just by direct calls.
- `test_session_resources.py` binds `HttpxClient(_session_file_api_base())`
  against a live local HTTP server; it exercises the pooled path end-to-end over
  a real socket, which no mock-transport test does.
- `test_lifecycle_discovery.py` — confirms nothing about discovery regressed.
