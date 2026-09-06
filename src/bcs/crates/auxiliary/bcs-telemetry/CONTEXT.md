# bcs-telemetry Context

## Provides

- Pure encoders for bounded OpenTelemetry GenAI input/output message attributes.
- UTF-8-safe truncation and captured/original byte counts.

## Consumes

- Adapter-provided text and capture limits.

## Allowed dependencies

- JSON serialization utilities.

## Forbidden dependencies

- Business services, transport clients, logging runtime and exporter creation.
- Request/task-local state, operation timers and tracing/metrics SDKs.

## Runtime ownership

The crate only encodes attribute values. Callers own span creation, attributes,
export and sampling. Log-only operation observations live in bcs-observability.

## Tests

`cargo test -p bcs-telemetry --test gen_ai_messages`
