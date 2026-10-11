# Caller identity

## Context Boundary

```yaml
purpose: Resolve and install authenticated Caller identity without changing Owner policy.
provides:
  - CallerIdentityService
  - CallerIamTokenService
  - CallerSessionValidator
consumes:
  - CallerTokenProviderProtocol
  - CallerRuntimeUpdaterProtocol
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
`device_provider`. `CallerSessionValidator` requires nonempty session context
for TeClaw before token exchange and direct identity installation. It keeps the
existing authorized binding resolution and Bot permissions; there is no runtime
session lookup or session ownership filtering. All eligible targets keep the
existing per-target update success aggregation.

The BaaS Caller overlay uses `mode=append` and, only for TeClaw, `session_key`.
The HTTP client encodes the query once. It retains `set`, `x-caller-token`, the
existing domains and numeric template suffix; it never replaces base rules.
Community/singlebox unsupported Caller implementations accept the optional
parameter without gaining a corporate dependency.

Boundary events use `caller_iam_request_received`, `caller_iam_response_succeeded`,
`caller_iam_response_failed`, `caller_session_key_validated/rejected`,
and `caller_outbound_append_started/succeeded/failed`. Logs include plaintext
session keys for diagnosis. IAM token and outbound Caller token values are masked
at their known fields; there is no custom recursive boundary logging mechanism.

These checks verify Backend behavior. Actual TeClaw append precedence and
Caller identity require a separate deployed runtime integration test.
