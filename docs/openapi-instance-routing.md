# OpenAPI instance selection

The bot-first connection, sessions (including favorites, excluding Session Files),
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

Multi-instance selection for `/sessions/{session_id}/files` and its child
operations is deferred. These endpoints retain their existing contract and
upload/materialization behavior; they do not expose `device_uuid`.
This exclusion does not apply to the separate `/resources` workspace APIs.

## Deployment

No database migration or backfill is required.
