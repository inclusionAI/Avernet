# OpenAPI V1 Session Collection Design

**Date:** 2026-08-13
**Status:** Approved

## Problem

BCS exposes session collection through two legacy HTTP operations:

- `POST /sessions/{sid}/collect`
- `DELETE /sessions/{sid}/collect`

The operations persist collection state per Session participant, but they are
not available through the versioned collaboration OpenAPI surface. OpenAPI
callers therefore cannot add or remove a Session collection while remaining
inside the Gateway Principal and V1 response-envelope boundary.

The first OpenAPI version of this capability intentionally supports only a
Human caller acting for one of that Human's Bots. Direct Bot callers and Human
collection under the Human Actor ID are out of scope for this change.

## Goals

- Add collect and uncollect operations beneath the existing collaboration
  Session namespace.
- Require an authenticated Human Principal.
- Require the Human to identify the Bot for which collection state changes.
- Verify that the Bot belongs to the authenticated Human and participates in
  the Session.
- Reuse the existing collection persistence and idempotency semantics.
- Preserve the legacy request placement and non-participant visibility
  behavior.
- Keep the versioned HTTP adapter dependent only on the V1 application API.

## Non-goals

- Direct collection by a Bot Principal.
- Collection under a Human Actor ID.
- Adding `collect` or `uncollect` commands to `bcs-cli`.
- Adding an OpenAPI `collected` Session-list filter or collection fields to
  Session list/detail projections.
- Changing collection persistence, database schema, or migrations.
- Removing or changing the legacy endpoints.

## Alternatives Considered

### Extend the existing V1 `SessionService` (selected)

The V1 Session facade authorizes the Human and target Bot, then delegates the
mutation to the existing `SessionManagementService`. This follows the same
facade pattern already used by V1 Session lifecycle operations, keeps HTTP
types out of application code, and requires no additional bootstrap state.

### Add a dedicated `SessionCollectionService`

A separate service would make the narrow capability explicit, but it would add
another trait object, state field, and composition-root binding for two small
operations. The existing `SessionService` already owns Session-scoped V1 use
cases, so a separate service is unnecessary.

### Mount the legacy handlers below the OpenAPI prefix

This would couple `bcs-api-http` to `bcs-http` and its legacy state and response
types. It violates the enforced adapter boundary and would bypass the V1
application contract and response envelope.

## Public HTTP Contract

### Collect

```http
POST /openapi/v1/collaboration/sessions/{session_id}/collect
Content-Type: application/json

{
  "participant": "bot-uuid"
}
```

`participant` is required and identifies the Bot whose collection state is
changed. The name and body placement intentionally match the legacy Human
caller request.

### Uncollect

```http
DELETE /openapi/v1/collaboration/sessions/{session_id}/collect?participant=bot-uuid
```

`participant` is required. It remains a query parameter because DELETE request
bodies are not reliable across all proxies and the legacy endpoint already
uses this shape.

### Security

Both operations declare the existing Human collaboration boundary:

```yaml
x-avernet-security:
  user: required
  app: required
```

They do not opt in to `human_or_owned_bot`; the default V1 Session identity
policy remains Human-only. The request cannot supply a Human identity or raw
credential.

### Success response

Both operations return HTTP 200 and the standard V1 envelope. The result makes
the affected Bot and final state explicit:

```json
{
  "code": 20000,
  "message": "OK",
  "data": {
    "session_id": "group-1:abcd1234",
    "participant": "bot-uuid",
    "collected": true
  },
  "request_id": "request-id"
}
```

Uncollect returns the same shape with `collected: false`.

Collect and uncollect are idempotent. Repeating either operation returns 200
and the same final state. A later collect after uncollect records a fresh
collection timestamp according to the existing store contract.

### Errors

| Condition | HTTP | Error code | Notes |
| --- | --- | --- | --- |
| Gateway Principal missing or invalid | 401 | `unauthenticated` | Existing verification boundary |
| `participant` missing or malformed | 400 | `invalid_request` | Rejected before mutation |
| Authenticated caller has no Human identity | 403 | `forbidden` | App-only and Bot-only callers are not admitted |
| Target Bot is not owned by the Human | 403 | `forbidden` | Ownership is server-verified |
| Session does not exist | 404 | `session_not_found` | Stable V1 error envelope |
| Target Bot is not a Session participant | 404 | `session_not_found` | Preserves legacy behavior and hides membership topology |
| Persistence failure | 500 | `internal_error` | No mutation success is reported when the write fails |

## Application Contract

Extend `bcs_service_api::application::v1::SessionService` with two explicit
commands and one result:

```text
CollectSession {
  caller: AuthenticatedCaller,
  session_id: String,
  participant: String,
}

UncollectSession {
  caller: AuthenticatedCaller,
  session_id: String,
  participant: String,
}

SessionCollectionResult {
  session_id: String,
  participant: String,
  collected: bool,
}
```

The commands carry an already verified caller rather than cookies, bearer
tokens, or request headers. Separate methods preserve clear use-case intent:

```text
SessionService::collect(CollectSession)
SessionService::uncollect(UncollectSession)
```

## Authorization and Data Flow

```text
Gateway Principal verification
  -> OpenAPI Session route
  -> V1 SessionService facade
     1. require Human Principal
     2. load target Bot
     3. verify Bot actor kind
     4. verify Bot.created_by == Human user id
     5. load Session
     6. verify target Bot is a Bot participant
     7. call SessionManagementService::collect/uncollect
  -> SessionRepoPort
  -> memory or SQL Session store
```

The HTTP adapter performs request parsing and envelope translation only. The V1
facade owns identity, ownership, and resource authorization. It delegates the
actual mutation to the existing application service rather than accessing
`SessionRepoPort` from the delivery adapter.

Ownership should use a targeted Bot lookup and compare the persisted
`created_by` value with the authenticated User ID. This is semantically
equivalent to the legacy Human path's `list_bots_by_creator` check without
enumerating every Bot owned by the Human.

The participant check must match both identifier and `ActorKind::Bot`. This
prevents a malformed or legacy Human participant row from being treated as the
target Bot solely because an identifier string happens to match.

## Compatibility

The change is additive to the OpenAPI V1 contract. The two legacy routes remain
mounted and retain their current Bot-token and Human-cookie behavior.

No persistence change is required. Both memory and SQL stores already keep
collection state in the Session participant record and implement idempotent
`collect` and `uncollect` operations.

The checked-in Gateway BCN OpenAPI artifact must be regenerated. The approved
operation inventory and tests that currently assert 41 operations must be
updated to 43.

## Testing Strategy

### OpenAPI contract tests

- The exact operation inventory contains POST and DELETE collection operations.
- POST requires the strict `{participant}` JSON body.
- DELETE requires the `participant` query parameter.
- Both operations declare required User and App identities.
- Success and error responses use V1 envelopes and documented error codes.

### Service API contract tests

- Collection commands carry `AuthenticatedCaller`, Session ID, and target Bot.
- Commands contain no raw credentials or transport types.
- `SessionService` remains object-safe after adding both methods.

### V1 application facade tests

- An authenticated Human can collect for an owned participant Bot.
- The same Human can uncollect for that Bot.
- Repeated collect and uncollect calls are idempotent.
- A non-owned Bot is rejected with 403 before persistence is called.
- An owned Bot that is not a Session participant is hidden behind 404.
- A missing Session returns `session_not_found`.
- Bot-only and App-only callers are rejected.
- Persistence failures propagate as internal errors.

### HTTP adapter tests

- POST body and DELETE query are translated into the correct V1 commands.
- Missing or unknown fields return `invalid_request`.
- Success responses contain `code`, `message`, `data`, and `request_id`.
- Application errors map to their declared HTTP statuses.

### Publication and integration tests

- Regenerate and verify the Gateway `bcn.openapi.json` snapshot.
- Update exact operation-count assertions from 41 to 43.
- Verify Gateway route security resolves both operations to required User and
  App identities.
- Run the existing legacy session-collection contract and E2E tests unchanged.
