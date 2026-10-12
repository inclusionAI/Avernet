# QwenWork bridge engine

## Provides

- `QwenWorkFactory`, the `qwenwork` implementation of bridge's Engine Plugin API.
- Loopback WeCom-compatible WS ingress, authenticated HTTP Hooks and local health.
- Conversation correlation, cumulative text conversion and tool event translation.

## Consumes

- `bcs-bridge-core::engine` and engine-neutral stream event constructors.
- QwenWork's WeCom Bot protocol and HTTP Hooks.
- Read-only QwenWork SQLite chat metadata (never transcript contents).

## Ownership and allowed dependencies

This is a desktop delivery adapter, separate from bridge's shared runtime and
BCS server services. It owns QwenWork protocol details only. Bridge owns BCN
connections, run ids, idempotency, session serialization and durable mappings.
The CLI composition root registers this factory; bridge core does not depend
on this implementation. Dependencies are bridge core, protocol DTOs, Tokio,
Axum, SQLite and serialization. No server services, Python process or sibling
engine implementation is called.

## Compatibility and lifecycle

The additive `EngineFactory::requires_bin()` defaults to true for existing
subprocess engines. This engine overrides it to false. Registration omits
`engine_bin`; library consumers must build this listener-backed factory within
a Tokio runtime. Binding errors fail startup; engine drop cancels the listener.
`engine_session_id` is a stable adapter conversation UUID, not QwenWork's SDK
session id. It is acknowledged through SessionObserver before any prompt is
sent, and QwenWork resumes by the same IM conversation on subsequent turns.

Main-request completion is supplied by the desktop's top-level `QueryEnd`
Hook, bound by the preceding `UserPromptSubmit.request_set_id` and exact prompt.
`Stop` and compact Hooks do not terminate a run. Compact remains an adapter
state; all tool input/output bookkeeping and durable conversation ownership
survive it. Lifecycle Hooks share the existing per-run SDK-session cache: one
cold SQLite metadata lookup per new session, no extra per-tool/query polling.
Main completion still reconciles the request-id-scoped WS final and tool results.

Desktop schema/protocol updates affect only this crate. No BCN wire format,
BCS routing policy or server database migration changes.

## Validation

`cargo test -p bcs-bridge-qwenwork` drives the real adapter against a local WS
peer and SQLite fixture. It also drives bridge's runtime consumer and checks
the generated stream and persisted session mapping. CLI registration tests
cover executable-free registration. See README for configuration and limits.
