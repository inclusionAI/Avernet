# bcs-telemetry Context

## Provides

- Transport-neutral operation timing, termination, request aggregation and correlation.
- Parent/child observation IDs and propagation into explicitly wrapped spawned tasks.
- Existing OpenTelemetry semantic attribute encoders.

## Consumes

- The tracing subscriber, OpenTelemetry layer and metrics recorder installed by bootstrap.
- Tokio task-local context and a diagnostic timer; no operation deadline is imposed.

## Allowed dependencies

- Tracing, metrics, OpenTelemetry APIs, async timing, UUID and serialization utilities.

## Forbidden dependencies

- BCS business services, adapters, concrete plugins or dependency clients.
- Exporter/client creation, environment/configuration loading, retry or routing policy.
- Company-specific SDKs, identities or endpoints.

## Runtime ownership

This utility observes caller-owned operations and preserves their result, cancellation
and awaits the caller-owned future. Operation names and outcomes must be bounded constants. It
never inspects or logs operation arguments, values or raw errors. Subscribers,
export, sampling and metrics exposure remain owned by bootstrap. Services and
adapters may use this utility without importing each other's implementations.

## Tests

`cargo test -p bcs-telemetry --test operations` covers cancellation, stalled calls,
result preservation, redaction, nested/spawned correlation and non-Send outputs.
