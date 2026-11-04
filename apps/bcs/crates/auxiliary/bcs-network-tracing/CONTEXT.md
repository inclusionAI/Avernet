# Network tracing transport adapters

## Provides

Axum HTTP middleware and reqwest extension methods consuming `bcs-tracing-api`.
The optional reqwest13 and blocking features support existing client versions
without changing their network policies. Bootstrap places middleware outside
auth, panic handling and CORS so selected business failures remain observable.
`HttpServerFilter` receives exact excluded Axum route patterns from composition;
unmatched routes and CORS preflights are always excluded. Excluded requests
disable the provider and clear the context for their whole handler scope,
including any instrumented clients called by a probe. The caller's scope is
restored afterwards. The adapter owns no business route names.

## Consumes

The public tracing contract, Axum and reqwest. No concrete exporter, business
services, environment access or vendor identity. Provider installation belongs
to the embedding composition root. Public executables use the default no-op.

## Behavior

Incoming HTTP scope ends at response-header readiness. BCS composition excludes
WebSocket endpoints, health/metrics probes and static manifest/assets routes.
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
including business HTTP failures, request exclusions, cancellation, response
readiness and no-op behavior.
