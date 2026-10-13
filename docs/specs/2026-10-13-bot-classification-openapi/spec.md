# Bot classification through Gateway

PR: [#2662](https://github.com/inclusionAI/Avernet/pull/2662).

Status: implemented; authorized by the user on 2026-10-13.
Owners: Backend, Gateway, Frontend next-gen. No ADR change.

## Problem

Next-gen group creation must classify prospective service Bot participants before
establishing an owner/editor relationship. The legacy `/api/bots` address does
not use next-gen's Gateway proxy or Gateway's public Bots domain.

## Contract

`GET /openapi/v1/bots/{bot_id}/classification?entity_id=<optional entity suffix>`
returns the standard public envelope:

```json
{
  "code": 200000,
  "message": "OK",
  "data": {"bot_id": "bot-1", "bot_type": "service"},
  "request_id": "trace-id"
}
```

- Only `bot_id` and `bot_type` are projected. Type is personal, service, or desktop.
- Gateway requires an authenticated user; Backend verifies its signed principal.
  App-only and anonymous callers receive 401. Classification intentionally needs
  no Bot owner/editor permission, consistent with the legacy classification API.
- Reads use the existing tenant-scoped `BotServiceProtocol.get_bot_classification`.
  An entity suffix disambiguates legacy Bot IDs. Missing Bot returns 404;
  ambiguous ID without a sufficient entity returns 409; empty entity returns 422.
  Unexpected failures retain the standard public 500 behavior.
- Error responses use public envelopes with six-digit codes. No Service API or
  Plugin API changes are needed. The legacy classification endpoint remains.
- Gateway reuses the Backend Bots domain and publishes the new operation/models
  in its pinned OpenAPI artifact. No new domain or upstream URL is introduced.
- Frontend uses its same-origin `/openapi` proxy to Gateway. Path and entity query
  are encoded; unmanaged Bot 404 is treated as non-service, other errors block
  submission with retry.

## Acceptance and validation

Signed-principal adapter tests exercise non-owner reads, field allowlisting,
entity forwarding, 401, 404, 409, and 422. Existing admission/authorization and
public parameter/schema tests hold the new operation to the full public rules.
Gateway tests validate domain resolution, human-only security, and the published
schema. The OpenAPI compatibility gate rejects any breaking schema update.
Frontend tests assert the new encoded public path and six-digit response mapping.

Local results: Backend admission/parameter/principal/architecture checks passed
(239 tests); schema/authorization/core classification checks passed (119 tests);
Gateway security and published classification route checks passed (84 tests).
The published OpenAPI addition passes the compatibility gate. Frontend routing/modal/classification tests passed (34 tests), with the final
classification suite also passing after 404 notification cancellation assertions;
TypeScript, frontend lint, Open Core checks, and focused Python lint passed.

Live deployment and real provider routing remain unverified; these changes are
local implementation and contract verification, not a deployment.
