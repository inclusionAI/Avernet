# bcs-observability Context

## Provides

- Log-only operation timing, termination, request aggregation and correlation.
- Parent/child observation IDs and explicit propagation into spawned tasks.
- `with_trace_id`: a data-only scope for an adapter-supplied trace ID string.

## Consumes

- The logging subscriber installed by bootstrap through tracing events.
- Tokio task-local context and a diagnostic timer; no operation deadline is imposed.
- Caller-provided operation names, outcomes and correlation IDs.

## Allowed dependencies

- Tracing logging/subscriber APIs, async timing, UUID and serialization utilities.

## Forbidden dependencies

- OpenTelemetry APIs/SDKs, tracing-opentelemetry, bcs-telemetry and metrics implementations.
- BCS business services, adapters, concrete plugins or dependency clients.
- Exporter/client creation, environment/configuration loading, retry or routing policy.
- Company-specific SDKs, identities or endpoints.

## Runtime ownership

This auxiliary crate observes caller-owned futures and preserves their results and
cancellation. Operation names and outcomes are bounded constants. It never inspects
or logs operation arguments, values or raw errors. It emits tracing log events;
bootstrap controls filtering, formatting, files and subscriber lifecycle.

`with_trace_id` accepts an opaque string, with empty meaning absent; the adapter
owns validation/extraction. `current_trace_id` only reads that data. Nested scopes
restore outer values, and scope completion/cancellation removes the inner value.
Detached work inherits IDs only through `in_current_context`, which copies the
request/operation/trace correlation and logging subscriber, never a live span.
The helper neither enters a span nor prolongs its lifetime. New tasks without an
explicit wrapper do not inherit task-local correlation automatically.

A future trace adapter may consume a declared observation hook from this package;
the base must never import its implementation. This extraction uses the existing
tracing event/subscriber extension mechanism and adds no plugin runtime or spans.

## Change impact

Public and internal operation callers depend on this package. GenAI encoders stay
in bcs-telemetry. HTTP and WebSocket tracing adapters explicitly inject existing trace IDs;
callers outside an injected scope get an empty trace ID and may still correlate
by request/operation ID. Log schemas, levels, thresholds and result semantics are
unchanged. The public revision and internal path dependencies must move together.

## Tests

- `cargo test -p bcs-observability` covers result preservation, redaction,
  cancellation, stalled calls, nested/spawned IDs and trace scope isolation.
- `cargo test -p bcs-http --test request_observations` covers the real HTTP bridge
  and proves detached logs do not delay either existing A2A span's export.
- `cargo test -p bcs-ws --test frame_compat` covers WebSocket callback trace
  correlation, run aliases, missing trace mappings and scope cleanup.
- `bash scripts/ci/check-observability-deps.sh` checks the normal/build dependency
  tree with all features for trace/metric implementations.
