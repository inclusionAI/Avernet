# Network tracing plugin contract

Contract revision: 2026-11-05 (explicit propagation field removal).

## Provides

An optional, trusted in-process network tracing hook. `NetworkTracer::start`
receives transport metadata, the existing transport header carrier and separate
`ParentContext` sources. It returns a propagation carrier, cloneable context and a
completion record. `Call` completes the record exactly once, with `Cancelled`
on drop. Providers must not panic, block on I/O, or alter business results.

## Consumes

Standard-library shared ownership and Tokio task-local scopes only. No vendor
SDK, HTTP/gRPC framework, exporter, configuration loader or business services.
`Context` is an opaque shared value owned by the installed implementation.

## Metadata and parent selection

`RequestInfo.route` is the matched HTTP server route template, including mount
prefixes (for example `/api/v1/bots/{id}/chat`). It is `None` on HTTP/RPC clients.
`endpoint` retains its sanitized URL/path or RPC service meaning; it must not be
used as a substitute for a stable route label. Adding `route` and replacing the
old `Option<&Context>` argument with `ParentContext` are source-level changes:
provider implementations and request-info literals must migrate together.

For HTTP servers, extract the remote parent from `incoming`; an unrelated
ambient task is not the caller. For clients, resolve valid context for the
provider's protocol in this order: `parent.ambient`, `parent.local`, existing
transport propagation in `incoming`, then a new root. Ambient data is separate
from the request headers: a current business span may be a descendant of the
opaque network server context, and headers may have been prepared earlier.
Ignore context belonging to another tracer's namespace. A SOFA implementation
does not reinterpret W3C identifiers as SOFA identifiers.

`capture_ambient` is an optional synchronous SDK hook returning only valid
propagation fields. SDK integration and scope selection belong to the provider;
the transport adapter does not choose between native and `tracing` contexts.
For mixed OTel APIs, an embedding application can enable context activation on
its `tracing-opentelemetry` layer and let its provider read the unified native
current context. Independent scope stacks require an explicit selection rule
owned by that integration. The default hook returns an empty carrier.

`StartedTrace.headers` represents the newly started span, including on server
calls. `StartedTrace.remove_headers` lists exact propagation fields owned by
the provider to delete on outgoing clients, including obsolete encoding aliases
or optional fields absent from the new span. Providers see the complete
`incoming` carrier before deletion. HTTP and ASCII RPC adapters remove all old
values of each valid field name, then insert `headers`; an inserted value wins
when a name appears in both lists. HTTP field names are case-insensitive.
Invalid transport names/values are ignored, as with ordinary injection.
For reqwest clients, custom default propagation fields must be supplied through
the network adapter's `ClientWithDefaults` wrapper, so defaults are merged before
extraction and deletion; raw reqwest stores its defaults privately and injects
them after this hook. Empty removal lists preserve the previous insert-only behavior. Providers must
not delete another protocol's fields, credentials, tenant or routing metadata;
binary RPC metadata remains transport-owned. Servers do not delete or rewrite
ingress headers. Adapters preserve all fields outside the removal/insertion
lists. Providers using the same protocol must coordinate through their SDK
integration rather than independently injecting unrelated traces. `incoming`
can include authentication headers; it is
trusted in-process input for extraction, not exportable metadata. Never log or
export the full carrier or credentials.

This revision adds the required `StartedTrace.remove_headers` field: existing
provider struct literals must add `remove_headers: vec![]`, or declare their
protocol's obsolete fields. All bundled providers and test fixtures migrate
together. The draft `bcs-network-tracing/otel` feature and `OtelContextBridge`
are removed; SDK providers implement `capture_ambient` directly instead. The
existing gateway OTel subscriber, propagator and default no-provider behavior
are unchanged. No internal SDK implementation belongs in this public contract.

Sampling controls recording, not propagation: an unsampled valid parent must
still produce a valid continuing context and propagation fields. Its completion
record may be a no-op. Never replace an unsampled trace with a sampled root.

## Lifecycle and compatibility

Bootstrap may install one process-wide provider before client initialization.
No installed provider is the default no-op implementation: this hook creates no
records or header changes, and existing transport behavior is unchanged.
Independent OTel instrumentation may still propagate its own context. A second
installation fails rather than silently replacing an active provider.
`with_provider` provides scoped composition, including an explicit no-op scope.

`with_context` restores the outer scope on return, error or cancellation.
`in_current_context` captures at construction, before spawn; its future installs
the captured context, provider and ambient propagation snapshot in the child task.
A valid current SDK context takes precedence over the inherited snapshot.
The blocking equivalent
captures a closure for `spawn_blocking`. Cloning a context must preserve shared
child allocation state. Neither helper persists context in queues or messages.
WebSocket frames and durable recovery are outside this contract.

These scopes propagate network context only. They do not activate an SDK's
native current context, attach business logs, or make automatic instrumentation
inherit a span. Such integration requires a concrete SDK bridge with enter/exit
on every future poll; never hold a thread-local guard across `.await` or bind in
`start` and unbind in `finish`.

The global provider is retained by `OnceLock`; its ordinary Drop is not an
exporter shutdown mechanism. Composition owns a separate exporter/guard. Stop
request admission, complete or cancel and join trace-producing derived work,
then flush/shutdown the exporter (or drop its explicit drain guard). Every
cancelled call must finish its record before exporter shutdown. The SPI does
not itself track detached tasks or provide async lifecycle management.

## Consumers and tests

Network adapters and external clients use the hook at transport boundaries.
Core business behavior and log-only operation observation are independent.
The public network adapter conformance suite checks no-op and completion
semantics. Each exporter must also test propagation, concurrent allocation,
cancellation, and output. This optional Plugin API does not change business
protocols, database schemas, or the existing OpenTelemetry API surface.
