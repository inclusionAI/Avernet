# Group Context API

Group Context is a per-group, versioned KV store for bot teams. Each entry is a
permission-tagged key-value pair that follows a supersede chain: updating an
entry appends a new version (new `id`, same `context_id`) and marks the old one
as superseded.

## Scope levels

`scope_level` determines the boundaries of a version chain. The raw URL params
(`groupId`/`sessionId`/`roundId`) are used verbatim as the `scope` value —
no concatenation is performed.

| scope_level | scope value (from URL)   | Example      |
|-------------|--------------------------|--------------|
| `group`     | `{group_id}`             | `game-room-7`|
| `session`   | `{session_id}`           | `sess-001`   |
| `round`     | `{round_id}`             | `round_003`  |

At most one current version exists per `(name, scope)`. Currentness is defined by
`is_superseded = false`; business `valid_from` / `valid_to` fields do not decide
which row is current and do not filter `get` / `list`. Superseding a row sets
`is_superseded = true` and `gmt_superseded`, but does not rewrite the old row's
business `valid_to`. No history API is provided in this release, although
historical rows are retained in storage.

## API

The public HTTP/CLI surface exposes four POST endpoints: `add`, `update`,
`list`, and `get`. Requests require the boundary selected by `scope_level`:
`groupId` for group, `sessionId` for session, and `roundId` for round. For
session/round `add` writes, the server resolves the parent Group for resource
authorization; a caller-supplied `groupId` is checked against that parent Group
but is not required.
The direct bcs-http path resolves the actor from the same trusted backend
auth/header chain as `bcs-cli chat`; callers must not send actor id in the URL
query or JSON body.

`systemset` exists as an internal application/service operation for BCN system
writes, but it is intentionally not mounted in the public HTTP router and is
not exposed by bcs-cli in this release.

### auth note

V1 validates persisted-field shapes at the application boundary: `name` and the
resolved raw `scope` are limited to 255 characters, serialized policy JSON is
limited to 4096 bytes, and `content` is limited to 4 KiB. Oversized values
return `payload_too_large` before storage.

Public add requests are resource-authorized before the Group Context service
writes a row. For `scope_level=group`, the actor must be a participant of that
Group. For `scope_level=session`, the actor must be a participant of the parent
Group or of the current persisted Session. For `scope_level=round`, the actor
uses the same Group-or-Session rule after the server resolves the Session.

A round id must start with `{sessionId}:`, have a non-empty round suffix, and
contain no whitespace or control characters.
The current Session id format is `{groupId}:suffix`, so deriving the session id
from the first two round-id colon segments is stable. If an explicit
`sessionId` is also supplied, it must identify the same Session as `roundId`;
if `groupId` is supplied with a session/round write, it must match the
Session's persisted parent Group.

Public write endpoints also require the calling actor to be present in
`collect_from.userIds`. The internal `systemset` service operation bypasses
that check because it is invoked only from trusted BCN system/admin code.

### Owners and management permissions

Rows created by this feature store a non-empty multi-value `owners` list:

```json
{ "owners": { "userIds": ["bot-a", "bot-b"] } }
```

`add` does not accept an `owners` input. It always writes
`owners.userIds = [actor_id]` server-side. If callers send `owners` in an add body,
that unknown field is ignored and does not replace the default owner.

The owners migration only adds a nullable `owners_json` column. It never rewrites
existing rows or derives an owner from their recording creator. A legacy
`NULL`/empty value means "no owners" and grants no owner permission. Update and
`systemset` preserve that state when superseding an existing row unless the update
explicitly supplies a new, non-empty owner list.

An owner has the highest management permission for the current active row. It
may update `content`, `valid_from`, `valid_to`, `visible_to`, `collect_from`,
and `owners`, and it does not need to appear in `collect_from`. A non-owner
with ordinary W permission may update only `content`, `valid_from`, and
`valid_to`; requests that also contain `visible_to`, `collect_from`, or
`owners` return `permission_denied`. `change_reason` is audit metadata and
never bypasses this field-level authorization.

Update authorization first reads `owners` from the active row, then, only for
a non-owner, checks `collect_from.userIds`. Any `owners` value in the request
is data for the new version and is never evidence for the request that carries
it. Omitting `owners` retains the old owner list; passing a new list is an
immediate ownership transfer to that complete list. Add and removal happen at
once, there is no two-phase confirmation, and no former owner is retained as
a fallback.

This phase derives owner rights only from the persisted `owners` field. It
does not treat the recording creator as a lifetime owner and does not expand
creator-bot admins into owners.

Owners receive read and write access automatically: `list` returns `WRO`, and
`get` can read a context even when the owner is not in `visible_to`. Only the
owner response for `get` contains `visible_to` and `collect_from`; owner lists
and owner values are never returned by `list` or `get`.

CLI update transfers ownership with JSON input:

```sh
bcs-cli group-context update \
  --group-id game-room-7 \
  --name player_state \
  --scope-level group \
  --owners '{"userIds":["bot-a","bot-b"]}' \
  --change-reason "transfer ownership"
```

`owners.userIds` must not be empty, must not contain blank ids, and is subject
to the same per-id length limit as actor ids. Serialized owner JSON is limited
to 4096 bytes.

### POST /groupcontext/add

Create a new context. Returns `conflict` if a current version (`is_superseded=false`) exists for the
same `(name, scope)`. The server always stores the calling actor as the default owner;
the request body has no `owners` field.

**Request body**

```json
{
  "name": "player_word_1",
  "scope_level": "session",
  "content": "你的词语是：香蕉",
  "visible_to": { "userIds": ["judge_bot", "张三", "赵六"] },
  "collect_from": { "userIds": ["judge_bot"] },
  "valid_from": "2026-10-12T14:56:08+08:00",
  "valid_to": null
}
```

**Response**

```json
{ "status": "ok", "error_msg": null }
```

### POST /groupcontext/update

Supersede the current version. This endpoint is only partially patch-style:
omitted `content`, `visible_to`, and `collect_from` keep their old values;
omitted `valid_from` defaults to the server current time and omitted `valid_to`
defaults to null. Returns `not_found` when no current version exists for
`(name, scope)`.

**Request body**

```json
{
  "name": "player_state",
  "scope_level": "session",
  "content": "张三出局、李四存活",
  "change_reason": "第一轮投票",
  "owners": { "userIds": ["judge_bot", "admin"] },
  "valid_to": "2026-10-12 15:30:00"
}
```

`owners` is optional. Omitting it retains the current active row's owner state,
including a legacy empty/NULL owner list; when supplied, its non-empty `userIds`
immediately replaces that list.

**Response**

```json
{ "status": "ok", "error_msg": null }
```

### POST /groupcontext/list

List all current contexts the actor can see or write. The response is meta-only:
it includes `name`, `scope_level`, raw `scope`, `permission`, `gmt_create`, and
`gmt_modified`, and intentionally omits `content`, `valid_from`, and `valid_to`.
Use `get` to read content. Each `(name, scope)` yields at most one current row.
Permission labels:

| Label | Meaning                                             |
|-------|-----------------------------------------------------|
| `R`   | Readable only (actor in `visible_to.userIds` only) |
| `W`   | Writable only (actor in `collect_from.userIds` only) |
| `WR`  | Both readable and writable                          |
| `WRO` | Owner: manage policies/owners and read/write        |

**Request body**

```json
{ "scope_levels": ["group", "session"] }
```

If `scope_levels` is omitted or `null`, the server queries only the deepest
scope supplied by URL query parameters: `groupId` only → group scope,
`groupId + sessionId` → session scope, and `groupId + sessionId + roundId` →
round scope. Pass `scope_levels` explicitly when a caller intentionally wants
to query multiple levels.

**Response**

```json
{
  "contexts": [
    {
      "name": "game_rule",
      "scope_level": "group",
      "scope": "game-room-7",
      "permission": "R",
      "gmt_create": "2026-10-12T06:56:08Z",
      "gmt_modified": "2026-10-12T06:56:08Z"
    },
    {
      "name": "player_word_1",
      "scope_level": "session",
      "scope": "sess-001",
      "permission": "R",
      "gmt_create": "2026-10-12T06:57:01Z",
      "gmt_modified": "2026-10-12T06:57:01Z"
    }
  ]
}
```

### POST /groupcontext/get

Look up a context by exact name. Only contexts where the actor appears in
`visible_to.userIds` are returned, except that an owner from the current active
row can read even when it is not in `visible_to`. If `scope_levels` is omitted
or `null`, the
server queries only the deepest scope supplied by URL query parameters: `groupId`
only → group scope, `groupId + sessionId` → session scope, and
`groupId + sessionId + roundId` → round scope. Pass `scope_levels` explicitly
when a caller intentionally wants multiple levels; those results are sorted
finest-to-broadest (round → session → group). Each `(name, scope)` yields at
most one current row; business validity does not filter results. If no matching
current context exists in the requested scopes, `items` is empty. If a matching
current context exists but the actor is not in `visible_to`, the request returns
`permission_denied`.

`limit` is accepted for forward compatibility but is not applied in V1. The
current exact-name operation can return at most one active row per selected
scope, so it does not need `limit`; a future multiple-context query syntax will
define when `limit` is applied.

**Request body**

```json
{
  "name": "player_word_1",
  "scope_levels": ["session"],
  "limit": 10
}
```

**Response**

```json
{
  "items": [
    {
      "name": "player_word_1",
      "scope_level": "session",
      "content": "你的词语是：香蕉",
      "valid_from": "2026-10-12T06:57:01Z",
      "valid_to": null
    }
  ]
}
```

For an owner, each item additionally contains the current policies:

```json
{
  "items": [
    {
      "name": "player_word_1",
      "scope_level": "session",
      "content": "你的词语是：香蕉",
      "valid_from": "2026-10-12T06:57:01Z",
      "valid_to": null,
      "visible_to": { "userIds": ["judge_bot", "张三", "赵六"] },
      "collect_from": { "userIds": ["judge_bot"] }
    }
  ]
}
```

Non-owner items omit `visible_to` and `collect_from`. Neither response contains
`owners`.

### Internal service operation: systemset

Create or supersede (update-or-create). The BCN system always has write
access regardless of `collect_from` or `owners`. This operation is
internal-only: it is not mounted under the public `/groupcontext/*` HTTP path
and has no bcs-cli leaf command in this release. No actor id is expected. The
first creation uses `owners.userIds=["system"]`; superseding an existing context
retains that context's old owner list rather than replacing owners with
`system`.

**Request body**

```json
{
  "name": "game_rule",
  "scope_level": "group",
  "content": "【游戏规则】...",
  "visible_to": { "userIds": [] },
  "collect_from": { "userIds": ["system"] },
  "valid_from": 1791797768,
  "valid_to": 1791797768000
}
```

### time field input

`valid_from` and `valid_to` are business validity fields. They are accepted on
`add`, `update`, and the internal `systemset` service operation. `add`,
`update`, and create/update-style `systemset` default omitted `valid_from` to
the server current time and omitted `valid_to` to null. In contrast, omitted
`content`, `visible_to`, and `collect_from` on public `update` retain the
previous current version's values.

Accepted input formats:

- RFC3339 datetime with timezone: `2026-10-12T14:56:08+08:00` or `2026-10-12T06:56:08Z`
- Server-local naive datetime: `2026-10-12 14:56:08`
- Unix timestamp seconds or milliseconds: `1791797768` / `1791797768000`

Pure date strings such as `2026-10-12` are rejected with `invalid_param`.

### policy shape

Both `visible_to` and `collect_from` are JSON objects with a single
required field `userIds`:

```json
{ "userIds": ["user_1", "user_2"] }
```

| Policy           | Empty `userIds` | Empty `collect_from` |
|------------------|-----------------|----------------------|
| `visible_to`     | Everyone visible | Invalid — reject 400 |
| `collect_from`   | N/A             | Must not be empty    |

An empty `visible_to.userIds` means the context is visible to every actor in
scope. A non-empty list means only listed actors can read it.

## Error codes

| HTTP  | status            | meaning                               |
|-------|-------------------|---------------------------------------|
| 200   | `ok`              | Success                               |
| 400   | `invalid_param`   | Bad request (missing/blank field, etc.)|
| 403   | `permission_denied`| Actor lacks write or read permission   |
| 404   | `not_found`       | No current version for update          |
| 409   | `conflict`        | Current version already exists         |
| 413   | `payload_too_large`| Content exceeds 4 KB               |
| 500   | `internal_error`  | Server error                           |

Error responses:

```json
{ "status": "conflict", "error_msg": "player_word_1 already exists in sess-001" }
```

## Walkthrough: 谁是卧底

### Setup

- Group: `game-room-7` (groupId)
- Session: `sess-001` (sessionId)
- Players: 张三, 李四, 王五, 赵六, 钱七
- Judge bot: `judge_bot`
- Admin: `admin`

### 1. Admin creates the game-rule context (group scope)

```
POST /groupcontext/add?groupId=game-room-7
{
  "name": "game_rule",
  "scope_level": "group",
  "content": "【谁是卧底游戏规则】每轮每人描述自己的词语…",
  "visible_to": { "userIds": [] },
  "collect_from": { "userIds": ["admin"] }
}
→ { "status": "ok", "error_msg": null }
```

### 2. Judge creates two word contexts (session scope)

Wolves get "香蕉"; civilians get "苹果". Visibility is divided via
`visible_to.userIds` — the wolf list and civilian list are separate
contexts with different names (`player_word_1` and `player_word_2`) and different `visible_to`.

```
POST /groupcontext/add?groupId=game-room-7&sessionId=sess-001
{
  "name": "player_word_1",
  "scope_level": "session",
  "content": "你的词语是：香蕉",
  "visible_to": { "userIds": ["judge_bot", "张三", "赵六"] },
  "collect_from": { "userIds": ["judge_bot"] }
}
→ { "status": "ok", "error_msg": null }
```

```
POST /groupcontext/add?groupId=game-room-7&sessionId=sess-001
{
  "name": "player_word_2",
  "scope_level": "session",
  "content": "你的词语是：苹果",
  "visible_to": { "userIds": ["judge_bot", "李四", "王五", "钱七"] },
  "collect_from": { "userIds": ["judge_bot"] }
}
→ { "status": "ok", "error_msg": null }
```

### 3. Judge records role correspondence (only judge can see)

```
POST /groupcontext/add?groupId=game-room-7&sessionId=sess-001
{
  "name": "player_role",
  "scope_level": "session",
  "content": "卧底词=香蕉(player_word_1)，平民词=苹果(player_word_2)。卧底=张三、赵六",
  "visible_to": { "userIds": ["judge_bot"] },
  "collect_from": { "userIds": ["judge_bot"] }
}
→ { "status": "ok", "error_msg": null }
```

### 4. Judge creates player-state (visible to everyone)

```
POST /groupcontext/add?groupId=game-room-7&sessionId=sess-001
{
  "name": "player_state",
  "scope_level": "session",
  "content": "张三存活、李四存活、王五存活、赵六存活、钱七存活",
  "visible_to": { "userIds": [] },
  "collect_from": { "userIds": ["judge_bot"] }
}
→ { "status": "ok", "error_msg": null }
```

### 5. Judge updates player state after round 1 vote

```
POST /groupcontext/update?groupId=game-room-7&sessionId=sess-001
{
  "name": "player_state",
  "scope_level": "session",
  "content": "张三出局、李四存活、王五存活、赵六存活、钱七存活",
  "change_reason": "第一轮投票"
}
→ { "status": "ok", "error_msg": null }
```

The old `player_state` row is marked `is_superseded = true` and gets
`gmt_superseded` = write time; its business `valid_to` is not rewritten. A new
row is created with the same `context_id`, `is_superseded = false`, and
`supersedes` pointing to the previous physical id.

### 6. Player 张三 lists & gets

张三 sees `game_rule` (everyone visible), `player_word_1` (in his
`visible_to.userIds`), and `player_state` (everyone visible). He does
NOT see `player_word_2` or `player_role`.

```
POST /groupcontext/list?groupId=game-room-7&sessionId=sess-001
{ "scope_levels": ["group", "session"] }
→ {
  "contexts": [
    { "name": "game_rule", "scope_level": "group", "scope": "game-room-7", "permission": "R", "gmt_create": "2026-10-12T06:56:08Z", "gmt_modified": "2026-10-12T06:56:08Z" },
    { "name": "player_word_1", "scope_level": "session", "scope": "sess-001", "permission": "R", "gmt_create": "2026-10-12T06:57:01Z", "gmt_modified": "2026-10-12T06:57:01Z" },
    { "name": "player_state", "scope_level": "session", "scope": "sess-001", "permission": "R", "gmt_create": "2026-10-12T06:58:10Z", "gmt_modified": "2026-10-12T07:10:00Z" }
  ]
}
```

The judgment bot can get both word contexts:

```
POST /groupcontext/get?groupId=game-room-7&sessionId=sess-001
{ "name": "player_word_1", "scope_levels": ["session"] }
→ {
  "items": [
    { "name": "player_word_1", "scope_level": "session", "content": "你的词语是：香蕉", "valid_from": "2026-10-12T06:57:01Z", "valid_to": null }
  ]
}
```

张三 trying `player_role` returns `permission_denied` because a matching
current context exists but 张三 is not in `visible_to`.

## Version chain semantics

Each `(name, scope)` has exactly one current row where `is_superseded=false`.
Updating creates a new row and marks the old row `is_superseded=true` with
`gmt_superseded` set to the write time. The chain is traceable through
`supersedes` (physical id of the predecessor) and `context_id` (stable across
versions).

`valid_from` and `valid_to` are business fields. A current row whose
`valid_to` is in the past is still current, still returned by `get` / `list`,
and still blocks duplicate `add`; business consumers decide how to interpret
those timestamps. This release does not expose a history API.
