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

Backend and Gateway may ship independently of Engine and frontend. Provision
`DWS_MANAGEMENT_MASTER_KEY` (at least 32 bytes) through the Backend secret store,
separately per environment. Without it, owner requests return 503 before relay.
The new paths are not usable on an old Adapter; configuring the Backend key
alone neither upgrades containers nor starts hosting. No database migration,
image, gitlink or runtime installation is included in this change.

The relay sends only `POST /api/dws-hosting/command`, with `payload` (compact
UTF-8 JSON string) and `signature`. Payload fields: `bot_id`, `actor_id`, fixed
`operation`, validated `data`, `timestamp_ms`, and fresh `nonce`. The signature
is HMAC-SHA256 of the exact payload bytes using a per-Bot/Owner key. Derive that
key as ASCII hex HMAC-SHA256 of compact, unescaped-Unicode JSON
`["dws-management-v1", bot_id, owner_id]` with the master key. Only the derived
key belongs in the matching container; never distribute the master key.

The separately deployed receiver must validate the signature, Bot/Owner scope,
timestamp freshness and one-use nonce before executing. It returns command data
or `management_error` (mapped to HTTP 409). Other transport errors retain the
existing relay/error-envelope behavior. Live acceptance requires a provisioned
test container and remains separate from these offline contract tests.
