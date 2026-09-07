# Request and operation observations

This instrumentation identifies which application stage is waiting and connects
it to dependency client observations. It does not change operation deadlines,
retry policy, authorization decisions or business return values.

## Reading an incident

1. Find `http.request.started` by `request_id`. The middleware accepts a safe
   `X-Request-ID` (1–128 ASCII letters/digits or `-_.:`), otherwise creates a UUID,
   and echoes it in the response. Extractor errors and coordination callbacks are
   included; coordination callbacks still do not create tracing spans. CORS
   short-circuit requests retain their existing special path.
2. Find `http.request.response_ready` or `http.request.cancelled`. The former
   measures time until the application constructs a response, including handler
   work. It is **not** the lifetime of an SSE/WS stream or network body transfer.
3. Read `http.request.operations`: `observations` is a JSON-encoded map containing
   each fixed operation name's count, total/max milliseconds and outcomes. It is
   emitted when the request future returns. Cancellation has termination events;
   it does not currently emit an aggregate snapshot. Background operations that
   finish after the response are not included in the already emitted snapshot.
4. Use `operation_id` and `parent_operation_id` to reconstruct nested stages from
   log events. These observations do not create tracing spans or record metrics.
   Correlation uses request and operation IDs without reading, propagating or
   emitting a distributed `trace_id` in these observation records.
   Existing gateway span selection and downstream TraceContext propagation are
   unchanged. `in_current_context` carries request/operation IDs and the logging
   subscriber into selected detached work. It does not
   retain or enter the request span, so background work cannot extend that span's
   lifetime or change its attributes through this helper. Detached run duration
   remains distinct from request latency.

An independently received WebSocket callback does not automatically inherit the
original HTTP request ID. Existing business logs expose identifiers such as
`run_id` for cross-request investigation; generic operation records do not
automatically include those identifiers. Distributed tracing integration is a
separate concern from these log observations.

A parent stage contains its children. Parallel child durations overlap. Do not
sum all entries as request time, subtract them indiscriminately, or interpret
wall-clock time as CPU time.

## Covered boundaries

| Area | Observations |
| --- | --- |
| `/bots/query` | auth, batch load, per-bot status enrichment, input/unique/returned counts, response conversion |
| Bot repository | memory hit/miss, memory lock wait, DB load, cache read, decode, omitted-load and default-status fallbacks, partial status-write failures |
| chat / chat-async acceptance | Bot/human auth, run-channel/context registration, ownership/organization/reachability, target/availability, Run create/state update, credentials, security and delivery |
| Auth chain | plugin order, skipped/no-match/success/error, plugin and elapsed chain duration |
| Internal waits | Run-channel registry read lock, enqueue backpressure, event-delivery semaphore permit |
| Shared external adapters | Provider request/ack-body, Fuse operations, event webhook, BaaS storage control calls, BaaS/AntDing callbacks, friend-work-order and admin-terminal callbacks, explicit DNS/policy validation |

The client observations measure the boundary visible to this process. For
example HTTP `send` includes DNS/connection/TLS where performed by the library,
and receipt of headers; it does not by itself measure body consumption or
remote processing time. Optional adapters outside this table are not claimed to
have detailed phase instrumentation. Existing business and stream metrics remain
the source for delivery disposition and terminal Run results.

## Semantics and cost

- `observe_result`: `success`/`error` reflect the Rust `Result`. A successful
  transport result is not evidence of HTTP 2xx or a successful business response;
  use the adapter's status/result record too.
- `observe_value`: `completed` means the future returned, including `None` or
  `false`; it does not relabel those values as business success.
- Each operation finishes once, including `cancelled`/`panicked` on drop.
- Fast operation completions are DEBUG; errors, cancellation and operations at
  least 100 ms are WARN. A call still pending at 5 seconds emits one
  `bcs.operation.stalled` warning and continues awaiting its original result.
  Normal long-lived run draining is not classified by this threshold.
- Request summaries and existing client-specific logs have their own INFO/error
  levels. No request bodies, headers, raw keys, tokens, SQL parameters or returned
  values are added to generic observation events. This does not alter unrelated
  historical payload logging elsewhere in the application.
- Observed futures are boxed at the wrapper boundary to bound nested adapter
  Future stack size; this adds one allocation per instrumented operation.
- Use fixed names only; request/operation IDs are log fields. Durations and
  outcomes are written to completion logs and accumulated for the request summary.
  `count` adds an outcome count to that summary only. No operation counters,
  histograms or gauges are exported to `/metrics` by these helpers.

File outputs now use a bounded nonblocking writer (4,096 queued lines/output).
The worker performs rotation and writes; retained shutdown guards flush the queue.
If the sink stalls, lines can be dropped instead of blocking request workers.
No dropped-line metric is exported. Console output and disk/collector health are
separate concerns; logs are not guaranteed complete when the file queue is full.

## Deployment

Keep `bcs_http_access` and `bcs_observation` enabled at INFO in file output target
filters (an existing `*` output already includes them). DEBUG operation detail
can be enabled temporarily for diagnosis; keep existing sampling/exporter settings.
No collector, dashboard, alert or online experiment is installed by this change.

## Package ownership and migration

- `bcs-observability` owns `Operation`, `observe_result`, `observe_value`, request
  aggregation and correlation scopes. It prints structured events directly with
  the Rust tracing log API; file output and subscriber configuration remain in bootstrap.
- The base has no OpenTelemetry or metrics dependency, including with all features.
  Request/operation scopes restore outer values after completion or cancellation and
  remain isolated when futures are polled concurrently.
- `bcs-http::gateway_trace::observe_request` establishes the request log context.
  Neither it nor the WebSocket dispatcher copies an OpenTelemetry trace ID into
  observation context. The base never reads a current span and has no trace-ID scope.
- `bcs-telemetry` retains the original pure GenAI message attribute encoders.
  Existing A2A span creation, provider TraceContext propagation and bootstrap
  exporter configuration retain their owners and behavior.
- Migrate operation calls/dependencies from `bcs_telemetry` / `bcs-telemetry` to
  `bcs_observability` / `bcs-observability`. Encoder callers keep their old dependency.
  Public and internal workspace consumers must use matching revisions. The temporary
  trace-ID helpers and the added observation `trace_id` fields have been removed;
  log queries should use request/operation IDs. Existing tracing diagnostic logs,
  dependency protocol trace fields, wire contracts and configuration are unchanged.
- Future tracing integrations, including SOFATracer in the internal overlay,
  should declare their own context/propagation contract. This change adds no
  tracing plugin runtime, automatic spans, or metrics; the existing tracing
  event/subscriber mechanism remains the logging extension point.
