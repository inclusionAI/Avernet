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

OpenAPI Caller context, MCP/CLI call-type writes and acli operations resolve
their `OwnerIdDep` locator through `openapi_v1.bot_address` before passing an
entity to Core. A legacy `owner_id` may identify the current Owner or the
immutable entity; an explicit `entity_id` is an exact address. Core entity
arguments remain strict, and `actor_id` remains the authenticated caller.
This preserves access after handoff without granting the former Owner access.

Explicit OpenAPI friend IAM preparation carries a server-verified
`friend_user_id` and owned `session_id`. The adapter verifies friendship, resolves
the effective chat stage, and loads the actual entity address from the exact
Bot/owner pair. For Caller-mode services, installation is restricted to that
subject's existing Caller instance, including when the subject is also the Bot
owner. Owner inspection of another Human's conversation does not authorize
credential installation there. The optional internal friend argument is not an
authorization flag clients can supply directly to the application service.
See `specs/2026-10-26-online-conversation-stage/spec.md` for the additive contract.

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

### Admin-authorized final Owner transition

The MCP and CLI update services retain owner-only editing, active service Bot,
engine and collaboration-lock checks. A configured `admin.super_admin` actor
may additionally remove the final effective Caller override. The application
service resolves membership from trusted configuration only for an Owner
request; the repository receives a default-deny internal authorization and
checks the aggregate while holding the Bot row lock. No HTTP authorization
flag is accepted. Existing Passport synchronization and conditional
compensation still apply. A successful update affects the current identity
flow immediately and does not defer identity activation until publication.
The `caller_owner_transition_authorized` diagnostic records the actor, Bot,
resource kind/code, engine and environment without credentials.
