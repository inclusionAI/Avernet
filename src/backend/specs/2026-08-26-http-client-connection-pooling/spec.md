# Outbound HTTP Connection Pooling and HTTP/2 for `HttpClient`

## Summary

Every outbound HTTP call the backend makes today opens a brand-new TCP+TLS
connection and closes it when the call returns. `HttpxClient` — the single real
implementation behind the `HttpClient` plugin seam, bound once per upstream
(`baas`, `bcn`, `general`, `masa_agent_eval`) — constructs a throwaway
`httpx.Client` inside `_request` and `stream`, uses it for exactly one request,
and tears it down via the `with` block.

That is fine at low call volume and wrong at the parallelism the product is
moving toward. The callers of this seam already fan out: `asyncio.to_thread` +
`asyncio.gather` across bot publish, task dispatch, device health scans, and
container API calls. Each of those parallel calls pays a full TLS handshake and
leaves a socket in `TIME_WAIT` behind it, and **nothing anywhere bounds how many
the process may open at once**. A wide enough fan-out exhausts ephemeral ports on
the backend pod and pushes a handshake burst onto BaaS and the agentclaw proxy
that no ceiling in this codebase limits.

This change makes each `HttpxClient` own **one long-lived, connection-pooled
`httpx.Client`** for the life of the process, with explicit `httpx.Limits`, and
adds **config-gated HTTP/2** so that many in-flight requests can share one
connection where the upstream offers it. Behavior at the call site — arguments,
response type, timeout budget, exception classification — does not change.

## The upstreams are TLS

This matters enough to state as a finding, because an earlier revision of this
spec concluded the opposite and scoped HTTP/2 out on that basis.

Every base URL *in this repository* is plaintext (`http://localhost:8890` in the
community and singlebox overlays; `http://10.0.0.1:20010`-style container URLs in
test fixtures), because the corp production overlay is not in this repo.
Production logs show the real hosts:

```
GET  https://secbaas-prod.alipay.com/api/v1/bots/BOT-…/ws-info?…      "HTTP/1.1 404"
POST https://agentclawproxy-prod.alipay.com/proxypass/ARCA_…:20003/api/file/list  "HTTP/1.1 200"
```

Both target upstreams — the BaaS API and the agentclaw proxy that fronts
container calls — are **HTTPS**. (The `HTTP/1.1` in those log lines is the
version httpx negotiated with `http2` disabled, which is today's default; it is
not evidence about what the servers support.) The earlier inference that the BaaS
hop was plaintext-in-mesh came from `baas_client.py`'s note about MOSN providing
security with no `Authorization` header — that note describes the
`/internal/bot-health-checker/alive` endpoint, not the `/api/v1/…` hop these logs
show.

Because they are TLS, ALPN negotiation is possible, and HTTP/2 is worth having.

**ALPN support, confirmed.** Probed from inside the corp network:

```
$ openssl s_client -connect secbaas-prod.alipay.com:443 -alpn h2,http/1.1 </dev/null 2>/dev/null | grep ALPN
ALPN protocol: h2
```

So the BaaS origin negotiates HTTP/2 today and multiplexing will actually
engage there. (An equivalent probe from the development sandbox is worthless and
should not be repeated: its TLS is terminated by an egress gateway whose
certificate issuer is `O = Anthropic, CN = Egress Gateway SDS Issuing CA`, so it
reports the gateway's ALPN, not the origin's.)

**Still unprobed: `agentclawproxy-prod.alipay.com`** — which matters *more* than
secbaas, since it fronts the parallel container calls this whole change is for.
Same one-liner against that host settles it. Not a blocker either way: httpx
falls back to HTTP/1.1 silently where `h2` is not offered, so an unprobed host
costs correctness nothing, only benefit.

## What each half buys

**Pooling** (the unambiguous win, and larger than a plaintext upstream would
make it):

1. **No TLS handshake per call.** A reused connection skips both the TCP
   three-way handshake and the TLS handshake — 2-3 round trips saved per
   request, plus the asymmetric crypto. Against a TLS upstream this is the
   dominant per-call cost, and it is paid on *every* call today.
2. **No socket churn.** Today every call leaves a closed socket in `TIME_WAIT`.
   Under the burst parallelism this is meant to enable, that is the concrete
   ephemeral-port-exhaustion risk, and it goes away.
3. **A ceiling that exists.** `max_connections` caps simultaneous connections
   per upstream client. Past the cap a request waits for a free connection
   rather than opening another socket, and fails with `httpx.PoolTimeout` if the
   per-call timeout elapses first. This is the backpressure the change is for:
   the fan-out is bounded at the client instead of being absorbed by the
   upstream.

**HTTP/2 multiplexing**, where the upstream offers it: many concurrent requests
share one connection instead of needing one connection each. On HTTP/1.1 the
pool still serves N concurrent callers with N connections (keep-alive reuses a
connection for *sequential* requests, one in flight at a time); under HTTP/2 the
same fan-out can ride a handful of connections. This is what makes the
parallel container-call path cheap rather than merely bounded.

httpx negotiates HTTP/2 through **TLS ALPN only** — it performs no cleartext
`h2c` upgrade (`httpcore/_sync/connection.py` selects an `HTTP2Connection` on
`http2_negotiated or (self._http2 and not self._http1)`, the second disjunct
being prior-knowledge h2, which requires disabling HTTP/1.1 entirely and would
break any upstream that does not speak h2). So the flag is inert for the
plaintext singlebox/community upstreams and active only against TLS ones.
Pooling is the prerequisite either way: multiplexing is meaningless on a client
that is discarded after one request.

## Scope

**In scope**

- `HttpxClient` holds one pooled `httpx.Client`, built lazily on first use and
  shared by every subsequent call, including `stream`.
- Pool ceilings (`max_connections`, `max_keepalive_connections`,
  `keepalive_expiry`) are explicit and configurable through the existing typed
  config path, applied to all four qualified bindings.
- An `http2` flag on the same config path, **defaulting to `false`**, forwarded
  to `httpx.Client(http2=…)`. The `h2` package is added as a dependency via the
  `httpx[http2]` extra so the flag can be flipped by configuration alone, with
  no code change and no redeploy of a different dependency set.
- The pool is released at process shutdown through the existing `Lifecycle`
  contract.
- `timeout` moves from the (now shared) client constructor to the per-request
  call, preserving the existing per-call timeout budget.

**Why `http2` defaults to off.** With secbaas's ALPN support now confirmed, the
original first reason for holding back is gone — multiplexing *will* engage. Two
reasons remain, and both are about staging rather than doubt:

1. The corp send-hook wrapper on httpx has never been exercised under h2, and
   neither community nor singlebox CI can exercise it (see Risks). It is the one
   component in this change's blast radius that cannot be tested before
   production.
2. The `general` client carries LLM SSE streams alongside container calls, so
   flipping the protocol there changes two very different traffic shapes at
   once — while `baas` could be flipped alone.

Defaulting off buys a staged rollout for one config line in a pre environment,
and pooling — the larger, certain win — lands immediately either way. **This
default is the decision most worth confirming at review**; shipping it on is a
one-word change to this spec.

**Out of scope**

- Cleartext `h2c` / prior-knowledge HTTP/2 (would require disabling HTTP/1.1).
- `LocalHttpClient` and the `test` / `corp_test` profile bindings — they never
  touch the network and gain nothing from a pool.
- Retry, circuit-breaking, or any change to how failures are classified.
- The async `aiohttp` / `requests` call sites elsewhere in the backend.
- Per-qualifier pool or protocol tuning. One policy applies to all four
  bindings; if `general` later needs its own ceiling or its own `http2` answer,
  that is an additive follow-up.

## Acceptance criteria

1. **Connections are reused.** Two sequential calls on one `HttpxClient`
   instance issue their requests through the *same* underlying `httpx.Client`;
   no client is constructed per call.
2. **The pool is bounded and configured.** The client is built with an explicit
   `httpx.Limits` carrying the configured `max_connections`,
   `max_keepalive_connections`, and `keepalive_expiry`. Defaults are httpx's own
   (100 / 20 / 5.0s) and are overridable from the `http_client` config block.
3. **HTTP/2 is available and off by default.** `httpx.Client` receives
   `http2=False` unless `http_client.http2` is set true, in which case it
   receives `http2=True`. The `h2` package is installed, so flipping the config
   is sufficient — importing `httpx` with `http2=True` must not raise.
4. **The wire shape is unchanged.** For every verb, arguments left as `None` are
   still omitted from the underlying request, and the request is still issued
   against the relative path with `base_url` supplied by the client. A caller
   passing an absolute URL (the `general` client's contract) still bypasses
   `base_url`.
5. **The timeout budget is unchanged.** A call passing `timeout=T` still applies
   `T` to connect / read / write / pool for that request and no other.
6. **Nothing is swallowed.** `raise_for_status` errors and transport errors
   propagate unchanged. Pool exhaustion surfaces as `httpx.PoolTimeout`, which
   is an `httpx.TimeoutException` and therefore already classifies as
   `HttpClientTimeoutError` at the boundary — no new exception type escapes the
   seam.
7. **Streaming shares the pool and does not close it.** `stream()` yields a
   streaming response from the shared client; when the `with` block exits the
   connection returns to the pool and the client stays usable for later calls.
8. **The pool is released at shutdown.** `HttpxClient` participates in the
   `Lifecycle` teardown phase and closes its client; discovery picks it up
   without a hand-maintained registration list.
9. **Thread-safe.** Lazy construction is safe under concurrent first calls from
   `asyncio.to_thread` worker threads — exactly one client is ever built per
   instance.
10. The existing backend unit suite passes, including the `HttpClient` contract
    test, the DI module tests that assert `HttpxClient` bindings, and the real
    `HttpxClient` streaming test driven through `httpx.MockTransport`.

## Risks

**Stale keep-alive connections.** A connection the upstream has already closed
on its own idle timeout, but which the pool still believes is live, surfaces as
`httpx.RemoteProtocolError("Server disconnected without sending a response")` on
the next request that picks it up. This failure mode does not exist today
because no connection is ever reused. Mitigation: `keepalive_expiry` defaults to
5s — far below any typical upstream idle timeout — and is configurable. This
spec deliberately does **not** add a retry, because "the wrapper swallows
nothing" is a documented invariant of this seam and adding retry semantics here
would change failure behavior for every caller.

**Streams occupy pool slots.** `stream()` holds its connection for the whole
response body. The `general` client carries LLM SSE streams, which are
long-lived by nature; on HTTP/1.1 enough concurrent streams can saturate that
client's pool and make ordinary calls on the same client wait. (HTTP/2 largely
dissolves this — concurrent streams multiplex onto one connection — which is an
argument for turning the flag on once it is trusted.) The 100-connection default
leaves ample headroom, and the ceiling is configurable, but the interaction is
real and is documented at the seam.

**The out-of-repo corp send-hook wrapper.** `llm.py` documents a corp-side hook
that wraps httpx transport failures into `HttpxCallingException('Error in httpx
send hook')`. It hooks `send`, whose signature and call shape this change does
not touch — but it has only ever observed clients that live for one request and
HTTP/1.1 connections. Community and singlebox CI cannot exercise it. This is the
primary reason `http2` ships defaulted off: pooling and multiplexing can then be
validated against it one at a time rather than together.

**A new runtime dependency.** `httpx[http2]` pulls `h2`, `hpack`, and
`hyperframe` — three small, pure-Python packages maintained alongside httpx
itself. They are imported only when `http2=True`, so with the default they are
installed but never loaded.

**Process-wide socket ceiling rises in principle.** Four singleton clients ×
`max_connections` = up to 400 concurrent connections at the default, versus
"unbounded but short-lived" today. This is a ceiling where there was none, not a
new floor: steady-state socket count goes *down*, because connections are reused
instead of rebuilt — and further down again if HTTP/2 is enabled.
