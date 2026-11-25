# Caller identity

## Context Boundary

```yaml
purpose: Resolve and install authenticated Caller identity without changing Owner policy.
provides:
  - CallerExpiryCorrectionService
  - ExpiryAwareCallerRuntimeUpdater
  - CallerIdentityService
  - CallerIamTokenService
  - CallerSessionValidator
  - ScheduledCallerRefreshProtocol
  - NoopScheduledCallerRefresh
consumes:
  - CallerTokenProviderProtocol
  - DingTalkCallerTokenProvider
  - CallerRuntimeUpdaterProtocol
internal_dependencies:
  - agentclaw.community.core
  - agentclaw.community.plugin_api
  - agentclaw.community.log
  - agentclaw.community.utils
```

### Change impact

For IAM credentials, `TokenExchangeOrchestrator` runs Caller refresh and the
independent outbound token plugin pipeline concurrently, awaiting both before
returning the original Caller outcome or raising its original error. Plugin and
context failures remain isolated from Caller; non-IAM credentials skip plugins.
Each branch retains its own authenticated subject and target checks. Caller
targets and individual plugins retain their existing internal order. Request
cancellation cancels and joins coroutine branches; already-running thread-backed
I/O may still finish and is not rolled back. Synchronous context reads and plugin
appends run off the event loop. `token_pipelines_started/finished` identify the
Bot, stage and total elapsed time without logging credentials.

The existing IAM refresh endpoint accepts `token_type=dingtalk` for an opaque
platform DeviceToken. It adds no caller or owner query parameters. This branch
first authenticates the existing application signature, resolves the owner using
the existing Bot locator, and invokes the optional
`DingTalkCallerTokenProvider.exchange_dingtalk` capability. Only the verified
upstream subject selects the existing Caller instance. Source credentials and
query IDs are never decoded or used to infer caller identity. The service rejects
invalid subject/token/expiry values and checks the exact binding before installing.
The default `iam` flow and its provider contract remain unchanged.

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

`ScheduledCallerRefreshProtocol` is an explicit manual refresh capability with
an enterprise implementation and a public no-op. Independent enterprise hourly
scheduling uses the installed-expiry ledger; Owner Passport hot-update callbacks
no longer invoke Caller refresh. Their original result and error semantics remain
unchanged. The optional legacy BotService constructor port remains accepted for
enterprise DI compatibility. Candidate enumeration uses an inclusive UTC cutoff,
unknown expiry bootstrap, serving status and environment-scoped keyset pages;
consumers validate actual engine, owner and physical target before exchange.

### Installed Caller expiry

`ExpiryAwareCallerRuntimeUpdater` coordinates dedicated Caller-instance installs
with the enterprise ASC refresh consumer through
`ExpertChatInstanceRepository.caller_token_update`. The nullable UTC
`ac_expert_chat_instance.caller_token_expires_at` records a successfully installed
credential, not merely a successful token exchange. Under a real transaction row
lock, a later or equal installed expiry skips both outbound replacement and the
expiry write. A skipped installation remains a successful interactive IAM result.
Shared service and test targets retain their existing installation behavior.

After an ExpertChat instance publish reaches SUCCESS with an IAM credential,
its internal identity exchange sets `force_runtime_refresh=True`. The expiry
wrapper consumes this option and reinstalls the credential under the same scoped
row lock even when its expiry is shorter or equal. Only a successful installation
records the actual newly installed expiry; a failure leaves the previous ledger
unchanged. Normal chat and scheduled refresh retain their increasing-expiry
policy. Already-ready connection reuse does not force installation, and missing
IAM credentials retain the existing skip behavior. Teclaw keeps its shared-runtime
bypass without reading or writing this expiry ledger. This internal recovery flag
is not a new HTTP parameter and is not forwarded to outbound providers.


The transaction stays open across the bounded outbound call and expiry write.
A failed installation or database commit propagates; it cannot advance the
recorded expiry. External installation and SQL commit are not an atomic pair. If the install
succeeds but the commit fails, the ledger retains its previous expiry. Ordinary
monotonic installation leaves an earlier/unknown value and can be retried later;
forced installation of a shorter credential can leave a later ledger value.
Such a failed recovery requires explicitly retrying forced initialization recovery
and checking the actual installed state; an already-ready connection fast path
does not reinstall credentials. Container replacement, a new instance
publish or a non-serving lifecycle transition clears the stored expiry. Deploy
`core/expert_chat/sql/2026_11_13_caller_token_expiry.sql` before this application
change on an existing MySQL/OceanBase store. Existing records remain NULL until
an installation succeeds. The enterprise scheduler consumes this ledger independently of Owner renewal.

### Teclaw scope
Teclaw Caller sessions and draft/pre/production stages share a runtime. This iteration excludes both Teclaw engines and Teclaw-provider bindings from Caller expiry coordination and ASC scheduled refresh: Teclaw has no scheduled-task support yet. Interactive IAM refresh keeps its existing runtime/outbound update and does not read or write `caller_token_expires_at`. Engine lookup also excludes legacy `baas` bindings for a Teclaw Bot; other providers retain the expiry policy.

For `GET /api/v1/token/iam?token_type=dingtalk`, the DeviceToken is supplied
in the `IAM_TOKEN` cookie. This branch does not require a BaaS
`X-Avernet-Principal` JWT and does not use that header to select the Caller.
The exchange provider validates the source credential and returns the Caller
subject; the service derives the Owner from the Bot context, requires a valid
CallerToken, and resolves and rechecks the exact Caller-instance binding before
installation. Missing source credentials or failed exchange/target checks still
reject the request. The BaaS app-caller-connection endpoint retains its separate
service-principal authentication contract.

The token exchange orchestrator accepts the optional `token_type` selector
and forwards non-default sources to the Caller service. The default `iam`
path omits that optional keyword when calling older IAM-only implementations.
HTTP regression coverage must include the real orchestrator and Caller service;
replacing the entire injected boundary can hide missing selector propagation.

### Administrative expiry correction

`PATCH /openapi/v1/bots/{bot_id}/caller-instances/{caller_user_id}/token-expiry`
is non-delegable and limited to a verified human in configured `admin.super_admin`.
The required `user_id` query must match that actor; the path's `caller_user_id`
is the independently addressed target. A request names the exact immutable
`entity_id`, current `binding_id`, aware `expires_at`, an explicitly supplied
nullable `expected_expires_at`, and bounded audit `reason`. Missing expected value
and naive dates are invalid. Past expiry is allowed to exercise scheduled refresh.
Only an existing serving dedicated Caller of a non-Teclaw Caller-mode service is
eligible. This endpoint never signs or installs a token, provisions a container,
or changes cron jobs. Ordinary expiry installation retains its monotonic future-expiry contract;
explicit initialization recovery records the actual installed future expiry.

The repository shares IAM/ASC's exact row lock, refreshes the locked snapshot,
and compares the expected UTC expiry before changing it. A concurrent refresh,
replaced binding, missing instance or non-serving status rejects correction.
The response includes instance id, binding and previous/new UTC expiry. The
`caller_expiry_correction_requested/succeeded/failed` HTTP events and
`caller_expiry_corrected` audit event record safe actor/target/expiry coordinates;
credentials, request headers and raw infrastructure exception messages are omitted.

### Legacy IAM friend selection

`GET /api/v1/token/iam` accepts optional `f_user_id` and `device_uuid` query
parameters. An absent or empty `f_user_id` preserves the original Caller refresh
path. A friend selector is forwarded directly to the existing Caller service;
Caller authentication, subject equality, current binding and session validation
remain in their existing domains. The orchestrator adds no friendship directory
query, expert-session preflight or stage rewrite. OpenAPI retains its own access
checks. `sessionKey` remains an alias for `session_key`. `device_uuid` is carried
in the request snapshot solely for JIT replica selection; empty values mean
omission. DingTalk keeps its existing credential and target-selection contract.
