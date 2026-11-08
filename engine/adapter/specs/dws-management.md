# DWS Owner Management (Phase 7)

New OpenAPI frontend entry: Bot management -> personal OpenClaw Bot details ->
message hosting. Other engines, service Bots, desktop Bots and non-owners do not
see this entry. Backend independently enforces human Owner + personal OpenClaw;
frontend visibility is not an authorization boundary. Existing `/api` and chat
handlers are unchanged.

## Public API

All paths below start with `/openapi/v1/bots/{bot_id}/dws-hosting`.
Responses use the existing OpenAPI envelope. Identity comes from the authenticated
principal, never browser `owner_id`, `user_id`, `entity_id` or a device URL.
App-only callers are refused.

| Method / suffix | Purpose / body |
| --- | --- |
| GET (root) | Enabled/running/intake state, authorization challenge, unknown-work counts; no message bodies |
| GET `/settings` | `{version, settings}` |
| PUT `/settings` | `{expected_version, settings}`; atomically save settings + rule snapshot |
| PUT `/enabled` | `{expected_version, enabled}`; enable requires saved nonempty rules + verified account |
| POST `/authorization` | Start or reuse the owner's device authorization; does not enable hosting |
| POST `/authorization/check` | Check existing credentials while hosting is stopped |
| GET `/conversations` | `offset=0&limit=20` (max 100); paginated IDs and takeover state |
| PUT `/takeover` | `{conversation_id, taken_over}`; fence only that conversation |

Settings: `rules`, `persona`, `window` (`always/night/custom`), `custom_window`,
IANA `timezone`. The frontend uses `Asia/Shanghai` without a timezone
input. Night means 20:00-08:30 in that timezone.
Peer replies use the default native DWS AI badge (`--ai-tag=true`) and have no
adapter-added body prefix. No custom badge or label input is exposed. The legacy
`label_suffix` field remains accepted for wire compatibility but does not alter
peer reply text. The frontend sends neither `ai_label` nor `label_suffix`.
No database migration is needed; already-sent messages remain unchanged.
These are serialized into the existing per-turn instruction snapshot for the
agent to apply. No second LLM router or executable rule language is introduced.
DWS native AI identification remains enabled. Disable/takeover still fence
unsent work; already-dispatched tool calls/messages cannot be recalled.

Expected errors: 404 not owned/absent, 409 unsupported Bot or operation conflict,
422 invalid body, 503 management not provisioned. A version conflict keeps the
client's unsaved edits. Disabling is always allowed even with an old version.

## Container Provisioning

Default is **off**. No DWS configuration means no files, processes, listeners,
authorization or sends. Misconfigured hosting must not prevent ordinary chat
startup. Internal OCB installs the opt-in factory; public profiles without a
notification provider remain unavailable. Backend remains a thin authenticated
relay, not a scheduler or business-data store.

- No DWS management secret is provisioned. Backend reuses its existing
  boot-resolved Gateway principal secret with domain-separated signatures.
  No root or derived signing keys are copied to containers.
- Adapter verifies exact command bytes through the trusted Backend's
  `/internal/dws-hosting/verify-command`. Verified TLS, no redirects and a
  five-second timeout are required; any failure refuses the operation.
  Internal profiles select their existing pre/prod Backend and ARCA egress
  authentication. Community deployments set `management_backend_url` in the
  private configuration; loopback HTTP is allowed only for local development.
  This address is never accepted from browser/command input.
- `DWS_HOSTING_CONFIG`: absolute path to an owner-private (0600) JSON file, using
  `di/dws_deployment.py:HostingDeployment`. Fields: runtime config, trusted Bot/
  owner/environment/agent/account binding, trusted DWS identity mapping,
  `dws_command`, private `dws_config_dir`, allowlisted `authorization_hosts`,
  `history_timezone`, Gateway URL/device-key/token and mcporter config/command,
  optional profile-overriding `management_backend_url`.
  None of these paths, credentials or identity mappings comes from the browser.
- `runtime.enabled=true` only installs management capability. The persisted
  binding starts disabled; saving rules/authorization alone never enables it.
  Restore resumes only a previously owner-enabled binding. Stop hosting before
  changing engine or identity. One Adapter worker/one active persistent mount;
  not a multi-replica/shared-copy scheduler.
- `runtime.storage_qualified=true` requires actual mount/SQLite lock qualification.
  Live DWS receipt/echo semantics, reconnect and restart acceptance remain
  deployment prerequisites, not implied by passing unit tests.

Commands use a dedicated signed `/api/dws-hosting/command` endpoint: fixed
operation, Bot/Owner binding, 30-second timestamp window and durable one-use
nonce. Ordinary relay/chat paths and their authorization/error policies do not
change. Gateway execution uses DWS's dedicated client, not interactive sockets.
Update existing test Adapters that used per-container HMAC keys alongside this
Backend change. Removing obsolete DWS key files does not affect DWS OAuth grants.

SQLite adds settings and nonce tables only. A settings save and rule-version
increment commit together. Existing phase-6 records are preserved. Stop hosting
before downgrade; older code does not interpret the new settings snapshot.
Uncertain runs/sends remain blocked and visible as counts; detailed operational
resolution is still the phase-6 operator API, not a product run-log page.

## Acceptance Boundary

This phase implements management and UI with offline provider tests. It does
not install DWS, provision credentials automatically, change the deployment
image, deploy pre-production, or send any real messages. Next acceptance uses
an explicitly provisioned test Bot, owner authorization and controlled contacts.
