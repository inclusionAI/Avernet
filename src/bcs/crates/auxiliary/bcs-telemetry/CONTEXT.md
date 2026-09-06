# bcs-telemetry Context

## Provides

- Transport-neutral operation timing, termination, request aggregation and correlation in logs.
- Parent/child observation IDs and log correlation propagation into explicitly wrapped spawned tasks.
- Existing OpenTelemetry semantic attribute encoders.

## Consumes

- The log subscriber and existing trace context installed by bootstrap.
- Tokio task-local context and a diagnostic timer; no operation deadline is imposed.

## Allowed dependencies

- Tracing logging APIs, existing OpenTelemetry context, async timing, UUID and serialization utilities.

## Forbidden dependencies

- BCS business services, adapters, concrete plugins or dependency clients.
- Exporter/client creation, environment/configuration loading, retry or routing policy.
- Company-specific SDKs, identities or endpoints.

## Runtime ownership

This utility observes caller-owned operations and preserves their result, cancellation
and awaits the caller-owned future. Operation names and outcomes must be bounded constants. It
never inspects or logs operation arguments, values or raw errors. The observations
do not create spans or record metrics; existing trace IDs are read only for log
correlation. Detached work carries the trace ID string, not a span handle, so the
helper does not extend request span lifetimes or enter their context. Subscribers,
export and sampling remain owned by bootstrap. Services and
adapters may use this utility without importing each other's implementations.

## Tests

`cargo test -p bcs-telemetry --test operations` covers cancellation, stalled calls,
result preservation, redaction, nested/spawned correlation, absence of new spans
and non-Send outputs.
