# BCS OpenAPI V1 Session Files Design

**Status:** Confirmed for implementation  
**Date:** 2026-08-12  
**Tracking issue:** [#980](https://github.com/inclusionAI/Avernet/issues/980)  
**Deferred work:** [#978](https://github.com/inclusionAI/Avernet/issues/978), [#979](https://github.com/inclusionAI/Avernet/issues/979)

## 1. Goal

Expose the existing BCS session-file workspace through the versioned BCN
OpenAPI V1 collaboration surface while preserving legacy behavior for
`bcs-cli`, `bcs-message`, and existing `/sessions/...` callers.

The change also introduces a reusable identity-policy boundary for OpenAPI V1.
Gateway may authenticate User, Bot, and App identities at the same time; BCN
selects the effective Human or Bot actor according to the route policy and
then performs resource authorization in the application facade.

## 2. Scope

The new routes are mounted below `/openapi/v1/collaboration`:

| Method | Path | Identity policy | Purpose |
| --- | --- | --- | --- |
| `GET` | `/sessions/{sid}/files` | `HumanOrOwnedBot` | List files |
| `POST` | `/sessions/{sid}/files` | `HumanOrOwnedBot` | Prepare an upload |
| `GET` | `/sessions/{sid}/files/{file_id}` | `HumanOrOwnedBot` | Get file metadata |
| `DELETE` | `/sessions/{sid}/files/{file_id}` | `HumanOrOwnedBot` | Delete a file |
| `PUT` | `/sessions/{sid}/files/{file_id}/content` | `HumanOrOwnedBot` | Stream proxy-upload bytes |
| `GET` | `/sessions/{sid}/files/{file_id}/content` | `HumanOrOwnedBot` | Download file content |
| `POST` | `/sessions/{sid}/files/{file_id}/complete` | `HumanOrOwnedBot` | Complete an upload |
| `POST` | `/sessions/{sid}/files/{file_id}/share` | `HumanOrOwnedBot` | Mint a share link |
| `GET` | `/sessions/shared-file/content` | public share token | Download shared content |

The following are explicitly not included:

- no capabilities endpoint;
- no shared-file metadata endpoint;
- no database schema change;
- no notification outbox or retry semantic change;
- no Gateway request-body streaming change;
- no removal of legacy URL construction from the core session-file service.

## 3. Existing Behavior and Compatibility

The unversioned `/sessions/{sid}/files...` routes currently back `bcs-cli` and
other legacy clients. They provide prepare, proxy upload, complete, list, get,
delete, download, share, shared metadata/content, and capabilities. This work
does not remove or alter those routes or their response contracts.

In particular:

- the legacy delete route remains `204 No Content`;
- legacy prepare and share URLs remain legacy `/sessions/...` URLs;
- `bcs-message` continues to embed the legacy `share_url` produced by
  `SessionFileService`;
- existing storage presigned URLs remain unchanged;
- existing best-effort complete notification behavior is preserved.

## 4. Architecture

The request flow is:

```text
Gateway signed Principal set
        |
        v
bcs-api-http Principal verification
        |
        v
IdentityPolicy actor selection
        |
        v
bcs-app-session SessionFile facade
  - session membership
  - file mutation authorization
  - legacy command enrichment
  - completion notification orchestration
        |
        v
existing SessionFileService
        |
        v
session-file repository + storage plugin
```

The boundaries follow the architecture constitution:

- `bcs-api-http` owns HTTP parsing, raw-body streaming, V1 envelopes, status
  mapping, and external URL projection.
- `bcs-service-api::application::v1` owns transport-neutral contracts,
  `IdentityPolicy`, and actor selection.
- `bcs-app-session` owns session membership and file-resource authorization,
  and delegates storage lifecycle operations to the existing
  `SessionFileService`.
- bootstrap validates configuration and injects concrete services and the
  public OpenAPI base URL.
- the existing `bcs-session-file` service remains the lifecycle/storage
  implementation. It is not taught about OpenAPI paths.

No HTTP type, header, URI, or V1 response envelope crosses into the application
facade.

## 5. Identity Policy

### 5.1 Policies added now

Three policies are introduced so the mechanism is complete and reusable:

- `HumanOnly`: requires an authenticated User and selects Human.
- `BotOnly`: requires an authenticated Bot and selects Bot.
- `HumanOrOwnedBot`: accepts Human or Bot; if both exist, validates ownership
  and selects Bot.

`IdentityPolicy::default()` is `HumanOnly`. A protected OpenAPI route that does
not explicitly declare a policy therefore fails closed as Human-only.

Route declarations use a Rust extension analogous to an annotation:

```rust
get(handler).identity_policy(IdentityPolicy::HumanOrOwnedBot)
```

The route policy controls identity selection only. It does not replace
resource authorization.

### 5.2 Principal combinations

| User | Bot | App | `HumanOrOwnedBot` result |
| --- | --- | --- | --- |
| yes | no | no/yes | Human |
| no | yes | no/yes | Bot |
| yes | yes | no/yes | Bot, only if `bot.owner_id == user.id` |
| no | no | yes | forbidden |
| no | no | no | unauthenticated |

AccessKey alone is not a file actor. Extra App or AccessKey identities do not
override a valid User/Bot actor.

When User and Bot coexist but `bot.owner_id != user.id`, actor selection returns
`403 forbidden`. This check uses only the signed Gateway Principal claim and
does not query BCN storage. It is evaluated at the identity-policy boundary
before any file operation runs.

Gateway is responsible for best-effort identity extraction and for including
the Bot `owner_id` in every Bot Principal shape (static bearer, directly
decoded AgentPass, or AgentPass resolved through BCN-backed identity data).
BCN continues to validate the signed claim and does not trust client-supplied
owner fields.

### 5.3 Database pressure

IdentityPolicy never queries the database. The User/Bot relationship check is
claim-local. Resource authorization may use the existing Bot Registry API to
resolve legacy ownership data; that API retains its existing cache/store
behavior and is not called merely to select an identity.

## 6. Session and File Authorization

The application facade loads the session once and requires its parent group to
exist. Membership is based on the session's current participants, not the
parent group's original roster.

### 6.1 Membership

- A Bot is a member when its `bot_uuid` is a session participant.
- A Human is a member when `human_{user.id}` is a Human participant, or a Bot
  created by that User is a Bot participant.

Protected list/get/download/share and upload operations require membership.

### 6.2 Prepare

The selected effective actor becomes the immutable file owner:

- Human actor -> `ActorRef(Human, human_{user.id})`;
- Bot actor -> `ActorRef(Bot, bot_uuid)`.

### 6.3 PUT content and Complete

These operations are deliberately narrower than the legacy membership-only
check. They are allowed when either:

1. the effective actor is the file owner; or
2. the effective actor is Human, the file owner is a Bot, and that Bot's
   `created_by` equals the authenticated User id.

Consequences:

- a Human may finish an upload prepared by their own Bot;
- Bot B cannot modify or complete Bot A's file, even when both Bots have the
  same Human creator;
- an unrelated session member cannot modify a shared file;
- authorization runs before bytes are handed to storage.

The second rule uses Bot Registry/cache lookup only for a Bot-owned file and a
Human caller.

### 6.4 Delete

Delete preserves the existing authority model after membership succeeds:

- file owner;
- session creator;
- parent-group driver;
- for a Human, any owned Bot identity used by that existing model.

The V1 facade resolves these transport-neutral values and delegates to the
existing `DeleteFileCommand` authorization.

### 6.5 Share

Any current session member may share a Ready file. The facade passes the
effective actor, its allowed legacy identities, and the current session
participant ids to the existing share use case.

## 7. Complete Notification

Complete notification becomes shared application orchestration instead of
being duplicated in the new V1 HTTP handler.

The application use case is:

1. authorize the caller and file mutation;
2. call `complete_upload`;
3. if completion succeeds, build the existing notification message;
4. notify all Bot participants other than the uploader;
5. log and ignore notification failure, returning successful completion.

If completion fails, no notification is attempted. The current best-effort
semantics are preserved; this task does not add an outbox or retry.

The adapter supplies a server-constructed content URL as application input:

- legacy adapter supplies the existing legacy URL;
- V1 adapter supplies the OpenAPI V1 protected content URL.

The URL is never accepted from request JSON. Message wording and receiver
selection remain compatible with legacy behavior.

## 8. URL Projection

### 8.1 Configuration

Bootstrap introduces:

```toml
[openapi_v1]
public_collaboration_base_url = "https://gateway.example.com/openapi/v1/collaboration"
```

The value is required to be an absolute `http` or `https` URL when configured,
must not contain query/fragment/userinfo, and is normalized without a trailing
slash. Local configuration points directly at BCN; production may point at
Gateway. The adapter never derives public URLs from `Host` or forwarded
headers.

### 8.2 Prepare projection

The existing service may return either direct storage presigned URLs or BCN
proxy URLs in `client_target_json`.

- Direct storage URLs are returned unchanged.
- Proxy upload URLs are replaced by the V1 adapter with:
  `{public_base}/sessions/{encoded_sid}/files/{file_id}/content`.
- Multipart proxy parts append the server-generated `?part=N` query.

This is a projection only. The storage service remains unaware of OpenAPI.

### 8.3 Share projection

The existing `ShareMintResult` continues to contain `share_url`, `share_token`,
and `expires_at` in this change. V1 ignores the legacy `share_url` and returns:

`{public_base}/sessions/shared-file/content?token={encoded_token}`.

Legacy HTTP and `bcs-message` continue using the service-produced legacy URL.
Removing URL construction from the service and making it return token-only is
deferred to #979.

## 9. HTTP Contract

### 9.1 JSON success responses

JSON endpoints use the existing V1 envelope:

```json
{
  "code": 20000,
  "message": "OK",
  "data": {},
  "request_id": "..."
}
```

Status/code pairs:

| Operation | HTTP | envelope code |
| --- | --- | --- |
| prepare | 201 | 20100 |
| share | 201 | 20100 |
| PUT content | 202 | 20000 |
| list/get/complete/delete | 200 | 20000 |

Unlike legacy delete, V1 delete returns a `DeleteResult` payload.

Session-file metadata omits the internal object handle. Status and actor kind
use the V1 snake_case convention. List pagination retains `prefix`, `status`,
`limit`, and `offset` and returns `items` plus `total`.

Prepare retains the established single/multipart upload shape, with only URL
projection changed.

### 9.2 Content responses

Protected content GET accepts only `show` (default `false`). Legacy `ttl` and
protected `token` query parameters are not part of the V1 schema.

Shared content GET requires `token` and accepts `show` (default `false`).

Successful downloads are not wrapped:

- presign backend: `302` redirect;
- streaming backend: `200` raw body with Content-Type, Content-Length, and
  inline/attachment Content-Disposition.

PUT accepts an optional `part` query parameter parsed as `u16`, a raw streamed
body, and an optional Content-Length. Missing Content-Length is represented as
unknown to preserve chunked-upload behavior.

## 10. Error Contract

Protected-route errors use the V1 error envelope and stable
`data.error_code`. The application vocabulary is extended so HTTP mapping does
not infer statuses from strings:

| Application category | HTTP | example error code |
| --- | --- | --- |
| invalid input | 400 | `invalid_request`, `invalid_part_number` |
| unauthenticated | 401 | `unauthenticated` |
| forbidden | 403 | `forbidden`, `file_upload_owner_mismatch` |
| not found | 404 | `session_file_not_found` |
| conflict | 409 | `file_upload_not_pending` |
| payload too large | 413 | `file_too_large` |
| unprocessable | 422 | `file_upload_incomplete` |
| bad gateway | 502 | `storage_backend_unavailable` |
| internal | 500 | `internal_error` |

The public shared-content route deliberately maps missing token, invalid token,
expired token, missing file, and non-Ready file to the same response:

- HTTP `404`;
- V1 envelope error code `shared_file_not_found`.

This avoids a token-validity and file-existence oracle.

## 11. Router and Principal Middleware

The current V1 router applies Principal verification to every OpenAPI and
Internal route. It is split into:

- protected routes wrapped by Principal verification and identity policy;
- the one public shared-content route, which receives request-id handling but
  no Principal requirement.

The public route still uses the same application facade and share-token
verification. Only identity authentication is bypassed.

## 12. Testing Strategy

Tests cover four layers:

1. contract tests for `IdentityPolicy`, V1 session-file DTOs, and object-safe
   service traits;
2. application tests for every identity combination, membership, upload-owner
   mutation rules, legacy command enrichment, error mapping, and best-effort
   notification;
3. HTTP tests for routes, envelopes, public/private middleware separation,
   raw streaming/redirects, query parsing, URL projection, and uniform shared
   404s;
4. bootstrap/config tests for URL validation and production wiring.

The legacy session-file unit and HTTP tests remain regression guards.

## 13. Deferred Work and Rollout

### #978 Gateway request-body streaming

Gateway currently buffers forwarded request bodies. V1 PUT is streaming inside
BCN, but a deployment through the current Gateway can still buffer the upload.
Gateway streaming is a separate cross-cutting change and is a rollout
dependency before advertising large proxy uploads through Gateway.

### #979 Share URL layering cleanup

The full cleanup will make the core/application service return token metadata
only and move all URL construction to delivery adapters, including legacy and
`bcs-message` call sites. This task keeps legacy URL behavior stable and limits
projection to OpenAPI V1.

## 14. Delivery Criteria

The feature is complete when:

- all nine route operations above are mounted and tested;
- protected routes enforce `HumanOrOwnedBot` and the ownership consistency
  check without IdentityPolicy database reads;
- public shared content requires only a valid token and uniformly returns 404
  on token/file failures;
- PUT/Complete enforce file-owner rules before storage mutation;
- V1 upload/share URLs use the configured OpenAPI base while legacy URLs are
  unchanged;
- Complete notification retains best-effort behavior through shared
  application orchestration;
- focused BCS tests, architecture checks, and `git diff --check` pass.
