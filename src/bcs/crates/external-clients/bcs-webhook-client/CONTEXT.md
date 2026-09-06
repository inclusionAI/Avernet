# bcs-webhook-client Context

## Provides

- `EventDeliveryPort` adapter for one Webhook HTTP Attempt.
- Per-attempt endpoint validation, fresh DNS resolution, attempt-local use of
  the validated addresses, no-redirect HTTP execution,
  HTTPS/loopback and standard-port policy, bounded connect/response handling,
  and transport/HTTP outcome classification.
- Canonical BCS Webhook fixed-header construction.

## Consumes

- Transport-neutral delivery requests from `bcs-service-api`.
- `bcs-route-security::OutboundUrlGuard` for host and resolved-address policy.
- Endpoint URL passed explicitly for the Attempt.

## Allowed dependencies

- `auxiliary/bcs-observability` for log-only operation observations and correlation.

- `bcs-service-api` delivery port and `bcs-route-security`
- HTTP, URL, time parsing, and async utility crates

## Forbidden dependencies

- `bootstrap/bcs`, application services, repositories, or database plugins
- Direct environment/config discovery
- Retry, DLQ, Subscription lifecycle, or Event projection policy

## Runtime ownership

This crate owns one outbound HTTP Attempt only. Eventing owns scheduling,
retries, ordering, replay, and Subscription policy.

## Tests

- `cargo test -p bcs-webhook-client`
