# Caller identity

## Context Boundary

```yaml
purpose: Resolve and install authenticated Caller identity without changing Owner policy.
provides:
  - CallerIdentityService
  - CallerIamTokenService
  - CallerSessionValidator
  - ScheduledCallerRefreshProtocol
  - NoopScheduledCallerRefresh
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
owner. Teclaw shared-runtime refresh is the narrow exception described
below: it uses the supplied nonblank session key without checking the Backend
owned-session ledger.
Owner inspection of another Human's conversation does not authorize
credential installation there. The optional internal friend argument is not an
authorization flag clients can supply directly to the application service.
See `specs/2026-10-26-online-conversation-stage/spec.md` for the additive contract.

IAM refresh accepts `session_key` as the canonical optional query parameter and
retains `sessionKey` as a compatibility alias. The Bot-scoped OpenAPI also
retains its original `session_id` spelling. Friend IAM preparation always uses
the server-verified owned `session_id`; an untrusted session-key alias cannot
replace it. The application passes the selected value as `session_key` through
the Caller contracts and runtime updater. Existing non-TeClaw callers need no
session parameter.

TeClaw uses the existing `service` Bot eligibility and the trusted binding's
`device_provider`. The existing `CallerSessionValidator.validate` requires nonempty session context
for TeClaw before token exchange and direct identity installation. It keeps the
existing authorized binding resolution and Bot permissions; there is no runtime
session lookup or session ownership filtering in that legacy method. All existing eligible targets keep the
existing per-target update success aggregation.

### Shared Teclaw refresh

The server-resolved `CallerIamTokenContext.shared_teclaw_runtime` capability is
true only for active Caller-mode Teclaw services. IAM refresh across draft,
verify and online uses the existing runtime resolver's AUTO policy to select the
requested stage's shared binding, matching chat runtime selection without querying
or creating an independent Caller instance. The refresh branch imposes no online
stage or SERVICE_ONLINE source restriction. Other engines and temporary test
exchange retain their existing Owner/lock and target-selection policies.

Before signing,
`validate_owned_shared_session` confirms an ACTIVE Teclaw binding, a nonblank
session key. Missing keys are invalid requests. This shared refresh branch does
not query the Backend-owned session index: absent, deleted or differently scoped
ledger rows do not reject refresh. It does not establish ownership of the supplied
session key; existing Caller authentication and target authorization remain in
force. The method retains its existing name for internal call-site compatibility.
No runtime session lookup or ownership record creation is performed.
After signing, the application installs into the initially selected binding;
it does not resolve the target or validate session context a second time. Shared
refresh does not enforce a signing-time publication fence. Required signing,
initial-target and installation errors remain errors.

The `caller_shared_target_selected`, `caller_shared_target_rejected`,
`caller_shared_target_refresh_succeeded`, and
`caller_shared_session_validated/rejected` events identify the selected Bot,
Owner, Caller, stage, binding/source and business session key where applicable.
They never include token values or raw upstream error bodies. Public and
enterprise composition use the existing binding and runtime-resolution ports;
the validator no longer consumes the ExpertChat repository. No HTTP route,
persistence schema or provider protocol is added.

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

### Admin-authorized call-type editing

Only the MCP and CLI call-type mutation services allow the actual Bot Owner or
an actor in configured `admin.super_admin` to edit. Other callers, including
operator-only users and collaborators, remain denied; the shared owner-only
asset guards and read policies are unchanged. Role membership is resolved from
trusted configuration, with no HTTP authorization flag. The final effective
Caller-to-Owner transition still requires a super admin.

The authenticated actor remains the modifier and collaboration-lock holder.
Active resource discovery and Passport queries/updates use the true current
Owner, while preliminary checks, transactional writes and conditional
compensation use the target lock key `bot_id:entity_id`. This immutable entity
locator remains stable after an ownership handoff. Another holder's lock,
missing/stale epochs and transaction-time lock races are still rejected.
Existing active-service/engine checks and Passport synchronization/compensation
remain in place; successful identity updates take effect immediately.

The `caller_call_type_edit_authorized` / `caller_call_type_edit_denied` events
record actor, Owner, entity, Bot, resource kind/code and the permission reason.
The existing `caller_owner_transition_authorized` event records the final
aggregate transition. Neither event logs credentials.

## Stable edit-lock coordinates

Caller MCP/CLI edits, lock-epoch checks, compensation and draft editability use
`bot_id:entity_id` from the resolved Bot, matching CollaboratorLockService.
Owner authorization, the lock holder and credential subjects remain personnel
identities. `CallerIamTokenContext` carries the required resolved `entity_id`
alongside the current `owner_id`; IAM refresh uses the entity for lock lookup and
the Owner for permission and runtime requests. No browser request or response
schema changes. Consumers constructing this internal context must supply entity;
the in-repository producer and tests are updated together. A missing entity must
not silently bypass another user's lock by querying a new Owner-qualified key.

`ScheduledCallerRefreshProtocol` v1 is an additive Service API invoked after a
service Bot's Owner Passport runtime update succeeds and
`bot_service.SCHEDULED_CALLER_REFRESH_ENABLED` is enabled. This code-level rollout
gate ships `True`; explicitly disabled callbacks
keep the original Owner result without invoking Caller refresh. It does not gate
interactive ASC provisioning. The composition root binds
a no-op in public profiles and an enterprise service when ASC is supported.
The explicit Bot entity is stable across Owner handoff; implementations resolve
each actual Caller and sandbox independently. Caller refresh is best effort:
preparation or refresh errors log safe metadata and preserve the successful Owner
callback result. Original Owner update errors still propagate. Caller failures
do not trigger Owner notification retries. This contract adds no scheduler
and makes no continuous-validity guarantee between Owner renewal callbacks.

For direct legacy `BotService` construction, omission of the optional refresh
port means no scheduled-credential capability is installed. Production DI always
provides an explicit no-op or enterprise implementation; errors from an installed
implementation are never treated as an unconfigured capability.


## Independent stored-ASC refresh

`ScheduledCallerRefreshProtocol.refresh_for_actor` authorizes the current Bot
owner and refreshes eligible runtimes independently of the Owner callback flag.
`POST /openapi/v1/bots/{bot_id}/caller-token/refresh` uses the existing owner
permission and addressed application-grant checks. Implementations derive the
current owner, environment, Caller and physical device from trusted records;
the request cannot choose a credential subject or sandbox. Targets must be alive
and have at least one enabled Cron task. Results report each binding's outcome,
safe reason and installed expiry, plus batch counts and status; no token is
returned. Partial failures remain explicit and do not change Owner callback
behavior. Public profiles report HTTP 501 for this unsupported capability.
This endpoint adds no scheduler and requires a supported implementation to be
bound by the host composition root.
