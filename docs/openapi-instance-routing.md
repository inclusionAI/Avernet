# OpenAPI instance selection

The bot-first connection, sessions (including favorites and Session Files),
engine-runtime, models, nodes and resources endpoints accept an optional
`device_uuid` query parameter. Use `instances[].id` from the existing
`GET /openapi/v1/bots/{bot_id}/containers` response.

Example:

```text
GET /openapi/v1/bots/{bot_id}/sessions?stage=online&device_uuid={instance-id}
GET /openapi/v1/bots/{bot_id}/connection?stage=online&device_uuid={instance-id}
GET /openapi/v1/bots/{bot_id}/resources?stage=online&device_uuid={instance-id}
```

## Routing and compatibility

- Existing caller/owner authorization runs unchanged. A UUID is a routing
  selector, not an access credential.
- The stage first selects the bot's draft or published binding. The UUID is
  then resolved within that binding by the existing BaaS routing path.
- An explicit instance must not fall back to another instance. Unsupported
  providers and friend-chat instance selection are rejected.
- Omitting the selector retains existing selection behavior. Existing `/api`
  handlers and retiring component-first OpenAPI contracts are unchanged.
- Resources additionally accept `stage`, defaulting to `draft`. Published
  resources are read-only, following the existing engine/identity file policy.
- This change does not add scaling APIs or aggregate sessions across instances.

## Session Files

An upload intent stores the selected UUID alongside its resolved binding.
Completion, background materialization and downloads reuse that target even
if the user subsequently selects another instance in the UI. Switching the
UI affects new requests; it does not move an already-started upload.

An explicit selector on a file operation must match its stored UUID. File
lists with a selector return only records pinned to that instance. Legacy
records with a NULL UUID remain available when the selector is omitted;
their original instance cannot be inferred retroactively.

## Deployment

Before deploying Backend, apply
`apps/backend/src/agentclaw/community/core/session_resources/sql/2026_10_09_session_file_device_uuid.sql`.
It adds the nullable `ac_session_resource.device_uuid` column. No data backfill
is required. Old binaries tolerate the extra column. Database changes are
not executed by this PR.
