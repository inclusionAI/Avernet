# DWS Hosting Management Relay

## Context Boundary

```yaml
purpose: Authenticate owner management requests and relay signed DWS commands.
provides:
  - router
consumes:
  - EngineRuntimeRelayProtocol
internal_dependencies:
  - agentclaw.community.adapters.http.openapi_v1
  - agentclaw.community.api.engine_runtime_service
  - agentclaw.community.di
```

### Change impact

Additive HTTP contract for the hosting configuration frontend and a separately
deployed container implementation. This module does not import Engine code,
start listeners, store hosting data, or change existing chat/relay behavior.

## Endpoints

Prefix: `/openapi/v1/bots/{bot_id}/dws-hosting`. Human Owner only; personal
OpenClaw Bots only. Identity is taken from the authenticated principal, never
from caller-supplied owner/user/entity query parameters. App-only callers are
refused. Every operation requires `?user_id=<authenticated user>` through the
standard `require_user_id` dependency; a mismatched user is refused with 403.
The signed command still uses the authenticated identity, not an owner hint.
Responses use the existing OpenAPI envelope.

| Method | Suffix | Purpose |
| --- | --- | --- |
| GET | (root) | Read hosting/authorization status |
| GET / PUT | `/settings` | Read/save versioned rules, schedule, persona and AI-label suffix |
| PUT | `/enabled` | Explicitly enable/disable hosting |
| POST | `/authorization` | Start owner device authorization |
| POST | `/authorization/check` | Check current authorization |
| GET | `/conversations` | Paginate bound conversations (`offset`, `limit` <= 100) |
| PUT | `/takeover` | Take over/resume one conversation |

Settings and enable writes include `expected_version`. The container owns
version checks, authorization readiness and execution fences. Saving settings
or authorizing does not enable hosting. A version conflict returns 409, while
disabling remains valid with a stale version. All request schemas forbid extra
fields. The mandatory AI label cannot be removed via the optional suffix.

## Split Rollout

Backend and Gateway may ship independently of Engine and frontend. Signing
reuses the existing boot-resolved Gateway principal secret and its boot policy;
there is no DWS-specific secret to configure or distribute. Missing platform
configuration fails closed. No database migration, image, gitlink or runtime
installation is included in this change.

The relay sends only `POST /api/dws-hosting/command`, with `payload` (compact
UTF-8 JSON string) and `signature`. Payload fields: `bot_id`, `actor_id`, fixed
`operation`, validated `data`, `timestamp_ms`, and fresh `nonce`. The signature
is HMAC-SHA256 of the exact payload bytes using a per-Bot/Owner key. Derive that
key as ASCII hex HMAC-SHA256 of compact, unescaped-Unicode JSON
`["dws-management-v1", bot_id, owner_id]` with the existing principal secret.
Both root and derived keys stay exclusively in Backend.

The separately deployed receiver validates the exact signed body via
`POST /internal/dws-hosting/verify-command` on its trusted configured Backend
(TLS, no redirects, five-second timeout). The proof is the credential; the
verification route only returns `{"verified":true}` and cannot issue proofs.
Adapter also checks local Bot/Owner scope, timestamp freshness and one-use nonce
before executing. Network/authentication failures must not fail open.
The old per-container-key Adapter must be updated with this protocol; Backend
deployment alone does not upgrade it or start hosting. It returns command data
or `management_error` (mapped to HTTP 409). Other transport errors retain the
existing relay/error-envelope behavior. Live acceptance requires a provisioned
test container and remains separate from these offline contract tests.
