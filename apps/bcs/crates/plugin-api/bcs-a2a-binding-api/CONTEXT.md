# Custom A2A Binding Plugin API v1

This is a trusted in-process outbound Plugin API, not an A2A wire extension.
Deployment composition installs one immutable Registry before starting BCS.
Public builds install nothing and cannot select custom declarations. Duplicate,
reserved or invalid binding names and a second installation are rejected.

## Provides and consumes

Binding validates an interface declaration, resolves a stable non-secret key,
provides a configuration fingerprint and constructs a normalized Client. Client
uses pinned A2A SDK task/message/event types at the outbound adapter boundary;
Bot core calls only the declaration projection and fingerprint operations and
never performs I/O. There is no dependency on service implementations, network
transports or deployment-specific code in this contract.

Registry creates an opaque internal route from binding name/key, stored in the
existing interface URL slot with binding `custom`. It is not a dialable endpoint.
Original Card JSON remains authoritative and unchanged. CUSTOM is reserved and
cannot be submitted as a wire binding. Unsupported bindings are skipped; enabled
but malformed declarations fail closed. Standard interfaces still require URLs.

## Lifecycle, compatibility and safety

Install once before serving. No hot swapping or background tasks. Routes and
adapter versions are immutable for process lifetime. A changed route fingerprint
must prevent old task operations from targeting the new backend. Restarts with a
missing plugin fail closed. No new DB round trips or tables are introduced.
Older binaries cannot decode custom run bindings: drain them before rollback.

Implementations enforce deployment-owned target allowlists and capabilities;
Card content may not choose implementation code, arbitrary connection targets or
credentials. resolve/fingerprint perform no I/O; client construction is lazy.
Errors must be safe machine codes. Timeout, credentials, tenant and negotiated
extensions are passed explicitly; adapters must honor them and normalize errors.
Do not silently retry side-effecting calls or invent Task identity. Stream drop
releases transport resources, but is not equivalent to remote cancellation.

Registration and refresh validate via the same projection as runtime selection.
This version is process-scoped because the runtime builds a single BCS server;
multi-server embedding must introduce per-server injection before supporting
independent plugin configurations in one process.

## Verification

`bcs-a2a-http/tests/support/custom_binding.rs` exercises admission, a real local
server, standard BCS event ingestion, durable correlation, cancellation and route
fencing through this contract. `bcs-chat-run-store` tests custom binding round trips.
Provider implementations additionally test their own declarations and wire mapping.

Singlebox runs `custom_binding_story.py` against the instrumented test-utils host
with an installed local example binding. All registration, refresh, streaming,
polling, recovery and cancellation enter through live HTTP/WS APIs, and only
the remote protocol is served by a local peer. No unit-test profiles or direct
domain calls contribute to this E2E evidence.
