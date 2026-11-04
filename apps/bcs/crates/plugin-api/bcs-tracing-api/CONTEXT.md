# Network tracing plugin contract

## Provides

An optional, trusted in-process network tracing hook. `NetworkTracer::start`
receives bounded transport metadata, a header carrier and an optional opaque
parent context. It returns a propagation carrier, cloneable context and a
completion record. `Call` completes the record exactly once, with `Cancelled`
on drop. Providers must not panic, block on I/O, or alter business results.

## Consumes

Standard-library shared ownership and Tokio task-local scopes only. No vendor
SDK, HTTP/gRPC framework, exporter, configuration loader or business services.
`Context` is an opaque shared value owned by the installed implementation.

## Lifecycle and compatibility

Bootstrap may install one process-wide provider before client initialization.
No installed provider is the default no-op implementation: no propagated
headers or records, and existing transport behavior is unchanged. A second
installation fails rather than silently replacing an active provider.
`with_provider` provides scoped composition, including an explicit no-op scope.

`with_context` restores the outer scope on return, error or cancellation.
`in_current_context` captures at construction, before spawn; its future installs
the captured context and provider in the child task. The blocking equivalent
captures a closure for `spawn_blocking`. Cloning a context must preserve shared
child allocation state. Neither helper persists context in queues or messages.
WebSocket frames and durable recovery are outside this contract.

## Consumers and tests

Network adapters and external clients use the hook at transport boundaries.
Core business behavior and log-only operation observation are independent.
The public network adapter conformance suite checks no-op and completion
semantics. Each exporter must also test propagation, concurrent allocation,
cancellation, and output. This is an additive optional Plugin API; it does not
change business protocols, database schemas, or existing OpenTelemetry APIs.
