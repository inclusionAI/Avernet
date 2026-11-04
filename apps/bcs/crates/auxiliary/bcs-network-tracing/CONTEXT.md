# Network tracing transport adapters

## Provides

Axum HTTP middleware and reqwest extension methods consuming `bcs-tracing-api`.
The optional reqwest13 and blocking features support existing client versions
without changing their network policies. Bootstrap places middleware outside
auth, panic handling, fallback and CORS so all HTTP responses are observable.

## Consumes

The public tracing contract, Axum and reqwest. No concrete exporter, business
services, environment access or vendor identity. Provider installation belongs
to the embedding composition root. Public executables use the default no-op.

## Behavior

Incoming HTTP scope ends at response-header readiness, including 101 upgrades.
It does not encompass WebSocket frames or consume streaming bodies. Client
spans likewise end at response headers; gRPC clients record the initial status.
Stream-message and final-trailer tracing require a separate future contract.

The existing client retains ownership of TLS, connection pools, deadlines,
redirect policy, authentication and retries. Each explicit send is a new call.
Transparent redirects/retries inside reqwest remain one logical client call.
URI credentials, query and fragment are removed from trace metadata. Plugin
propagation is inserted using validated HTTP header names and values. Existing
headers outside the provider's propagation fields remain unchanged.

## Tests

`tests/conformance.rs` exercises real adapter consumers with a recording plugin,
including HTTP failure, cancellation, response readiness and no-op behavior.
