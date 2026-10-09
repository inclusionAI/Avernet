# Provider-scoped registration (phase 1)

## Calls

The OpenAPI prefix is `/openapi/v1/collaboration`. The legacy routes
`GET /register/token` and `POST /register` also support the same v1/v2 tokens.

1. An authenticated Human requests `GET /register/token?provider_id=<id>`.
   Provider ID is an existing Provider identifier, not its display name. The
   token binds that Provider and Human; the response contains
   `registration.token_version=2`, `provider_id`, and `allowed_modes`.
2. The client redeems it without a login principal:
   `POST /register?token=<token>&bot-name=<name>&provider_bot_ref=<stable-ref>&mode=plugin`.
   Use `mode=gateway` for downlink and optionally add URL-encoded `webhook_url`.
3. Save the returned Bot UUID/token securely. The later CLI phase starts the
   matching runtime; registration itself does not start or health-check it.

Do not place literal credentials in command history or logs. Use client query
encoding; deployment access logs must redact `token`. Both successful responses
are `Cache-Control: no-store`. Never distribute Provider-admin tokens to a local
bridge. The Bridge issuer embeds the shared downlink credential at build time;
handle the binary as containing a Provider-wide secret.

## Defaults and permissions

Omitting `provider_id` preserves v1 issuance. Omitting `mode` preserves upstream
behavior, represented by `plugin`. Provider-admin registration still defaults to
`gateway`. Both APIs, signed `allowed_modes` and `bcs_bots.connection_mode` use
the same `ProviderBotConnectionMode` enum and `plugin`/`gateway` strings.
Existing `bot-name` and `bot_name` are accepted. Legacy request/response fields,
status/envelope codes and six-hour expiry remain unchanged. Optional registration
metadata is absent for legacy calls. v1 tokens reject gateway, webhook and Provider
ref options. A new client must check the issuance metadata before redeeming a
Provider token: an old server may ignore unknown query parameters.

Provider owners/creator may issue scoped tokens. To let any authenticated Human
self-register under Poolab, an operator explicitly configures its actual ID:

```toml
[openapi_v1]
registration_self_service_provider_ids = ["poolab-provider-id"]
```

The default is empty, not all Providers. Existence, disabled state and authorization
are checked at issuance and redemption. Owner and Provider on POST come only from
verified claims. The v2 purpose is `provider_bot_registration`; its verifier is
separate from the v1 codec. Both HTTP surfaces try v1 verification first and
dispatch to scoped registration only on `UnsupportedVersion`; an invalid v2
token never falls back to ordinary registration. Token mode scope cannot be
expanded by a request.

## Legacy HTTP compatibility

- `GET /register/token` keeps the configured legacy Human authentication boundary.
  Without `provider_id`, it returns the original v1 token, six-hour expiry and
  bare `{token, expires_at, note}` object. With `provider_id`, the shared
  registration application service checks Provider authorization and issues v2;
  the bare object additionally includes `registration` scope metadata.
- `POST /register` remains anonymous, authenticating only through `token`.
  Both `bot-name` and `bot_name` remain accepted. V1 retains ordinary registration,
  its original validation/errors and ignored unknown query parameters. V2 parses
  `mode`, `provider_bot_ref` and `webhook_url`, then uses the same application/core
  authorization and persistence path as OpenAPI. Owner and Provider identities
  come exclusively from the verified token.
- Both legacy successes remain HTTP 200 with bare JSON; OpenAPI keeps its HTTP
  200/201 envelopes. V1 responses omit `registration`; V2 registration adds it
  alongside `bot_name`, `bot_uuid` and `bot_token`. Both legacy successes set
  `Cache-Control: no-store`. Scoped errors use the legacy `{error, message}` shape
  with HTTP 400/401/403/404/409/500 for the corresponding application failure.
- Tokens issued by either surface can be redeemed by either surface. Duplicate
  Provider/ref registration still returns 409 and never replays credentials.
  The bare legacy routes need no Gateway Principal; the existing configured
  Human authentication is sufficient for issuance.
- Bootstrap injects the existing `RegisterService` into legacy HTTP state for
  scoped calls. Standalone adapter construction without that service retains v1
  behavior and fails scoped calls closed with HTTP 500. There is no new runtime
  setting, database migration, Plugin API or legacy frontend change.

## Membership versus delivery

| Mode | Membership | Runtime credential | Delivery binding | Endpoint |
| --- | --- | --- | --- | --- |
| plugin (upstream) | Provider metadata in `bcs_bots` | Real Bot token | None | Not accepted |
| gateway | Provider metadata in `bcs_bots` | Real Bot token | Compatibility binding | Bot override, else Provider default |

Both modes support `static_bearer`, `provider_admin` and `agentpass` Providers.
For AgentPass, clients supply the real AgentPass agent code as `provider_bot_ref`.
Registration passes that value through Bot capabilities to persist it in the
dedicated `bcs_bots.agent_code` column, exactly as Provider-admin registration
does for either mode. The serialized/public capabilities view still omits it.
Other auth modes leave `agent_code` unset. There is no separate agent-code
parameter and registration still authenticates with the scoped register token,
not an AgentPass token. AgentPass callback verification remains unchanged.

Gateway needs an enabled downlink. Existing Provider protocol, auth mode and
credentials remain unchanged, including the shared `downlink_bcs_to_provider`
credential used for BCS-to-Provider delivery for AgentPass Providers.
Gateway registration requires at least one allowed
endpoint and an existing, enabled `downlink_bcs_to_provider` credential with a
nonblank secret before creating an identity. Missing, disabled or blank credentials
return 400 `invalid_request`; credential repository read failures propagate as 500.
Issuance and upstream registration do not read or require downlink credentials;
issuance also requires no endpoint.
An omitted override stays null so later default changes take effect. The response
distinguishes stored `webhook_url` and resolved `effective_webhook_url`.
Provider creator/owners and callers authorized for self-service registration
may supply a Bot webhook override. Delivery to that endpoint uses the existing
Provider-wide `downlink_bcs_to_provider` bearer. The Bridge build embeds the
matching Provider credential; a custom receiver must accept that same bearer.
Whoever controls the configured callback endpoint can observe this Provider-wide
bearer in the Authorization header.
Legacy Provider Bot lists continue listing delivery bindings, not new upstream
memberships. A membership-list API is not part of this phase.

## Retry and lifecycle

The immutable key is `(env, provider_id, provider_bot_ref)`. Ref/Provider IDs use
1–128 ASCII letters, digits, underscore, hyphen, dot or colon. A duplicate ref
returns 409 even for identical input; it does not replay an ID/token. A valid
token can create multiple distinct refs. A ref already used by the legacy Provider
API is not adopted. Use existing management APIs for endpoint changes or delivery
switching; registration does not transfer Provider ownership or change mode.

There is no registration journal in the runtime flow. SQL atomically inserts the
Bot with Provider metadata and, for gateway only, its compatibility binding.
Uniqueness includes retained soft-deleted Bots. Human/owner-edge writes follow;
errors propagate but these writes do not form one cross-store transaction.
A failure before the Bot transaction commits can be retried. A failure or lost
response after commit needs operator reconciliation; retry returns 409 and cannot
recover the original runtime credential. Do not blindly change refs on an ambiguous
failure. This explicitly replaces the earlier journal-based resume contract.

Both modes persist Provider affiliation in `bcs_bots`; only gateway creates or
updates `bcs_provider_bot_bindings`. Gateway disabled state mirrors Bot soft
deletion, not temporary WS disconnection. The configurable delivery read source
does not change membership or write policy.

## Deployment and verification

- Bot Provider metadata adds only identity/ref, mode and nullable webhook. Bot
  and binding lifecycle timestamps remain in their existing locations; no
  Provider-specific Bot timestamps or metadata-version checks are introduced.
  Provider updates do not lock or rewrite affiliated Bots. The existing gateway
  binding lock is unchanged.
- Apply additive MySQL `029_bot_provider_storage.sql` before new server code;
  SQLite applies version 030 on bootstrap. Earlier upstream migrations stay frozen.
  This PR has never been deployed; its unused registration-table draft and
  compatibility reader are removed, not retained or followed by a drop migration.
- Follow the [read-source rollout prerequisites](../../docs/provider-bot-storage-migration.md).
  Schema expansion alone does not backfill membership. Historical correction is
  a separate reviewed work order; no dedicated backfill DB method or command is
  shipped. Legacy binding reads remain the default; switch only after correction
  and validation have completed for every target environment.
- Memory Provider metadata is process-local. Durable restart guarantees require
  SQLite/MySQL; historical correction concerns durable SQL storage only.
- Rollback of the read-source setting preserves dual writes. An old binary is
  not automatically safe after new upstream memberships or lifecycle changes.
- Contract propagation: domain v2 codec, Service API DTO/core/repo traits, application
  facade, HTTP adapter, memory/SQL stores, bootstrap/config and OpenAPI schemas.
  No new Plugin API or bridge/CLI changes are included.

Tests cover invalid token rejection, scope/auth failures, wire compatibility,
memory/SQLite metadata and dual-write conformance, endpoint precedence, real owner
edges, duplicate rejection and prevention of token rotation/deleted-Bot resurrection.
The registration matrix covers both legacy and OpenAPI surfaces, all three
Provider auth modes and both delivery read sources, including persisted AgentPass
codes for plugin and gateway, cross-surface token redemption and v1 wire compatibility.
MySQL query/schema checks do not substitute for a live MySQL deployment test.
