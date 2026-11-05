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
propagation uses validated HTTP header names and values. Clients first remove
all old values of the exact fields in `StartedTrace.remove_headers`, then insert
the new fields. Unspecified headers and independent tracing namespaces remain
unchanged. Invalid field names are ignored; a name in both lists is replaced.

Clients with custom default headers use `ClientWithDefaults::new(transport,
headers)`, with the transport built **without** reqwest `default_headers`.
The wrapper retains the configured transport and its connection pool. Its
`request(|http| ...)` builder and `execute_traced` merge defaults into the request
before extraction/removal/injection, including in no-provider mode. Explicit
request values override all defaults of the same case-insensitive name; repeated
values, opaque values and sensitive flags are preserved. Shared defaults are
never mutated. `RequestWithDefaults::map` applies further request configuration;
`send` is an explicitly untraced send for discovery paths.

Raw reqwest extension methods cannot remove private client defaults: reqwest
adds those after the hook, and exposes no removal API. They remain supported
for transports without default propagation fields. Do not put tracing defaults
in the underlying reqwest builder or replace it with a new per-send client.
A2A REST and Fuse construction use the controlled wrapper. This is a caller
migration, not a change to TLS/proxy/DNS/redirect/timeout policy or to the SPI.

HTTP clients (reqwest 0.12/0.13 and blocking) supply their existing headers to
the plugin; A2A RPC supplies existing ASCII metadata. Binary metadata remains
transport-owned. No request headers are reinterpreted as ambient SDK context.
Server metadata includes the actual Axum `MatchedPath` in `route`; clients have
no matched route and use `None`. Sanitized endpoint metadata is unchanged.

SDK providers may implement the contract's `capture_ambient` hook to serialize
their current context into the separate ambient carrier. Scope selection and
SDK activation remain the embedding application's responsibility. The draft
`otel` feature and bridge are removed; these transport adapters have no OTel
SDK dependency. Explicit spawn helpers preserve a provider-supplied propagation
snapshot when the child has no current SDK context.

HTTP middleware places the server span's validated propagation fields in the
`HttpServerContext` request extension, without changing the original incoming
headers. BCS's gateway OTel layer extracts a valid parent from that extension,
falling back to the original headers. A W3C network server span therefore parents
the gateway span; an inner business span can in turn parent an outbound network
span through the provider's ambient hook. A provider from another namespace
leaves W3C extraction and the original OTel namespace independent. With no
provider, existing behavior is unchanged.

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
It also checks mixed native/tracing scope restoration and the original gateway
OTel behavior with no network provider. `tests/propagation.rs` verifies stale B3
encoding removal, repeated fields, independent namespaces and disabled behavior
through both reqwest versions and blocking HTTP.
The message-flow tracing test drives WebSend and retained BotEvent processing
through HTTP into the real work-order notifier, holding delivery until after
request completion to check isolated traces and scoped no-op propagation.

Default-header regression cases cover default-only and request-plus-default
carriers through async send/execute and blocking send. Wire assertions check
B3 precedence, missing optional fields, disabled scopes and reused clients.
Additional cases check request overrides, repeated/opaque/sensitive values,
build errors, configured redirects and deadlines.
