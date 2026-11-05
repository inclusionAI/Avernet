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

HTTP clients (reqwest 0.12/0.13 and blocking) supply their existing headers to
the plugin; A2A RPC supplies existing ASCII metadata. Binary metadata remains
transport-owned. No request headers are reinterpreted as ambient SDK context.
Server metadata includes the actual Axum `MatchedPath` in `route`; clients have
no matched route and use `None`. Sanitized endpoint metadata is unchanged.

The optional `otel` feature exposes `OtelContextBridge(provider)`. Composition
wraps a selected provider before installation. The bridge serializes the current
valid `tracing` OTel span, falling back to OTel's native current context, into
the separate ambient carrier. It does not install a subscriber/exporter or
activate native scopes. Explicit spawn helpers also preserve a propagation
snapshot when the child has no current SDK context.
The embedding OTel setup must configure its global text-map propagator, as BCS
bootstrap already does; the bridge does not replace that configuration.

HTTP middleware places the server span's validated propagation fields in the
`HttpServerContext` request extension, without changing the original incoming
headers. BCS's gateway OTel layer extracts a valid parent from that extension,
falling back to the original headers. A W3C network server span therefore parents
the gateway span; an inner business span can in turn parent an outbound network
span through the ambient bridge. A SOFA-only extension leaves W3C extraction
and the original OTel namespace independent. With no provider, existing behavior
is unchanged.

## Tests

`tests/conformance.rs` exercises real adapter consumers with a recording plugin,
including business HTTP failures, request exclusions, cancellation, response
readiness and no-op behavior.
The live `caller_execution_story.py` E2E installs a recording plugin in the
existing test-utils executable and verifies Provider propagation, business
failure records, probe exclusion and metadata redaction through real HTTP.
`tests/routes.rs` checks stable templates across resource IDs, nested mounts and
exclusions. `bcs-http/tests/network_tracing_otel.rs` uses an SDK-backed W3C provider,
the actual gateway layer and Provider HTTP transport to check complete parent
chains, ambient/header conflicts, explicit spawn and unsampled propagation.
The message-flow tracing test drives WebSend and retained BotEvent processing
through HTTP into the real work-order notifier, holding delivery until after
request completion to check isolated traces and scoped no-op propagation.
