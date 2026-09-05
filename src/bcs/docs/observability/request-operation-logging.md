# Request and operation observations

This instrumentation identifies which application stage is waiting and connects
it to dependency client observations. It does not change operation deadlines,
retry policy, authorization decisions or business return values.

## Reading an incident

1. Find `http.request.started` by `request_id`. The middleware accepts a safe
   `X-Request-ID` (1–128 ASCII letters/digits or `-_.:`), otherwise creates a UUID,
   and echoes it in the response. Extractor errors are included. The coordination
   callback and CORS short-circuit requests retain their existing special paths.
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
   `trace_id` reads an existing trace context when available and is otherwise empty;
   request and operation IDs provide correlation even without tracing enabled.
   Existing gateway span selection and downstream TraceContext propagation are
   unchanged. `in_current_context` carries request/operation/existing trace context into selected
   detached work; detached run duration remains distinct from request latency.

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
