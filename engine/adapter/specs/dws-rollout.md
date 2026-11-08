# DWS rollout

Only DWS hosting is gated. Existing chat, other engines and non-DWS system
configuration permissions are unchanged. A grant does not enable hosting or
replace the existing personal-OpenClaw/Owner checks.

## Configuration

Reuse `ac_config_category` / `ac_config_item`, category `dws_hosting`, key
`rollout`, separated by the Backend's current environment. Missing, malformed
or unavailable configuration denies access.

Operators use the existing authenticated APIs (no new administration API):

1. `POST /api/v1/config/categories` with
   `{"category":"dws_hosting","category_name":"DWS hosting"}`.
2. `POST /api/v1/config/set` with
   `{"category":"dws_hosting","config_key":"rollout","config_value":{"mode":"allowlist","staff_ids":["165137"]}}`.

Modes: `disabled`, `allowlist`, `all`. Staff IDs are strings and match exactly.
DWS create/set require the existing operator privilege; delete already requires
it. Ordinary users cannot grant themselves access. Do not pass an environment
from the browser: the deployment selects it.

The optional `apps/backend/src/agentclaw/community/core/system_config/dws_rollout_pre.sql`
seeds only `pre`, only `165137`, without overwriting an existing configuration.
It is not automatically executed. Review existing configuration before using
it. No schema migration is required.

## Enforcement

- `GET /openapi/v1/bots/{bot_id}/dws-hosting/eligibility` returns
  `data.available`, not the whitelist, without contacting the container.
- The frontend hides the hosting entry until eligible and rechecks every 30s.
  The Backend independently denies DWS management requests with 403; turning
  hosting off or taking over a conversation remains permitted for the Owner.
- On enable, Adapter persists the Backend-signed enable proof in its private
  state directory. `/internal/dws-hosting/runtime-eligibility` accepts it only
  to read this Bot/Owner's current eligibility. It cannot replay management
  commands, whose 30s expiry, scope and nonce protections remain unchanged.
- Adapter checks before execution/sending and periodically while idle. Positive
  results cache for at most 30s; Backend requests time out after 5s. Revocation
  or verification failure durably disables hosting and cancels unsent work.
  An already-sent external request cannot be recalled. Regrant never auto-enables.

Deploy Backend before Adapter/frontend, configure the pre allowlist, then
explicitly enable hosting. Existing enabled installations without an enable
proof stop safely and require explicit re-enabling. No new secret environment
variable is required. Normal chat has no dependency on this verifier.
