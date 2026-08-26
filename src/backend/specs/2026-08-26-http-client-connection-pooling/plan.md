# Plan — Outbound HTTP Connection Pooling and HTTP/2 for `HttpClient`

Implements `spec.md`. Six source files (one of them the dependency manifest
pair), six test files (one deleted, one new). No Protocol change:
`plugin_api/http_client.py` keeps its signatures — only a stale impl note in its
docstring is corrected — so no consumer and no conformance contract moves.

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
| `http2=True` requires `h2` importable at construction | Yes — `Client.__init__` does `if http2: import h2`. Verified constructing a real `HTTPTransport` client with `http2=True`. |
| httpx negotiates h2 by ALPN only | `httpcore/_sync/connection.py`: `http2_negotiated or (self._http2 and not self._http1)` — no cleartext upgrade path |

## Component 0 — dependency: `httpx[http2]`

`pyproject.toml`: `"httpx>=0.27.0"` → `"httpx[http2]>=0.27.0"`, keeping its
position in the alphabetised shared-base list and its existing comment context
(the block is documented as mirroring the corp manifest, so the corp side needs
the same edit when this lands there).

`uv.lock` is hand-edited rather than regenerated. Regenerating is not an option
here: the lock pins `https://mirrors.aliyun.com/pypi/simple` as its registry and
that mirror is unreachable from the dev sandbox, so `uv lock` would rewrite
every URL in the file to a different index — a diff of thousands of lines
unrelated to this change. The aliyun mirror mirrors PyPI's path layout exactly
(`/pypi/packages/<a>/<b>/<hash>/<file>` ↔
`files.pythonhosted.org/packages/<a>/<b>/<hash>/<file>`), so the entries are
constructed by host-swapping the URLs PyPI's JSON API reports. Resolved versions
and hashes, already fetched and verified:

- `h2` 4.4.1 — depends on `hpack>=4.2,<5` and `hyperframe>=6.1,<7`
- `hpack` 4.2.0 — no dependencies
- `hyperframe` 6.1.0 — no dependencies

Four edits to `uv.lock`:

1. Root package `dependencies`: `{ name = "httpx" }` → `{ name = "httpx", extra = ["http2"] }`.
2. Root `[package.metadata] requires-dist`: `{ name = "httpx", specifier = ">=0.27.0" }`
   → `{ name = "httpx", extras = ["http2"], specifier = ">=0.27.0" }`.
3. The existing `httpx` package entry gains a
   `[package.optional-dependencies]` section with `http2 = [{ name = "h2" }]`,
   following the shape the `uvicorn` entry already uses for its `standard` extra.
4. Three new `[[package]]` blocks for `h2`, `hpack`, `hyperframe`, inserted in
   the file's alphabetical ordering, each with
   `source = { registry = "https://mirrors.aliyun.com/pypi/simple" }`, its
   `sdist`, its wheel, and (for `h2`) its `dependencies` list.

Verification that the edit is well-formed is `uv lock --check` (or `uv sync
--frozen` where the mirror is reachable) — the task list runs whichever is
available and records which.

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
        max_connections: int = DEFAULT_MAX_CONNECTIONS,
        max_keepalive_connections: int = DEFAULT_MAX_KEEPALIVE_CONNECTIONS,
        keepalive_expiry: float = DEFAULT_KEEPALIVE_EXPIRY,
        http2: bool = DEFAULT_HTTP2,
    ):
        self._base_url = base_url
        self._limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive_connections,
            keepalive_expiry=keepalive_expiry,
        )
        self._http2 = http2
        self._lock = threading.Lock()
        self._client: httpx.Client | None = None
```

The module-level `DEFAULT_*` constants mirror `HttpClientPoolConfig`'s field
defaults, `DEFAULT_HTTP2 = False` included. They exist for direct constructions
only (the singlebox endpoint fixture, the streaming test); the composition root
always passes explicit values, so the two default sets can never silently
diverge in production.

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
            self._client = httpx.Client(
                base_url=self._base_url,
                limits=self._limits,
                http2=self._http2,
            )
        return self._client
```

Lazy rather than eager because `discover_lifecycle_participants` resolves every
binding at boot; eager construction would open pools for upstreams a given
deployment never calls.

**The `transport` parameter is deleted** (review decision, 2026-08-26). It exists
on the current class but is never wired by DI — all four providers construct
`HttpxClient(base_url=…)` bare — and was passed only by
`test_http_client_stream.py`, which injected an `httpx.MockTransport`. A
production constructor argument that only tests supply is design damage, so it
goes, and the test file that depended on it goes with it (see Test plan). This
also removes the `client_kwargs` dict and its `if self._transport is not None`
guard: with `timeout` moving to the call site and `transport` gone, there is
nothing left to assemble conditionally.

Consequence, accepted deliberately: **`HttpxClient.stream()` ends up with no
automated coverage.** It is the one method this change rewrites whose behavior no
test will exercise. Two things bound the risk — `stream()`'s body after the
rewrite is three lines with no branching, and `test_session_resources.py` still
drives the pooled non-stream path over a real socket — but it is a real gap and
`spec.md` records it as such. If it is ever worth closing, `respx` is already a
dev dependency and intercepts httpx at the transport layer with no production
seam, so coverage can return without the parameter coming back.

**Pool topology — one client per binding, not per base_url.** Worth stating
precisely, because the two are only the same for three of the four qualifiers:

| Binding | `base_url` | Origins its pool spans |
| --- | --- | --- |
| `baas` | secbaas host | one |
| `bcn` | BCN host | one |
| `masa_agent_eval` | eval host | one |
| `general` | `""` — callers pass absolute URLs | **many**: agentclawproxy, LLM endpoints, container IPs |

Within a single `httpx.Client`, httpcore binds each connection to exactly one
origin (`can_handle_request` is `origin == self._origin`), so *reuse* is
per-origin and the `general` client will hold separate connections per host it
talks to. But the ceiling is pool-wide, not per-origin —
`connection_pool.py:324` gates new connections on
`len(self._connections) < self._max_connections`, where `self._connections` is
the entire pool.

**Consequence:** the `general` client's 100-connection budget is *shared* across
agentclawproxy, every LLM endpoint, and every container IP. That is the single
place where one-policy-for-all-four is most likely to need revisiting, and it is
the same client the spec already flags for mixing SSE streams with ordinary
calls. It is still the right starting point — 100 is generous and the numbers are
configurable — but if any qualifier ends up wanting its own ceiling, this is the
one, and the per-qualifier follow-up named in `spec.md` is where it goes.

**Request path.** `_request` keeps its `None`-omitting `kwargs` assembly
verbatim — that is what acceptance criterion 4 pins — and changes only its last
two lines. The `with httpx.Client(...) as client:` block goes away; `timeout`
moves from the constructor to the call:

```python
return self._pooled_client().request(method, path, timeout=timeout, **kwargs)
```

A bare float expands to the same connect/read/write/pool budget httpx applied
when it was a constructor argument, so criterion 5 holds without a translation
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
across it). It is idempotent and **terminal**: it latches `_closed`, and a later
call raises rather than building a fresh pool.

Terminal rather than forgiving, which reverses this plan's original reasoning.
Forgiving looked kinder — a straggler would not see a `RuntimeError` during
shutdown — but code review traced where it leads: teardown closes the pool, an
in-flight `asyncio.to_thread` worker gets httpx's own `RuntimeError`, its
caller's retry layer classifies that connection-level and retries, and the retry
builds a **new** pool after shutdown that nothing will ever close. Reopening
connections to an upstream that teardown just released is worse than a loud
failure on a request that should not be in flight.

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
occupying pool slots), and the HTTP/2 semantics — ALPN-only negotiation, inert
against the plaintext singlebox upstreams, off by default — so the next reader
does not re-derive the multiplexing question from scratch.

## Component 2 — `di/config.py`: `HttpClientPoolConfig`

A new frozen dataclass in the existing style, placed under a new
`# ── Outbound HTTP transport ──` section between the CORS block and
`# ── Object storage ──`:

```python
@dataclass(frozen=True)
class HttpClientPoolPolicy:
    """The effective transport policy for ONE binding."""

    max_connections: int = 100
    max_keepalive_connections: int = 20
    keepalive_expiry: float = 5.0
    http2: bool = False


@dataclass(frozen=True)
class HttpClientPoolConfig:
    """Shared defaults plus sparse per-qualifier overrides."""

    defaults: HttpClientPoolPolicy = field(default_factory=HttpClientPoolPolicy)
    overrides: Mapping[str, HttpClientPoolPolicy] = field(default_factory=dict)

    def for_qualifier(self, qualifier: str) -> HttpClientPoolPolicy:
        """Effective policy for one binding: its override, else the defaults."""
        return self.overrides.get(qualifier, self.defaults)
```

Two types rather than one because the values a binding *uses* and the structure
that *selects* them are different things: `HttpxClient` takes four scalars, and
nothing below the composition root should know overrides exist.

`for_qualifier` resolves whole-policy, not field-by-field: an override that sets
only `http2` inherits nothing else from `defaults`. Field-level merging is the
alternative and is rejected — a half-specified override would silently drift as
the shared defaults change, which is exactly the failure mode a reader of the
YAML would not predict. The provider (Component 3) is what materialises a
complete `HttpClientPoolPolicy` per override by starting from `defaults` and
applying only the keys present, so the YAML *is* sparse while the resolved object
is total.

Pool defaults are httpx's own. The docstring carries the operational meaning the
numbers do not: that the ceilings are **per upstream client**, not process-wide;
that exceeding `max_connections` yields `HttpClientTimeoutError`
(`httpx.PoolTimeout`) once the per-call timeout elapses; that `keepalive_expiry`
must stay below the upstream's idle timeout to avoid the stale-connection
`RemoteProtocolError`; and that `http2` engages only against TLS upstreams that
offer `h2` via ALPN, defaulting off pending per-environment validation.

## Component 3 — `di/modules/config_module.py`: the provider

One `@singleton @provider` reading the `http_client` block, following the
file's established `_block(...)` + dataclass-defaults idiom exactly:

```python
def _pool_policy(block: dict[str, Any], base: cfg.HttpClientPoolPolicy):
    """One policy from a YAML mapping, falling back to ``base`` per field."""
    return cfg.HttpClientPoolPolicy(
        max_connections=int(block.get("max_connections", base.max_connections)),
        max_keepalive_connections=int(
            block.get("max_keepalive_connections", base.max_keepalive_connections)
        ),
        keepalive_expiry=float(block.get("keepalive_expiry", base.keepalive_expiry)),
        http2=bool(block.get("http2", base.http2)),
    )


@singleton
@provider
def http_client_pool(self) -> cfg.HttpClientPoolConfig:
    block = _block("http_client")
    defaults = _pool_policy(block, cfg.HttpClientPoolPolicy())
    raw = block.get("overrides") or {}
    overrides = {
        str(name): _pool_policy(dict(body), defaults)
        for name, body in raw.items()
        if isinstance(body, dict)
    }
    return cfg.HttpClientPoolConfig(defaults=defaults, overrides=overrides)
```

`_pool_policy` runs twice per override — once to build `defaults` from the
dataclass field defaults, then again per override starting from `defaults`. That
is what makes a sparse override total: `overrides.baas: {http2: true}` resolves to
the shared ceilings with `http2` flipped, so `for_qualifier` can return a whole
policy without any merging at the call site.

Unknown qualifier keys **are** rejected — the provider raises, naming the typo
and the valid set. This reverses an earlier draft of this plan, which argued the
provider "has no qualifier list to validate against". That was simply false: the
four `QUALIFIER_*` constants are importable, and deriving `_HTTP_CLIENT_QUALIFIERS`
from them keeps the config surface from drifting off the injector keys.

The distinction that matters is key vs. value. A malformed *value* still names a
real binding, so falling back to a working default is a sane reading of intent —
that path stays inert. An unrecognised *key* cannot be honoured by any binding,
so accepting it leaves the operator believing a ceiling was raised while the
binding ran on the shared defaults, which is the failure this block exists to
prevent. `ci.enforce.md` §E requires startup to fail early on invalid config, and
`baas.deploy_runtime` in the same module already rejects an unknown value this
way.

For that raise to actually mean *startup*, `HttpClientPoolConfig` joins
`container.py`'s eager-check allowlist. Without it the provider would raise only
when something first resolved an `HttpClient` — in a dev boot, after
`discover_lifecycle_participants` had already swallowed it. The per-provider
`logger.info` (Component 4) remains the way to confirm what a *valid* override
resolved to.

Placed next to `masa_agent_eval`. Missing block ⇒ dataclass defaults, so no
deployment needs a config change to adopt pooling, and enabling HTTP/2 later is
purely additive to one YAML block.

## Component 4 — `di/modules/http_client_module.py`: wiring

Each of the four providers gains a `pool: cfg.HttpClientPoolConfig` parameter
and forwards it. `general_http_client` picks up `@inject` (it currently has no
dependencies). The forwarding is identical in all four:

```python
policy = pool.for_qualifier(QUALIFIER_BAAS)      # …_BCN / _GENERAL / _MASA_AGENT_EVAL
return HttpxClient(
    base_url=base_url,
    max_connections=policy.max_connections,
    max_keepalive_connections=policy.max_keepalive_connections,
    keepalive_expiry=policy.keepalive_expiry,
    http2=policy.http2,
)
```

Each provider names its own qualifier constant — the same one its return type is
annotated with — so the override key and the injector key cannot drift apart.

The existing `logger.info` line per provider is extended to record the pool
ceiling and the HTTP/2 flag alongside the base_url, so a deployment's effective
transport settings are visible in boot logs rather than having to be inferred
from config. This is what will confirm, in a pre environment, that flipping
`http2` actually took effect.

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
not active, so the dataclass defaults remain the single source of the values —
the block exists to show the shape, not to set it.

```yaml
user_config:
  # Outbound HTTP transport policy for the HttpClient plugins.
  # Omit the whole block to take the defaults shown below.
  # http_client:
  #   # Shared defaults — apply to every binding that has no override.
  #   max_connections: 100            # hard ceiling on open connections, PER BINDING.
  #                                   # Past it a request waits, then fails with
  #                                   # HttpClientTimeoutError (httpx.PoolTimeout).
  #   max_keepalive_connections: 20   # how many of those stay open once idle
  #   keepalive_expiry: 5.0           # seconds an idle connection is kept. Keep BELOW
  #                                   # the upstream's own idle timeout, or a connection
  #                                   # it already closed surfaces as RemoteProtocolError.
  #   http2: false                    # negotiated via TLS ALPN only: engages on https://
  #                                   # upstreams offering h2, silently stays on HTTP/1.1
  #                                   # for http:// ones (no cleartext h2c upgrade).
  #
  #   # Optional per-binding overrides. Valid keys are exactly the four HttpClient
  #   # qualifiers: baas, bcn, general, masa_agent_eval. An unrecognised key is
  #   # silently inert — check the boot log line for each binding to confirm what
  #   # actually resolved.
  #   overrides:
  #     baas:
  #       http2: true                 # single origin, plain request/response —
  #                                   # the safest binding to flip h2 on first
  #     general:
  #       max_connections: 200        # base_url="" — this one pool is shared across
  #                                   # agentclawproxy, LLM endpoints and container IPs,
  #                                   # and its SSE streams each hold a slot for the
  #                                   # whole response body
```

**How that resolves.** An override replaces the whole policy for its binding, but
the *provider* builds each one starting from the resolved defaults, so the YAML
stays sparse while the object is total. With the block above:

| Binding | max_connections | max_keepalive | keepalive_expiry | http2 |
| --- | --- | --- | --- | --- |
| `baas` | 100 *(default)* | 20 *(default)* | 5.0 *(default)* | **true** |
| `bcn` | 100 | 20 | 5.0 | false |
| `general` | **200** | 20 | 5.0 | false |
| `masa_agent_eval` | 100 | 20 | 5.0 | false |

Note what `baas` inherits: setting only `http2` leaves its three ceilings at the
shared defaults — and if those defaults later change, `baas` follows them. That
is the intended behavior and the reason the merge happens in the provider rather
than at the call site.

## Test plan

**`tests/community/core/harness/services/test_http_client_stream.py` (deleted).**
Its two `HttpxClient` cases exist only because the class accepts an injectable
`transport`; with that parameter gone they cannot be written as they stand. Its
third case, `test_local_http_client_stream_raises`, never used `transport` — it
pins `LocalHttpClient.stream` raising when unstubbed — so it moves into
`tests/community/contracts/test_http_client.py`, where the rest of the local-impl
contract already lives, rather than being lost with the file.

**`tests/community/plugins/test_http_client.py` (rewritten).** The existing
tests patch `httpx.Client` and assert `ctor.assert_called_once_with(base_url=…,
timeout=…)` plus a `request(...)` call with no `timeout` — both encode the
per-call-client design and must move. With no injectable transport, every case
below drives a patched `httpx.Client` and asserts on construction arguments and
delegation. That is a weaker instrument than a real client — it verifies what
`HttpxClient` *asks httpx to do*, not what httpx then does — which is acceptable
here because httpx's own behavior (per-request `timeout`, absolute-URL handling,
pool reuse) was verified directly against the pinned version and is recorded in
the assumptions table above:

- `test_pool_is_reused_across_calls` — two calls, one underlying client
  instance; `httpx.Client` constructed exactly once (criterion 1).
- `test_client_is_built_with_configured_limits` — `limits` carries the three
  configured values (criterion 2).
- `test_http2_defaults_off_and_is_forwarded_when_enabled` — `http2=False` is
  passed to `httpx.Client` by default and `http2=True` when configured. Paired
  with an unpatched case that constructs `HttpxClient(..., http2=True)` and
  triggers the pool, proving `h2` is importable in the installed environment
  (criterion 3) — no request is issued, so no network is touched.
- `test_none_args_are_omitted_from_the_request` — preserved (criterion 4).
- `test_post_with_files_and_data_passes_multipart_kwargs` — preserved
  (criterion 4).
- `test_get_and_put_dispatch_correct_methods` — preserved.
- `test_timeout_is_passed_per_request_not_per_client` — `request` receives
  `timeout=T`; the client is not constructed with one (criterion 5).
- `test_absolute_url_bypasses_base_url` — asserts the absolute URL reaches
  `Client.request` unaltered, which is the part `HttpxClient` owns; that httpx
  then ignores `base_url` for it is verified in the assumptions table
  (criterion 4).
- `test_response_and_transport_errors_propagate` — preserved (criterion 6).
- `test_stream_shares_the_pool_and_leaves_it_open` — `stream` is issued on the
  pooled client and `close()` is never called on it; a following `get` reuses the
  same instance (criterion 7). This is the only remaining check on `stream`, and
  it covers plumbing rather than streaming behavior.
- `test_close_is_idempotent_and_terminal` — `close()` twice does not raise, and
  a later call raises rather than building a second pool (criterion 8).
- `test_teardown_latches_against_a_retrying_caller` — a retry loop hammering a
  closed client never resurrects the pool (criterion 8).
- `test_teardown_closes_the_pool` — `await client.teardown()` closes the
  underlying client (criterion 8).
- `test_concurrent_first_calls_build_exactly_one_client` — N threads racing
  through `_pooled_client()` produce one instance (criterion 9).

**`tests/community/di/modules/test_http_client_module_bcn.py`** — pass
`cfg.HttpClientPoolConfig()`; add assertions that a non-default `defaults` policy
(limits *and* `http2=True`) reaches the constructed client, and that an override
keyed to a *different* qualifier does not.

**`tests/community/di/modules/test_config_module_http_client.py` (new).** The
override resolution is the part with real logic, so it gets its own tests:
missing block ⇒ dataclass defaults for every qualifier; a top-level block sets
the shared defaults; a sparse override inherits the unset fields from those
defaults rather than from the dataclass; `for_qualifier` on an unlisted qualifier
returns the defaults object; a non-dict override body is ignored rather than
raising.

**`tests/community/di/modules/test_infrastructure_module.py`** — pass
`cfg.HttpClientPoolConfig()` to `general_http_client()`.

**Not added:** pooling assertions in `tests/community/contracts/test_http_client.py`.
That file is the Rule 25 conformance test for the *protocol* and its local impl;
pooling is a prod-impl detail behind an unchanged protocol, so it belongs in the
unit file.

## Risk-driven checks before the suite

- `uv lock --check` (or `uv sync --frozen`) — the hand-edited lock is
  internally consistent. This is the check most likely to catch a mistake in
  Component 0, and it must run before anything else is trusted.
- `test_profile_and_modules_for.py` resolves the real `HttpxClient` bindings
  across profiles — confirms the new required provider argument is satisfied by
  the container, not just by direct calls.
- The session-resource endpoint cases bind `HttpxClient(_session_file_api_base())`
  against a live `ThreadingHTTPServer`, exercising the pooled path end-to-end
  over a real socket. Run them with `pytest tests/community/endpoints/` — the
  file registers cases via `@endpoint_test` and defines no `test_*` functions,
  so naming it directly collects nothing and passes vacuously.
- `test_lifecycle_discovery.py` — confirms nothing about discovery regressed.
- `test_local_no_external_deps.py` — confirms the new `h2` dependency does not
  breach whatever the local-plugin import rules allow.
