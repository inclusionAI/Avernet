# Outbound HTTP Connection Pooling for `HttpClient`

## Summary

Every outbound HTTP call the backend makes today opens a brand-new TCP
connection and closes it when the call returns. `HttpxClient` — the single real
implementation behind the `HttpClient` plugin seam, bound once per upstream
(`baas`, `bcn`, `general`, `masa_agent_eval`) — constructs a throwaway
`httpx.Client` inside `_request` and `stream`, uses it for exactly one request,
and tears it down via the `with` block.

That is fine at low call volume and wrong at the parallelism the product is
moving toward. The callers of this seam already fan out: `asyncio.to_thread` +
`asyncio.gather` across bot publish, task dispatch, device health scans, and
container API calls. Each of those parallel calls costs a fresh TCP handshake
and leaves a socket in `TIME_WAIT` behind it, and **nothing anywhere bounds how
many the process may open at once**. A wide enough fan-out exhausts ephemeral
ports on the backend pod and pushes an accept-queue burst onto BaaS and the
agentclaw proxy that no ceiling in this codebase limits.

This change makes each `HttpxClient` own **one long-lived, connection-pooled
`httpx.Client`** for the life of the process, with explicit `httpx.Limits`:
connections are reused across calls instead of rebuilt, and the pool becomes the
hard ceiling on outbound concurrency per upstream. Behavior at the call site —
arguments, response type, timeout budget, exception classification — does not
change.

## Why not HTTP/2 multiplexing

Multiplexing was requested alongside pooling and is **deliberately out of
scope**. It would not engage on the paths this change is for:

- httpx negotiates HTTP/2 through **TLS ALPN only**. It does not perform a
  cleartext `h2c` upgrade — `httpcore/_sync/connection.py` selects an
  `HTTP2Connection` on `http2_negotiated or (self._http2 and not self._http1)`,
  and the second disjunct is prior-knowledge h2, which requires disabling
  HTTP/1.1 entirely and would break any upstream that does not speak h2.
- The **agentclaw proxy / container hop** (`BaasService.invoke_http` → the
  `general` client) addresses containers directly as
  `http://<container-ip>:200xx`. Plaintext, no ALPN, no multiplexing —
  regardless of any flag.
- The **BaaS API hop** is an internal, in-mesh endpoint. `baas_client.py`
  documents it as carrying no `Authorization` header because "security is
  provided by MOSN service mesh"; every base URL in this repository is
  `http://`.

Turning `http2=True` on for those upstreams would add the `h2` dependency, a
config knob, and a wire-protocol risk surface in exchange for no behavior
change. If a TLS upstream later justifies it (the `general` client also carries
LLM API traffic, which *is* `https://`), it is a one-kwarg follow-up on top of
the pooled client this spec builds — pooling is the prerequisite either way,
since HTTP/2 multiplexing is meaningless on a client that is discarded after one
request.

## What pooling actually buys on HTTP/1.1

Stated plainly so the win is not overclaimed. HTTP/1.1 keep-alive reuses a
connection for *sequential* requests; one request is in flight per connection at
a time, so N concurrent callers still need N connections. What changes:

1. **No handshake per call.** A reused connection skips the TCP three-way
   handshake — one round trip saved per request across a mesh hop.
2. **No socket churn.** Today every call leaves a closed socket in `TIME_WAIT`.
   Under the burst parallelism this is meant to enable, that is the concrete
   ephemeral-port-exhaustion risk, and it goes away.
3. **A ceiling that exists.** `max_connections` caps simultaneous connections
   per upstream client. Past the cap a request waits for a free connection
   rather than opening another socket, and fails with `httpx.PoolTimeout` if the
   per-call timeout elapses first. This is the backpressure the change is for:
   the fan-out is bounded at the client instead of being absorbed by the
   upstream.

## Scope

**In scope**

- `HttpxClient` holds one pooled `httpx.Client`, built lazily on first use and
  shared by every subsequent call, including `stream`.
- Pool ceilings (`max_connections`, `max_keepalive_connections`,
  `keepalive_expiry`) are explicit and configurable through the existing typed
  config path, applied to all four qualified bindings.
- The pool is released at process shutdown through the existing `Lifecycle`
  contract.
- `timeout` moves from the (now shared) client constructor to the per-request
  call, preserving the existing per-call timeout budget.

**Out of scope**

- HTTP/2 / multiplexing (see above).
- `LocalHttpClient` and the `test` / `corp_test` profile bindings — they never
  touch the network and gain nothing from a pool.
- Retry, circuit-breaking, or any change to how failures are classified.
- The async `aiohttp` / `requests` call sites elsewhere in the backend.
- Per-qualifier pool tuning. One policy applies to all four bindings; if
  `general` later needs its own ceiling, that is an additive follow-up.

## Acceptance criteria

1. **Connections are reused.** Two sequential calls on one `HttpxClient`
   instance issue their requests through the *same* underlying `httpx.Client`;
   no client is constructed per call.
2. **The pool is bounded and configured.** The client is built with an explicit
   `httpx.Limits` carrying the configured `max_connections`,
   `max_keepalive_connections`, and `keepalive_expiry`. Defaults are httpx's own
   (100 / 20 / 5.0s) and are overridable from the `http_client` config block.
3. **The wire shape is unchanged.** For every verb, arguments left as `None` are
   still omitted from the underlying request, and the request is still issued
   against the relative path with `base_url` supplied by the client. A caller
   passing an absolute URL (the `general` client's contract) still bypasses
   `base_url`.
4. **The timeout budget is unchanged.** A call passing `timeout=T` still applies
   `T` to connect / read / write / pool for that request and no other.
5. **Nothing is swallowed.** `raise_for_status` errors and transport errors
   propagate unchanged. Pool exhaustion surfaces as `httpx.PoolTimeout`, which
   is an `httpx.TimeoutException` and therefore already classifies as
   `HttpClientTimeoutError` at the boundary — no new exception type escapes the
   seam.
6. **Streaming shares the pool and does not close it.** `stream()` yields a
   streaming response from the shared client; when the `with` block exits the
   connection returns to the pool and the client stays usable for later calls.
7. **The pool is released at shutdown.** `HttpxClient` participates in the
   `Lifecycle` teardown phase and closes its client; discovery picks it up
   without a hand-maintained registration list.
8. **Thread-safe.** Lazy construction is safe under concurrent first calls from
   `asyncio.to_thread` worker threads — exactly one client is ever built per
   instance.
9. The existing backend unit suite passes, including the `HttpClient` contract
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
long-lived by nature; enough concurrent streams can saturate that client's pool
and make ordinary calls on the same client wait. The 100-connection default
leaves ample headroom, and the ceiling is configurable, but the interaction is
real and is documented at the seam.

**The out-of-repo corp send-hook wrapper.** `llm.py` documents a corp-side hook
that wraps httpx transport failures into `HttpxCallingException('Error in httpx
send hook')`. It hooks `send`, whose signature and call shape this change does
not touch — but it has only ever observed clients that live for one request.
Community and singlebox CI cannot exercise it. Flagged for corp validation
rather than mitigated here.

**Process-wide socket ceiling rises in principle.** Four singleton clients ×
`max_connections` = up to 400 concurrent connections at the default, versus
"unbounded but short-lived" today. This is a ceiling where there was none, not a
new floor: steady-state socket count goes *down*, because connections are reused
instead of rebuilt.
