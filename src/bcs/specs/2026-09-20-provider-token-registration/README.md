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
| upstream | Durable registration record | Real Bot token | None | Not accepted |
| gateway | Durable registration record | Real Bot token | Provider binding | Bot override, else Provider default |

Gateway needs an enabled downlink using `static_bearer` or `provider_admin` auth;
AgentPass Providers are not silently converted. Existing Provider protocol and
credentials remain unchanged. Gateway registration requires at least one allowed
endpoint and an existing, enabled `downlink_bcs_to_provider` credential with a
nonblank secret before reserving an identity. Missing, disabled or blank credentials
return 400 `invalid_request`; credential repository read failures propagate as 500.
Issuance and upstream registration do not read or require downlink credentials;
issuance also requires no endpoint.
An omitted override stays null so later default changes take effect. The response
distinguishes stored `webhook_url` and resolved `effective_webhook_url`.
Only the Provider creator/owners may supply a Bot webhook override: the existing
delivery protocol sends a Provider-wide bearer to that endpoint. Self-service
callers may register upstream or use the configured Provider default for gateway,
but cannot direct that bearer to their own endpoint (403 before reservation).
Supporting independent self-service callbacks requires separately designed
Bot-scoped downlink credentials and is not included in this phase.
Legacy Provider Bot lists continue listing delivery bindings, not new upstream
memberships. A membership-list API is not part of this phase.

## Retry and lifecycle

The immutable key is `(env, provider_id, provider_bot_ref)`. Ref/Provider IDs use
1–128 ASCII letters, digits, underscore, hyphen, dot or colon. Retries with the same
owner, mode, validated Bot name and override return the same ID/token. Different
inputs yield 409; use existing management APIs for subsequent changes. A ref
already used by the legacy Provider API is not adopted.

Reservation persists before Bot creation. Bot creation uses an atomic insert-only
operation that treats tombstones as existing identities, never read-then-upsert.
Bot registration, Human actor/owner edges,
and optional binding are completed before the reservation is marked complete.
Any failure returns an error; retry resumes the same identity. This is a resumable
multi-step operation, not a cross-store atomic transaction. Pending rows must not
be purged automatically: a partial registration may already have a Bot. Completed
retries do not recreate deleted Bots, restore removed/disabled bindings, or roll
back rotated credentials. Concurrent claims cannot reassign a winning reservation.

## Deployment and verification

- Apply additive MySQL migration `029_provider_registrations.sql` before deploying
  new server code. SQLite automatically applies migration 030 on bootstrap.
- The approved rebase renumbers only this unreleased PR's original MySQL 028 /
  SQLite 029 migrations; upstream Fixed Loop history stays unchanged. Earlier
  registration-draft databases need a fresh disposable database or an explicitly
  reviewed retained-data reconciliation, never rewritten migration records.
- The ledger contains a runtime credential like the existing Bot store. Restrict
  DB access and backups accordingly; it is never serialized as an API response.
- Memory mode is process-local, matching existing memory Provider stores. Durable
  restart/retry guarantees require SQLite/MySQL.
- Rollback leaves the additive table in place. Older servers reject v2 tokens;
  their original v1 calls remain supported. No legacy data backfill is required.
- Contract propagation: domain v2 codec, Service API DTO/core/repo traits, application
  facade, HTTP adapter, memory/SQL stores, bootstrap/config and OpenAPI schemas.
  No new Plugin API or bridge/CLI changes are included.

Tests cover legacy token rejection, scope/auth failures, wire compatibility,
memory/SQLite reservation conformance, endpoint precedence, real owner edges,
completion failure/retry and prevention of token rotation/deleted-Bot resurrection.
MySQL query/schema checks do not substitute for a live MySQL deployment test.
