# Caller identity

## Context Boundary

```yaml
purpose: Resolve and install authenticated Caller identity without changing Owner policy.
provides:
  - CallerIdentityService
  - CallerIamTokenService
  - CallerSessionAuthorizer
consumes:
  - CallerTokenProviderProtocol
  - CallerRuntimeUpdaterProtocol
  - DeviceAdapterTransport
internal_dependencies:
  - agentclaw.community.core
  - agentclaw.community.plugin_api
  - agentclaw.community.log
  - agentclaw.community.utils
```

### Change impact

IAM refresh accepts an optional `sessionKey` query parameter. The application
passes its original value as `session_key` through the Caller contracts and
runtime updater. Existing non-TeClaw callers need no session parameter.

TeClaw uses the existing `service` Bot eligibility and the trusted binding's
`device_provider`; it does not introduce a new business Bot type. Before token
exchange, `CallerSessionAuthorizer` resolves the fixed binding through
`DeviceContextResolver` and reads `/api/sessions` with the authenticated Caller,
Bot and original session filters. The response must contain the exact session
ID, Caller user ID and Bot agent ID. A successful empty response grants no
permission. No session is created or registered by this check, so both regular
service draft/verify sessions and Expert Chat sessions use the runtime's existing
session authority rather than assuming all sessions are in the Expert Chat ledger.

For multiple eligible bindings, unmatched TeClaw sessions are excluded. At least
one TeClaw match is required if any TeClaw session was denied; a non-TeClaw
success cannot hide that denial. Missing/blank keys and transport failures abort
before exchange. Direct identity installation repeats the same required check.
The original per-target update success aggregation remains in effect afterwards.

The BaaS Caller overlay uses `mode=append` and, only for TeClaw, `session_key`.
The HTTP client encodes the query once. It retains `set`, `x-caller-token`, the
existing domains and numeric template suffix; it never replaces base rules.
Community/singlebox unsupported Caller implementations accept the optional
parameter without gaining a corporate dependency.

Boundary events use `caller_iam_request_received`, `caller_iam_response_succeeded`,
`caller_iam_response_failed`, `caller_session_lookup_started/succeeded/failed`,
and `caller_outbound_append_started/succeeded/failed`. Fields include system,
direction, operation ID, method/route, business context, duration and sanitized
responses. Token/session/credential fields are recursively redacted, rule values
inherit their header's sensitivity, and known credentials inside error messages
are masked. Upstream exception chains are suppressed at the external boundary.

These checks verify Backend behavior. Actual TeClaw append precedence and
Caller identity require a separate deployed runtime integration test.
