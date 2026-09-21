# Provider-scoped registration (phase 1)

## Calls

The public prefix is `/openapi/v1/collaboration`.

1. An authenticated Human requests `GET /register/token?provider_id=<id>`.
   Provider ID is an existing Provider identifier, not its display name. The
   token binds that Provider and Human; the response contains
   `registration.token_version=2`, `provider_id`, and `allowed_modes`.
2. The client redeems it without a login principal:
   `POST /register?token=<token>&bot-name=<name>&provider_bot_ref=<stable-ref>&mode=upstream`.
   Use `mode=gateway` for downlink and optionally add URL-encoded `webhook_url`.
3. Save the returned Bot UUID/token securely. The later CLI phase starts the
   matching runtime; registration itself does not start or health-check it.

Do not place literal credentials in command history or logs. Use client query
encoding; deployment access logs must redact `token`. Both successful responses
are `Cache-Control: no-store`. Never distribute Provider-admin or shared downlink
tokens to a local bridge.

## Defaults and permissions

Omitting `provider_id` preserves v1 issuance. Omitting `mode` preserves upstream.
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
verified claims. The v2 purpose is `provider_bot_registration`; legacy token
verification rejects this version. Token mode scope cannot be expanded by a request.

## Membership versus delivery

| Mode | Membership | Runtime credential | Delivery binding | Endpoint |
| --- | --- | --- | --- | --- |
| upstream | Provider metadata in `bcs_bots` | Real Bot token | None | Not accepted |
| gateway | Provider metadata in `bcs_bots` | Real Bot token | Compatibility binding | Bot override, else Provider default |

Gateway needs an enabled downlink using `static_bearer` or `provider_admin` auth;
AgentPass Providers are not silently converted. Existing Provider protocol and
credentials remain unchanged. Gateway registration requires at least one allowed
endpoint and an existing, enabled `downlink_bcs_to_provider` credential with a
nonblank secret before creating an identity. Missing, disabled or blank credentials
return 400 `invalid_request`; credential repository read failures propagate as 500.
Issuance and upstream registration do not read or require downlink credentials;
issuance also requires no endpoint.
An omitted override stays null so later default changes take effect. The response
distinguishes stored `webhook_url` and resolved `effective_webhook_url`.
Only the Provider creator/owners may supply a Bot webhook override: the existing
delivery protocol sends a Provider-wide bearer to that endpoint. Self-service
callers may register upstream or use the configured Provider default for gateway,
but cannot direct that bearer to their own endpoint (403 before creation).
Supporting independent self-service callbacks requires separately designed
Bot-scoped downlink credentials and is not included in this phase.
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

- Apply additive MySQL `029_bot_provider_storage.sql` before new server code;
  SQLite applies version 030 on bootstrap. Earlier upstream migrations stay frozen.
  This PR has never been deployed; its unused registration-table draft and
  compatibility reader are removed, not retained or followed by a drop migration.
- Follow the [fenced migration and read-source rollout](../../docs/provider-bot-storage-migration.md).
  Schema expansion alone does not backfill membership. Legacy binding reads remain
  the default; switch only after every environment passes the audit.
- Memory Provider metadata is process-local. Durable restart guarantees require
  SQLite/MySQL; the backfill utility applies only to durable SQL storage.
- Rollback of the read-source setting preserves dual writes. An old binary is
  not automatically safe after new upstream memberships or lifecycle changes.
- Contract propagation: domain v2 codec, Service API DTO/core/repo traits, application
  facade, HTTP adapter, memory/SQL stores, bootstrap/config and OpenAPI schemas.
  No new Plugin API or bridge/CLI changes are included.

Tests cover legacy token rejection, scope/auth failures, wire compatibility,
memory/SQLite metadata and dual-write conformance, endpoint precedence, real owner
edges, duplicate rejection and prevention of token rotation/deleted-Bot resurrection.
MySQL query/schema checks do not substitute for a live MySQL deployment test.
