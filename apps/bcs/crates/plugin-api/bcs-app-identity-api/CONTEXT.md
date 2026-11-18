# Outbound application identity

The public bootstrap optionally selects one linked `OutboundAppIdentityFactory`
by name under `[a2a_http_identity]` in server configuration. The plugin receives
its own options. Leaving this section absent disables the contract. An invalid,
missing, or ambiguous factory fails initialization.

Built-in A2A HTTP delivery calls `OutboundAppIdentity` with the Provider ID and
the canonical origin of the HTTP request after the standard outbound address
guard. The plugin can omit a credential for targets where it is optional,
return one sensitive header, or reject the request. The header is added before
network I/O. The adapter rejects malformed headers, conflicts with request
headers, and reserved HTTP, A2A and tracing names. Credentials are request-local
and excluded from the HTTP trace carrier.

The same contract applies independently to each REST and JSON-RPC operation,
including SSE setup and subsequent subscriptions. Card discovery, Provider
webhooks, and other A2A bindings use their own transports. Application identity
verification on inbound requests can be added as a separate capability.

[HTTP consumer contract tests](../../external-clients/bcs-a2a-http/src/http_client/tests.rs)
exercise the two bindings over loopback with a recording plugin.
