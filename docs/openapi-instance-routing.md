# OpenAPI instance selection

The bot-first connection, sessions (including favorites, excluding Session Files),
engine-runtime, models, nodes and resources endpoints accept an optional
`device_uuid` query parameter. Use `data.instances[].id` from the existing
`GET /openapi/v1/bots/{bot_id}/containers` response.

Example:

```text
GET /openapi/v1/bots/{bot_id}/sessions?stage=online&device_uuid={instance-id}
GET /openapi/v1/bots/{bot_id}/connection?stage=online&device_uuid={instance-id}
GET /openapi/v1/bots/{bot_id}/resources?stage=online&device_uuid={instance-id}
```

## Creating a session on a selected instance

`POST /openapi/v1/bots/{bot_id}/sessions` accepts the same `device_uuid` query
selector. The containers endpoint returns the current **online** instance
snapshot, so a published service Bot must name `stage=online` as well as the
instance; the default `stage` is `draft`.

```http
POST /openapi/v1/bots/{bot_id}/sessions?user_id={caller-id}&entity_id={owner-id}&stage=online&device_uuid={instance-id}
Content-Type: application/json

{"title": "job-123", "model": "provider/model"}
```

The same `user_id` may be reused across requests. Each request resolves its own
instance, so two sessions for one user can target different instances. Keep
`stage=online` and `device_uuid={instance-id}` on follow-up session, message and
connection calls. If only `device_uuid` is omitted, BaaS chooses an active
instance within that stage; if `stage` is omitted, it falls back to `draft`, not
to the original online binding. A collaborator operating someone else's Bot must
also send `entity_id={owner-id}` with `user_id={caller-id}`; an owner may omit
`entity_id`.

`device_uuid` is only a routing selector. Authorization still uses the verified
caller and Bot ownership/collaboration rules. BaaS then verifies that the UUID
belongs to that Bot and is `ACTIVE`; an unknown, foreign or inactive instance is
rejected without falling back to another instance.

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
upload/materialization behavior; they do not expose `device_uuid`. A session
created on a selected instance does **not** thereby make its Session Files
requests instance-affine; those requests continue to use the existing runtime
target resolution and may reach another instance. This exclusion does not apply
to the separate `/resources` workspace APIs.

## Deployment

No database migration or backfill is required.
