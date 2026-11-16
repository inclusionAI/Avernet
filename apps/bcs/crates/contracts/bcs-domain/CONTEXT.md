# bcs-domain Context

## Caller identity boundary

`caller_identity_material` owns runtime-only trusted caller envelopes shared by Service API and Caller Identity Plugin API. These data types contain no signing, exchange or provider selection behavior.

## Compatibility

Caller subject reference tags are runtime-only Rust types, not serialized wire or database values. The provider-specific tags are now `PluginLineageRef` and `ExternalSubjectRef`. Consumers and deployment-specific plugins must rebuild against these names together; no wire or database migration is required.

## A2A public management policy

`a2a_management` defines pure Card/source validation and stable management errors;
`a2a_json` rejects duplicate JSON members before a raw declaration is constructed.
They use serde and the URL parser only, without SDKs, HTTP or persistence. The
Service API re-exports the policy; HTTP adapters and application orchestration
consume it, and repositories enforce immutable source identities at writes.

Custom interface addresses are deferred to deployment binding admission; standard bindings still require URLs. The internal `Custom` binding is a selected-route representation, not an accepted wire declaration.

HTTP+JSON and JSONRPC interfaces accept both HTTP and HTTPS beyond loopback.
The shared `a2a::http_interface_address_allowed` policy rejects missing hosts,
non-HTTP schemes, userinfo, fragments and surrounding whitespace. Admission,
Card fetch and route derivation share that policy; the outbound adapter still
applies deployment network policy and DNS/IP pinning on each attempt. HTTP does
not encrypt traffic or credentials, so deployments must use trusted networks or
HTTPS. Private-network access remains configuration-controlled, not implicitly
authorized by an HTTP URL. This is an additive admission change for existing
consumers, requiring no wire, persistence or configuration-schema migration.
See the [outbound adapter boundary](../../external-clients/bcs-a2a-http/CONTEXT.md)
and the `a2a_http_address` and `conformance_card_fetch` tests.
