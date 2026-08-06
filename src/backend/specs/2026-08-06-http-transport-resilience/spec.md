# Backend HTTP Transport Resilience — Retry Component and Connection Reuse

## Summary

Two defects in the backend's outbound HTTP path make a single transient network
blip destroy a whole user action, and make every outbound request more expensive
than it needs to be.

1. **No retry on a connection-level failure.** `BaasService.get_http_info` issues
   one `GET /http-info` against BaaS with a 5-second deadline. If that request
   gets no answer, the exception propagates and the caller fails. For a local
   skill upload the caller is a per-file loop, so one blip discards the entire
   upload — including the files that already landed.

2. **No connection reuse.** The real `HttpClient` opens a fresh `httpx.Client`
   for every single request and closes it immediately after, so every outbound
   call pays a new TCP connect plus TLS handshake.

This feature adds a reusable retry component that any backend caller can adopt,
applies it to the one call site that provoked the incident, and makes the HTTP
client hold its connections open.

No API surface, request shape, response shape, status code, authorization rule
or persisted data changes.

## Motivation

### The incident

On 2026-08-06 a user uploading a multi-file local skill saw the upload fail. The
backend logged:

```
2026-08-06 20:41:12,101 - start - ERROR - [341] - [SpawnProcess-2:6]
  - [0b25540417860200566504643e3ecd]
  - [BaasService.get_http_info] Failed to get http info: Error in httpx send hook
```

The failing `GET /http-info` started at `20:41:07,059` and failed at
`20:41:12,101` — 5.042 seconds, exactly the call's deadline. BaaS's own access
log recorded the same request with `$request_time` ≈ 4996ms and status 499
(client went away). Successful requests in the same window took 46–132ms.

The failure is bimodal — roughly 60ms or roughly 5000ms, with nothing in
between — and the 499s arrive in tight clusters across unrelated bots and
unrelated paths. That points at a shared upstream stall, not at a per-request
fault. **The upstream cause is BaaS-side and out of scope here** (see
_Non-goals_); what is in scope is that the backend has no tolerance for it.

### Why one blip costs the whole upload

`SkillService.upload_skill` writes files one at a time, and every write resolves
its own connection info. A 24-file skill therefore makes ~25 independent
`/http-info` calls, each an independent chance to fail. The first failure raises,
and the handler deletes the entire skill directory — so the user loses the files
that uploaded successfully, not just the one that failed.

At a 1% per-call failure rate, a 25-call upload fails about 22% of the time.
Making a single call survive one blip collapses that back to near zero.

### Why the failure is unreadable in logs

The error text `Error in httpx send hook` names nothing useful. In production the
`httpx` send path is wrapped by an out-of-repo tracer (`sofa_tracer`) that
re-raises low-level transport failures as an opaque exception type this repo can
neither import nor subclass-match. The original error — was it a read timeout, a
connect timeout, a reset connection? — survives on `__cause__` / `__context__`,
but every current handler formats only the wrapper.

Two consequences the design has to respect:

- **Classification cannot be by exception type.** `except httpx.TimeoutException`
  does not catch a wrapped timeout. Callers must classify by symptom instead.
- **Diagnostics must unwrap the cause chain**, or the next incident is as opaque
  as this one.

This is not a new discovery. `core/harness/services/llm.py` already solved both
problems for the LLM client, in private helpers, with the reasoning written out
in their docstrings. Nothing else in the backend can reach them. Three other
places in the repo (`llm.py:5`, `harness_module.py:101`,
`bot_dormant/baas_client.py:51`) assert that the wrapper only patches
`AsyncClient` and therefore does not affect the synchronous client — an
assumption this incident contradicts, since the failing call went through the
synchronous client.

### Why connection reuse belongs in the same change

`HttpxClient._request` wraps every call in `with httpx.Client(...)`, so no
connection is ever reused. A 24-file skill upload opens roughly 50 fresh
connections — one per `/http-info`, one per file upload — each paying a full
TCP + TLS handshake against the same host it just disconnected from.

That is wasted latency on every outbound call in the backend, and it enlarges
the window in which a connection-level failure can occur — the same class of
failure the retry component exists to absorb.

## Goals

- A retry component that lives in backend infrastructure, is not coupled to any
  one caller, and can be adopted elsewhere without copy-paste.
- The component classifies retryable failures by symptom, so it works correctly
  whether or not the send-hook wrapper is present.
- Failure logs name the underlying cause rather than the wrapper.
- A transient connection-level failure on `GET /http-info` is survived rather
  than propagated.
- Outbound HTTP reuses connections instead of reconnecting per request.

## Non-goals

- **Fixing the BaaS-side stall.** The upstream root cause is blocking database
  I/O on the BaaS event loop in the `/http-info` request path. It is real, it is
  the reason the blips happen, and it is being handled separately. This feature
  makes the backend tolerate the blips; it does not remove them.
- **Retrying non-idempotent requests.** Only requests that are safe to repeat may
  adopt the component. File uploads and bot lifecycle mutations must not.
- **Retrying on HTTP status.** A completed 4xx/5xx response is an answer, not a
  transport failure. Status-based retry policy stays with the caller, which alone
  knows whether repeating is meaningful.
- **Resolving `/http-info` once per upload**, and **not deleting the whole skill
  on a transient failure.** Both are real and both were identified alongside this
  work; they are deliberately out of this change's scope and remain open.
- Changing `get_http_info`'s existing timeout default, its signature, or any
  other call site's behavior.

## Behavior

### Retry component

- Runs a caller-supplied operation and returns its result unchanged.
- On a failure that shows no sign of having received an HTTP answer, retries up
  to a bounded number of total attempts.
- On a failure that carries a client-error (4xx) response, does not retry —
  repeating a rejected request cannot succeed.
- Waits a short, randomized interval before a retry, so that many callers tripped
  by the same upstream stall do not retry in lockstep.
- Re-raises the final failure unchanged, so existing `except` clauses and cause
  chains at call sites keep working.
- Logs each retry and the final failure with the unwrapped cause chain.
- Exposes the symptom classifier and the cause-chain formatter as part of its
  public surface, so callers that need only those can use them without adopting
  the retry loop.

### `get_http_info`

- A `GET /http-info` that fails at the transport level is attempted a second
  time before the caller sees an error.
- A response that arrives — including an error status — is handled exactly as
  today. No new status is retried.
- The existing per-attempt deadline is unchanged. Worst-case latency for a fully
  failing call therefore rises from one deadline to two; this is the accepted
  cost of surviving a blip, and it is bounded.
- When the call ultimately fails, the log line names the underlying cause instead
  of `Error in httpx send hook`, and the raised error preserves the original
  exception as its cause.

### HTTP client

- Connections are reused across calls to the same client.
- Per-call timeout behavior is unchanged: each request still applies the timeout
  its caller passed.
- Concurrent use from multiple threads remains safe — the client is a
  process-wide singleton reached from thread-pool workers.
- Request wire shape is unchanged: arguments left unset are still omitted.

## Success criteria

- A single transport failure on `GET /http-info` no longer surfaces to the
  caller; a multi-file skill upload survives one blip that would previously have
  destroyed it.
- A failure carrying a 4xx response is not retried.
- Production failure logs for this path name the underlying exception type.
- Repeated calls through one `HttpClient` reuse a connection rather than opening
  one per request.
- The retry component is importable and adoptable by any backend module, with no
  dependency on the BaaS service or the skill-upload flow.
- No existing test changes behavior expectations; the LLM client's retry
  semantics are unchanged.

## Open questions

None blocking. Two decisions taken unilaterally, both reversible:

- **Attempt count and per-attempt deadline.** Two total attempts, existing
  deadline retained per attempt. Lowering the deadline (so two attempts cost less
  wall-clock than one does today) is defensible given the observed 60ms median,
  but it changes behavior for every other `get_http_info` caller and is not
  justified by evidence from those call sites.
- **Retry stays at the call site, not inside `HttpClient`.** Putting it in the
  transport would silently retry non-idempotent requests, including file uploads
  and bot creation.
