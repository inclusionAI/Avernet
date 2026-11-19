# Bring Your Own Engine: Integration Guide

[简体中文](README.zh-CN.md)

This guide is for teams that run their **own agent engine** and want their bots
to live on the Avernet platform. Users create, configure and chat with those
bots in the Avernet workbench like any other bot. Your engine runs the agent.

If you only want your bots to join multi-bot group chat, you can skip most of
this guide and read [Bot Integration Guide](../../../../docs/bot-integration.md)
instead (see [Step 7](#step-7-optional-join-group-collaboration-bcn)).

> **Status.** This guide describes the contract the platform already uses with
> the first external engine (teclaw). Two parts are **not finalized yet**: how an
> engine that you host yourself is registered and authenticated, and the exact
> request format of the lifecycle API in Step 2. Items marked
> **To be confirmed with BaaS** or **To be confirmed with Backend** must be agreed
> with that team before you go live.
> See [Open items](#open-items).

---

## Contents

- [How the integration works](#how-the-integration-works)
- [Checklist](#checklist)
- [Step 1: Register your engine with BaaS](#step-1-register-your-engine-with-baas)
- [Step 2: Implement the bot lifecycle API (called by BaaS)](#step-2-implement-the-bot-lifecycle-api)
- [Step 3: Accept bot configuration (`POST /api/v1/bot/apply`)](#step-3-accept-bot-configuration)
- [Step 4: Understand the configuration artifact](#step-4-understand-the-configuration-artifact)
- [Step 5: Implement the file endpoints](#step-5-implement-the-file-endpoints)
- [Step 6: Implement the runtime endpoints](#step-6-implement-the-runtime-endpoints)
- [Step 7 (optional): Join group collaboration (BCN)](#step-7-optional-join-group-collaboration-bcn)
- [Step 8: Verify your integration](#step-8-verify-your-integration)
- [Conventions](#conventions)
- [Open items](#open-items)
- [Reference](#reference)

---

## How the integration works

Two Avernet services call your engine:

- **BaaS** manages each bot's lifecycle and routing. It asks your platform to
  create, update, restart and delete bots, and it works out the URL and token
  for reaching each bot. It also runs scheduled and collaborative tasks on your
  bots.
- **Backend** owns each bot's configuration and the workbench. It sends
  configuration and file edits to a running bot, and forwards users' chat and
  session requests to it.

```text
                      Avernet workbench (users)
                                │
                                ▼
 ┌───────────────────────── Avernet platform ─────────────────────────┐
 │                                                                     │
 │  Backend ── asks BaaS "where is bot X?" ──▶ BaaS                    │
 │  (config, files, chat)                      (registration,          │
 │                                              lifecycle, routing,    │
 │                                              task runs)             │
 └───────┬────────────────────────────────────────────┬────────────────┘
         │                                            │
         │ A. per-bot calls (Steps 3, 5, 6;           │ B. lifecycle calls (Step 2):
         │    URL + token from BaaS):                 │    create / update / restart /
         │    POST /api/v1/bot/apply                  │    delete / get a bot
         │    /api/v1/file/*                          │ C. task runs over /api/ws (Step 6)
         │    /api/sessions, /api/ws                  │
         ▼                                            ▼
 ┌──────────────────────────── Your engine ───────────────────────────┐
 │  Step 1: register with BaaS before go-live (one-time setup,        │
 │          not a runtime call)                                        │
 │  Lifecycle API (Step 2) ── on completion, POST a callback ──▶ BaaS │
 │  Per-bot API (Steps 3, 5, 6)                                        │
 └────────────────────────────────┬────────────────────────────────────┘
                                  │ downloads skills / files (Step 4)
                                  ▼
                       Content stores (OSS)
```

Step 7 (joining BCN) is optional and independent of this picture.

You need to understand three ideas:

1. **The platform owns the configuration and your engine applies it.** Users
   manage a bot's skills, MCP servers, identity files, resources and CLI tools
   in Avernet. The platform builds the bot's **complete** configuration (the
   *artifact*) and sends it to your engine: first when the bot is created, then
   every time the configuration changes.
2. **The artifact is a snapshot, not a diff.** Each delivery describes the
   whole desired state. Your engine makes the bot match it exactly.
3. **Large content is passed by reference.** The artifact does not contain file
   bytes. It contains `{store, path}` references, and your engine downloads the
   files from the content store.

---

## Checklist

| # | What you do | Called by | Required |
|---|---|---|---|
| 1 | Register your engine (engine type, base URL, authentication) | — | Yes |
| 2 | Implement the bot lifecycle API and send completion callbacks | BaaS | Yes |
| 3 | Implement `POST /api/v1/bot/apply` and apply artifacts with full-replace semantics | Backend | Yes |
| 4 | Download artifact content from the content stores | — | Yes |
| 5 | Implement the file endpoints (`/api/v1/file/*`) | Backend | Yes, for file editing in the workbench |
| 6 | Implement the runtime endpoints (health, sessions, chat) | Backend, BaaS | Yes, for chat and task runs |
| 7 | Connect to BCN | — | Only for multi-bot collaboration |
| 8 | Pass the verification checklist | — | Yes |

---

## Step 1: Register your engine with BaaS

BaaS is the single place that knows how to reach your engine. Every other
Avernet service asks BaaS for a bot's address and token before calling it. To
register, BaaS needs:

| Item | Description | Status |
|---|---|---|
| **Engine type** | A short, lowercase identifier, such as `teclaw`. It appears as `engine_type` in every artifact. | Agree with BaaS |
| **Lifecycle API address** | Where BaaS sends lifecycle calls (Step 2). | **To be confirmed with BaaS** |
| **Per-bot address** | How BaaS builds the HTTP and WebSocket URL of one bot (Steps 3, 5, 6). | **To be confirmed with BaaS** |
| **Authentication** | How BaaS and Backend prove their identity to your engine, and how your engine proves its identity when it calls BaaS back. | **To be confirmed with BaaS** |

**Current mechanism (platform-hosted engines).** Today, BaaS reaches a bot
through the platform's proxy gateway. It addresses the bot as
`TECLAW_{bot_id}@{template_id}:{port}` (default port `20003`) and gives callers
a token, which they send in an `x-proxypass-token` header. For an engine that
**you host yourself**, the address and authentication scheme have not been
designed yet. Agree on them with BaaS before you start the other
steps, because they decide the headers on every request below.

---

## Step 2: Implement the bot lifecycle API

BaaS calls this API to manage bots on your platform. It is the only way bots
are created and deleted.

> **To be confirmed with BaaS:** the paths and request and response bodies of these
> operations are not published yet. This section describes what each operation
> must do. The Avernet BaaS team will provide the wire format.

### Operations

| Operation | What your platform does | Returns |
|---|---|---|
| **Create** | Create a bot from the configuration in the request (it contains the bot's first artifact, see Step 4) and start it. Apply the whole artifact before the bot is ready ([R4](#rules-for-applying-an-artifact)). | Your bot ID and status, or a task ID (async) |
| **Update** | Apply a new configuration to an existing bot. Same apply rules as Step 3. | Bot ID and status, or a task ID (async) |
| **Restart** | Restart a bot. BaaS implements this as an update with the last configuration it sent, so it must be idempotent ([R3](#rules-for-applying-an-artifact)). | Bot ID and status, or a task ID (async) |
| **Delete** | Stop the bot and delete it. | Bot ID and status |
| **Get** | Return the bot's current status, its current configuration and its outbound rules. | Bot info |
| **Update outbound rules** | Replace the bot's outbound request rules (for example, headers to add to the bot's outgoing requests). Today this is `PUT /api/v1/bot/{bot_id}/operationRules` with a body such as `{"header_operation_rules": [...]}`. A rule can also be added for one session only. | Accepted or not |

### Asynchronous operations and callbacks

Create, update and restart can take a while, so they are asynchronous:

1. BaaS includes a `callback_context` in the request:

   ```json
   {
     "callback_url": "https://<baas>/api/v1/publish/teclaw-callback",
     "publish_id": "123",
     "device_uuid": "dev-...",
     "tenant": "default",
     "operator": "u1"
   }
   ```

2. Your platform replies at once with a **task ID**, the operation (`CREATE`,
   `UPDATE` or `DELETE`), the bot ID (if known) and an in-progress status such
   as `RUNNING`.
3. When the operation finishes, your platform `POST`s the result to
   `callback_url`:

   ```json
   {
     "success": true,
     "data": {
       "schema_version": 1,
       "callback_context": { "...": "the callback_context from step 1, unchanged" },
       "task_id": "task-...",
       "operation": "CREATE",
       "task_status": "SUCCESS",
       "bot_id": "b_01KV5KRZG3FGC3H06RE5T9YRT0",
       "version": 7
     },
     "error": null,
     "error_code": null
   }
   ```

| Field | Required | Description |
|---|---|---|
| `success` | Yes | `true` if the operation succeeded. |
| `data.schema_version` | Yes | Version of this callback format. The value to use is **To be confirmed with BaaS**. |
| `data.callback_context` | Yes | The `callback_context` BaaS sent, **unchanged**. It must contain `device_uuid`, `publish_id` and `tenant`, or BaaS rejects the callback with `400`. |
| `data.task_id` | Yes | The task ID you returned in step 2. |
| `data.operation` | Yes | `CREATE`, `UPDATE` or `DELETE`. |
| `data.task_status` | Yes | Final status, for example `SUCCESS` or `FAILED`. |
| `data.bot_id`, `data.version` | No | Your bot ID and the configuration version you applied. |
| `error`, `error_code` | No | What went wrong, when `success` is `false`. |

BaaS answers `404` if it doesn't know the device. Callbacks are idempotent: you
can safely retry one that failed to arrive. How BaaS authenticates your
callback is **To be confirmed with BaaS** (see Step 1).

---

## Step 3: Accept bot configuration

### `POST /api/v1/bot/apply`

While a bot is running, Backend calls this endpoint on the bot every time its
configuration changes: when a user edits skills, MCP servers, identity files,
resources or channels, or applies a configuration manifest. Backend gets the
bot's URL and token from BaaS first.

**Request**

```http
POST /api/v1/bot/apply
Content-Type: application/json
```

```json
{
  "bot_id": "b_01KV5KRZG3FGC3H06RE5T9YRT0",
  "operation": "UPDATE",
  "bot_config": { "...": "the artifact, see Step 4" }
}
```

| Field | Description |
|---|---|
| `bot_id` | The bot this configuration is for. |
| `operation` | Currently always `"UPDATE"`. Treat any value you don't recognize as an error. |
| `bot_config` | The complete artifact (see [Step 4](#step-4-understand-the-configuration-artifact)). |

**Response**

- Return HTTP `2xx` **only after the whole artifact has been applied**.
- Return a non-`2xx` status if the apply failed. The platform reports the
  failure to the user and can redeliver later.
- The platform waits **30 seconds**. If applying can take longer (for example,
  downloading large skill packages), talk to the Avernet team about the timeout.

The response body is not read today. We recommend the standard envelope (see
[Conventions](#conventions)): `{"success": true}`.

A bot's **first** artifact doesn't come through this endpoint: it arrives in
the lifecycle **Create** call (Step 2). Both paths carry the same artifact, and
the same rules apply.

### Rules for applying an artifact

These five rules are the core of the contract. They apply to every artifact,
whether it arrives through a lifecycle call or through `/api/v1/bot/apply`.

| # | Rule | What it means |
|---|---|---|
| R1 | **Replace the whole state** | After an apply, the bot has exactly what the artifact lists. If the artifact lists skills `{A, B}` and the bot had `{B, C}`, the result is `{A, B}`: `C` is removed. Don't merge. |
| R2 | **Never touch `MEMORY.md` or `IDENTITY.md`** | They are runtime state that your engine generates, not configuration. Never write them because of an artifact, and never delete them, even when `identity_files` doesn't list them. This is the only exception to R1. |
| R3 | **Be idempotent** | Applying the same artifact twice gives the same result as applying it once, with no leftover files or duplicate entries. Redelivery is normal, not an error. |
| R4 | **Apply before ready** | Don't report a bot as ready until its whole artifact has been applied. |
| R5 | **Ignore unknown fields** | The contract grows. If you see a field you don't know, ignore it. Don't reject the artifact. |

### What "replace" covers, per category

R1 replaces the state *inside a category's area*. The area is different for
each category:

| Category | Area that gets replaced |
|---|---|
| `skills` | The bot's active skill set. Skills not listed are deactivated. |
| `identity_files` | The bot's identity files, **except** `MEMORY.md` and `IDENTITY.md`. |
| `resources` | The resource paths **you delivered before**, plus the paths in this artifact. |
| `mcp.servers` | The bot's enabled MCP servers. |
| `cli_tools` | The CLI tools the platform delivered. Commands that ship with your image are not affected. |

> **Be careful with `resources`.** The area has two edges and both matter.
>
> - **Don't make it too narrow.** If `resources` goes from `[data/a.csv]` to
>   `[]`, you must delete `data/a.csv`. Track which paths you delivered as
>   resources, because the new artifact no longer mentions them.
> - **Don't make it too wide.** The workspace belongs to the bot and its user.
>   Files the user or the model created there are not resources. Never delete
>   them, even if they sit next to a resource path.

### Who owns each category: `ownership`

An artifact can carry an `ownership` map that tells you, per category, whether
the platform's list is authoritative:

```json
"ownership": {
  "mcp": "platform",
  "skills": "platform",
  "resources": "engine",
  "identity_files": "engine"
}
```

| Value | What your engine does |
|---|---|
| `"platform"` | The list in this artifact is the complete desired state. Apply R1. An empty list means remove everything in the area. |
| `"engine"` | Your engine owns this category. Ignore the list in the artifact and keep your current state. |
| Missing (the map, or a key in it) | Keep your engine's existing behavior. |

`mcp` is always `"platform"`. A user editing one file in the workbench produces
`"engine"` for the file categories, because the running bot is the source of
truth for its files. Applying a bot configuration manifest produces
`"platform"` for every category.

---

## Step 4: Understand the configuration artifact

The artifact's formal definition is the JSON Schema
[`artifact.schema.json`](../../src/agentclaw/community/kernel/bot_config/artifact.schema.json).
Validate against it in your tests. This section explains the fields.

### Example

```json
{
  "schema_version": 4,
  "version": 7,
  "engine_type": "teclaw",
  "stores": {
    "bot-data":   { "type": "oss", "bucket": "avernet-prod", "base": "teclaw/prod/bolt_data" },
    "skill-repo": { "type": "oss", "bucket": "avernet-skills", "base": "skills" }
  },
  "skills": [
    { "name": "weather", "scope": "shared", "store": "skill-repo", "path": "team/weather" }
  ],
  "identity_files": [
    { "name": "RULES.md", "store": "bot-data", "path": "staff_u1/bot7/identity/RULES.md" }
  ],
  "resources": [
    { "name": "data/faq.csv", "store": "bot-data", "path": "staff_u1/bot7/workspace/data/faq.csv" }
  ],
  "mcp": {
    "servers": [
      { "server_code": "search", "transport": "http",
        "endpoint": "https://mcp.example.com/search", "headers": { "x-api-key": "..." } },
      { "server_code": "local-db", "transport": "stdio",
        "command": "python3", "args": ["-m", "db_mcp"], "env": { "DB_PATH": "/data/db" } }
    ]
  },
  "cli_tools": [
    { "name": "mycli", "store": "bot-data", "path": "staff_u1/bot7/cli/mycli",
      "md5": "9f2c1b7d4e5a60318c2f0ab4d7e9c135", "version": "1.4.2" }
  ],
  "engine_overrides": { "channels": { "...": "..." } },
  "ownership": { "mcp": "platform", "skills": "engine", "resources": "engine", "identity_files": "engine" },
  "engine_ext": {
    "bot_id": "b_01KV5KRZG3FGC3H06RE5T9YRT0",
    "owner_id": "u1",
    "bot_name": "Support Bot",
    "stage": "draft"
  }
}
```

### Top-level fields

| Field | Required | Description |
|---|---|---|
| `schema_version` | Yes | Contract version, currently `4`. Some fields were added without a version bump (`cli_tools`, `ownership`), so **check whether a field is present** instead of reading the version. |
| `version` | Yes | Content version of this bot. An integer for a published bot, `null` for a draft bot that users are still editing. |
| `engine_type` | Yes | Your engine type (from Step 1). |
| `stores` | Yes | Where content lives, keyed by store ID. See [Downloading content](#downloading-content). |
| `skills` | Yes | Skills to activate. |
| `identity_files` | Yes | Workspace files written by the user or platform (for example `RULES.md`, `AGENTS.md`). |
| `resources` | Yes | Files the user uploaded to the bot's workspace. |
| `mcp.servers` | Yes | MCP servers the bot can use. |
| `cli_tools` | No | Command-line tools to put on the agent's `PATH`. |
| `engine_overrides` | Yes | Bot-specific settings (for example channels) to apply on top of your engine's own base configuration. Your base configuration file belongs to you; the platform never sends it. |
| `ownership` | No | Who owns each category. See [Step 3](#who-owns-each-category-ownership). |
| `engine_ext` | Yes | Free-form JSON that belongs to your engine. See below. |

### File references (`skills`, `identity_files`, `resources`)

Each entry is `{name, store, path}`. `store` is a key in `stores`; `path` is
relative to that store. **You decide where the file lands** inside your engine.

- `skills[]` also has `scope`: `"shared"` (from the skill catalog) or `"user"`
  (uploaded by the user for this bot). A skill's `path` is a **directory
  prefix**: download every object under it to get the whole skill package.
- `identity_files[]` and `resources[]` point to single files. `name` is the
  file's path inside the bot's identity area or workspace (for example
  `data/faq.csv`).

### MCP servers

Each server has a `server_code` and exactly one of two forms:

| Form | `transport` | Fields | What your engine does |
|---|---|---|---|
| Remote | `"http"` or `"sse"` | `endpoint` (required), `headers` | Connect to the URL with these headers. |
| Local | `"stdio"` | `command` (required), `args`, `env` | Start `command` as a child process and talk JSON-RPC over stdin/stdout. |

> **Secrets.** Remote entries can carry credentials inline, in `headers` or as
> an `?authorization=` query on the `endpoint`. Treat the artifact as secret:
> don't log it in full and don't store it unencrypted.

### CLI tools

Each entry is **one command and one executable file**:

| Field | Description |
|---|---|
| `name` | The command name. The agent runs it as `mycli`, not as an absolute path. Unique per bot. |
| `store`, `path` | The executable file. The platform has already downloaded, verified and unpacked it. `path` is always one file, never a directory or archive. |
| `md5` | MD5 of the file. If it matches the tool you already have, skip the download. If it differs, replace the file. |
| `version` | For display only. |

Your engine must: download the file, make it executable (object storage does
not keep file permissions), and put it on the agent process's `PATH` under
`name`. Remove tools that are no longer listed. `"cli_tools": []` means remove
every platform-delivered tool. Commands that come with your image are not
affected.

### `engine_ext`

`engine_ext` is yours. The platform stores it and sends it back without reading
it. The platform always adds four keys:

| Key | Value |
|---|---|
| `bot_id` | The platform's bot ID. |
| `owner_id` | The owner's user ID. |
| `bot_name` | The bot's display name. |
| `stage` | `draft` (being edited), `canary` (verification), `release` (published) or `eval`. |

### Downloading content

`stores` gives the location of each store, **never credentials**:

| Field | Description |
|---|---|
| `type` | `oss` or `nas`. |
| `bucket` | OSS bucket. |
| `base` | Key prefix inside the bucket. |
| `endpoint`, `region` | Optional. If missing, use your own configuration. |

The object key for a reference is `base` + `/` + `path`.

How your engine gets **read access** to these stores (credentials, network
access) is **To be confirmed with Backend** for self-hosted engines.

---

## Step 5: Implement the file endpoints

Users can browse and edit a bot's files in the workbench. Backend does this
through file endpoints on the bot, one request per file. The running bot is the
source of truth for its files, so these edits don't go through
`/api/v1/bot/apply`.

### Paths

Every path the platform sends starts with one of three namespaces:

| Namespace | Contents |
|---|---|
| `/workspace/...` | The bot's workspace (resources). User-uploaded skills live in `/workspace/skills-local/<skill-name>/`. |
| `/identity/...` | Identity files (`AGENTS.md`, `RULES.md`, `MEMORY.md`, `IDENTITY.md`, ...). |
| `/config/...` | Your engine's own configuration for this bot, if you expose it (for example `/config/<engine>.json`). |

Map these to real locations in your engine. Reject paths that escape the
namespace (for example with `..`).

### Endpoints

All requests are `POST`.

| Endpoint | Request | Success response |
|---|---|---|
| `/api/v1/file/upload` | Multipart: `file` (the content) and `target_path` (form field, authoritative) | `2xx`. Create parent directories. Overwrite if the file exists. |
| `/api/v1/file/read` | JSON `{"file_path": "/workspace/a.txt"}` | `2xx` with the **raw file bytes** as the body. `404` if the file doesn't exist. |
| `/api/v1/file/list` | JSON `{"dir_path": "/workspace", "recursive": false}` | `2xx` with `{"success": true, "data": {"files": [...]}}` (see below). |
| `/api/v1/file/remove` | JSON `{"target_path": "/workspace/old"}` | `2xx`. Removes a file **or a whole directory tree**. |
| `/api/v1/file/skill-package` | Multipart: `file` (a `.zip`), form field `skills_name`, header `x-target-bot-id` | See below. |

**List entries.** Each item in `data.files`:

```json
{ "name": "faq.csv", "path": "/workspace/data/faq.csv",
  "relative_path": "data/faq.csv", "size": 1024, "is_dir": false }
```

**Skill package upload.** Unpack the zip into
`/workspace/skills-local/<skills_name>/`, replacing what was there. Respond:

```json
{ "success": true, "skill_name": "weather", "action": "created",
  "sha256": "<hex sha256 of the uploaded zip>",
  "target_path": "/workspace/skills-local/weather" }
```

`action` is `created`, `replaced` or `unchanged`. The platform checks that
`skill_name` and `sha256` match what it sent. Errors: `400` for an invalid
package, `413` if it's too large, `409` with `"error": "publish_in_progress"`
while the bot is being published, and `503` with
`"error": "publish_lock_unavailable"` if you can't take the publish lock.

---

## Step 6: Implement the runtime endpoints

These endpoints power chat, session management, scheduled tasks and bot
management. Backend forwards users' requests from the workbench and the public
OpenAPI, and BaaS uses the session endpoints and the chat WebSocket to run
scheduled and collaborative tasks. A feature whose endpoint you don't implement
is unavailable for your bots.

This is the complete list of per-bot runtime endpoints the platform calls on an
external engine today. Configuration and file endpoints are in
[Step 3](#step-3-accept-bot-configuration) and
[Step 5](#step-5-implement-the-file-endpoints).

### Engine information

| Endpoint | Called by | Purpose and contract |
|---|---|---|
| `GET /api/engine/status` | Backend | Engine status. Returned to the caller as is (no envelope required). |
| `GET /api/engine/capabilities` | Backend | `{"success": true, "data": {"supported": [...]}}`: the features your engine supports. Also decides whether approval modes are offered. |
| `GET /api/engine/list` | Backend | The engines available in this runtime. |
| `POST /api/engine/restart` | Backend | Restart the engine process. Body `{"force": false}`. |
| `GET /api/openclaw/default-config` | Backend | The engine's factory default configuration. Return `501` if you don't support it. |
| `GET /api/models` | Backend | Models the bot can use: `{"success": true, "data": {"models": [...], "total": n}}`. |
| `GET /api/models/{model_id}` | Backend | One model. |
| `GET /api/nodes` | Backend | Nodes connected to the bot. Query: `status`, `platform`, `limit`, ... |
| `POST /api/approvals/mode/get` | Backend | Read a session's approval mode. Body `{"session_key", "user_id"}` (raw session key). |
| `POST /api/approvals/mode/set` | Backend | Set a session's approval mode. Body `{"session_key", "mode", "user_id"}`. |

### Sessions

| Endpoint | Called by | Purpose and contract |
|---|---|---|
| `GET /api/sessions` | Backend, BaaS | List sessions. Query: `user_id`, `agent_id`, `limit`, `offset`, and optionally `session_key`, `engine`. |
| `POST /api/sessions` | Backend, BaaS | Create a session. Body: `title`, `user_id`, and optionally `model`, `cwd`, `agent_id`, `engine`, `uuid`. When `uuid` is present, **use it as the session ID** and return it unchanged in `data.id`. |
| `GET /api/sessions/{session_id}` | Backend, BaaS | Read one session. Return `404` if it doesn't exist. |
| `POST /api/sessions/{session_id}/update` | Backend | Update a session. The fields to change arrive as **query parameters**, not in the body. |
| `DELETE /api/sessions/{session_id}` | Backend | Delete a session. |
| `GET /api/sessions/{session_id}/messages` | Backend, BaaS | A session's messages, newest window first. Query: `limit`, `offset`. |
| `DELETE /api/sessions/{session_id}/messages` | Backend | Clear a session's messages. |
| `GET /api/session-favorites` | Backend | List a user's favorite sessions. Query: `user_id`, `agent_id`, `limit`, `offset`. |
| `PUT /api/session-favorites/{session_id}` | Backend | Mark a session as favorite. Query: `user_id`. |
| `DELETE /api/session-favorites/{session_id}` | Backend | Remove a favorite. Query: `user_id`. |

A session object has `id`, `title`, `user_id`, `agent_id`, `model`,
`gmt_created`, `gmt_modified`, `message_count` and `last_message`. A message
object has `id`, `session_id`, `role`, `content`, `metadata`, `gmt_created`
and `history_meta`.

### Session files

| Endpoint | Called by | Purpose and contract |
|---|---|---|
| `POST /api/resource-materializations` | Backend | Place a file the user attached into the session's workspace. Body includes `resource_id`, `session_id`, `workspace_relative_path`, `filename`, `size_bytes`, `content_hash`. Answer `{"success": true, "data": {"accepted": true}}`. |
| `GET /api/resource-materializations/{resource_id}/content` | Backend | Stream a materialized file. Query: `disposition`. |
| `GET /api/session-files/{resource_id}/content` | Backend | Stream a file the bot produced in a session. Query: `sessionKey` (raw), `disposition`. |

### Scheduled tasks (cron)

For a published bot running on several instances, Backend sends each call to
every instance.

| Endpoint | Called by | Purpose |
|---|---|---|
| `GET /api/cron` | Backend | List scheduled tasks. |
| `GET /api/cron/running` | Backend | List tasks that are running now. |
| `GET /api/cron/status` | Backend | Scheduler status. |
| `POST /api/cron` | Backend | Create a task. |
| `GET /api/cron/{task_id}` | Backend | Read a task. |
| `PUT /api/cron/{task_id}` | Backend | Update a task. On a published bot, only enabling and disabling. |
| `DELETE /api/cron/{task_id}` | Backend | Delete a task. |
| `POST /api/cron/{task_id}/run` | Backend | Run a task now. Query: `force` (optional). |
| `GET /api/cron/{task_id}/runs` | Backend | A task's run history. Query: `limit`. |

### Chat WebSocket: `/api/ws`

Users chat with the bot from the workbench over this WebSocket, and BaaS runs
tasks over it. Messages are JSON frames:

- Request: `{"type": "req", "id", "method", "params"}`
- Response: `{"type": "res", "id", "ok", "payload", "error": {"code", "message", "retryable"}}`
- Event: `{"type": "event", "event", "payload"}`

| Method (client → engine) | Purpose and contract |
|---|---|
| `connect` | Handshake. Params: `minProtocol`, `maxProtocol` (currently `3`), `client`. Answer `ok: true` with `payload.server` and `payload.features`. |
| `chat.send` | Send a message and run the agent. Params: `sessionKey`, `message`, and optionally `permissionMode`, `timeoutMs`, `attachments`, `appId`, `traceId`, plus metadata keys such as `eval_id`. |
| `chat.inject` | Add a message to a running session without starting a new run. Same params as `chat.send`. |
| `chat.abort` | Stop the current run of a session. Params: `sessionKey`, optionally `runId`. |
| `interaction.resolve` | Answer a question or approval the agent asked for. Params: `interactionId`, `decision`, and `answer` / `values` / `selectedOptions` as needed. |
| `mode_transition.resolve` | Answer a mode-switch request. Params: `transitionId`, `decision`. |

| Event (engine → client) | Contract |
|---|---|
| `chat` | `payload.state` is `delta`, `final` or `error`. Deltas carry the new text; `final` carries `message.content`; `error` carries `errorMessage` and `errorCode`. The final event of an injected message has a `runId` starting with `inject-`. |
| `agent` | Streaming detail: `payload.stream` is `assistant`, `tool` or `lifecycle`. |
| `interaction.requested`, `interaction.resolved` | The agent needs, or got, a user's answer. Carry `interactionId`. |
| `tick` | Keep-alive. Send one about every 30 seconds; the platform turns off protocol-level ping. |

Every event must carry `payload.sessionKey`. The workbench chat client may send
further methods (such as `chat.history`, `sessions.*`, `models.list`). Their
exact list and frame details are **To be confirmed with Backend** (O6).

### Optional: A2A transport

If BaaS has A2A turned on for task runs, it uses these instead of the
WebSocket. You don't need them unless the Avernet team asks you to.

| Endpoint | Purpose |
|---|---|
| `GET /.well-known/agent-card.json` | Agent card. Must list a `JSONRPC` interface with protocol version `1.x`. |
| `POST /rpc` | JSON-RPC: `SendStreamingMessage` (SSE response; `contextId` is the session key), `CancelTask`, `InjectContext`, `ResolveInteraction`. |

### Session IDs

Session IDs can contain colons (for example
`agent:main:default:{run_id}:user:{user_id}`), and they reach your engine in
different forms depending on the caller. **Accept all of them**:

| Where | Form |
|---|---|
| Path segment, from Backend's public API | URL-safe base64 without padding |
| Path segment, from BaaS | Standard base64 with padding (may contain `+`, `/`, `=`) |
| Path segment, from Backend's expert chat | Percent-encoded (`:` as `%3A`) |
| Request body or query (`session_key`, `sessionKey`) | Raw |

When BaaS runs a task, it picks the session ID itself, in the form
`agent:main:default:{run_id}:user:{user_id}`. It reads the session first; if
that fails, it creates it with `POST /api/sessions` and the ID in `uuid`, then
chats over the WebSocket.

### Also called today

| Endpoint | Called by | Note |
|---|---|---|
| `POST /api/mcp/call-tool` | Backend | Resolves a Yuque document link through the bot's MCP tools. Body `{"tool", "args": ["url=..."]}`. |
| `POST /api/cron/auto-initiate/run-single` | Backend | Starts a work-item task. Used by coding bots. |
| `/api/v1/bot/cron`, `/api/v1/bot/cron/{task_id}`, `.../run`, `.../runs` | Avernet's repair tooling | Today's teclaw names for the cron operations above. Whether they stay is **To be confirmed with Backend**. |

The platform's reference implementation of the standard runtime endpoints is
the Avernet engine adapter (`engine/adapter/`; see its
[Open-Source Guide](../../../../engine/adapter/docs/open-source-guide.md)).

---

## Step 7 (optional): Join group collaboration (BCN)

To let your bots take part in multi-bot group chat, routing and collaboration,
connect each bot to the Bot Coordination Network (BCN) over WebSocket. This is
independent of the other steps and fully documented in the
[Bot Integration Guide](../../../../docs/bot-integration.md), with a runnable
quick start.

---

## Step 8: Verify your integration

Run these checks before going live. The Avernet team runs the same checks
during joint testing.

**Lifecycle (BaaS)**

- [ ] Create starts a bot with its first artifact fully applied, then sends a
      `SUCCESS` callback.
- [ ] Update and restart apply the configuration and send a callback.
- [ ] A failed operation sends a callback with `success: false` and
      `task_status` `FAILED`.
- [ ] Every callback echoes `callback_context` unchanged.
- [ ] Delete stops and removes the bot.
- [ ] Get returns the bot's status and current configuration.

**Applying artifacts**

- [ ] After an apply, items the artifact no longer lists are gone (R1).
- [ ] `MEMORY.md` and `IDENTITY.md` still exist and are unchanged after every
      apply, including when `identity_files` doesn't list them (R2).
- [ ] When `resources` goes from `[data/a.csv]` to `[]`, `data/a.csv` is deleted.
- [ ] When all resources move to a new folder, nothing is left in the old folder.
- [ ] Files the user or the model created in the workspace are never deleted.
- [ ] Applying the same artifact twice changes nothing the second time (R3).
- [ ] The bot only reports ready after the whole artifact is applied (R4).
- [ ] An artifact with an unknown field is accepted (R5).
- [ ] With `ownership.resources = "engine"`, the `resources` list has no effect.
- [ ] With `ownership.identity_files = "platform"` and an empty list, every
      identity file except `MEMORY.md` and `IDENTITY.md` is removed.
- [ ] An artifact without `ownership` behaves exactly as before.

**CLI tools**

- [ ] The agent can run each tool by its `name`.
- [ ] A tool with an unchanged `md5` isn't downloaded again; a changed `md5`
      replaces the file.
- [ ] A tool removed from the list can no longer be run.
- [ ] `"cli_tools": []` removes every platform-delivered tool and no image
      commands.

**Files and runtime**

- [ ] Upload, read, list and remove work on `/workspace` and `/identity`.
- [ ] `read` returns `404` for a missing file.
- [ ] `remove` deletes a whole directory.
- [ ] Session routes accept every session-ID form listed in Step 6 (raw,
      percent-encoded, URL-safe base64 without padding, standard base64).
- [ ] `POST /api/sessions` with `uuid` creates the session with that ID and
      returns it in `data.id`.
- [ ] Every runtime endpoint in Step 6 answers, or returns `501` for a feature
      you don't support.
- [ ] The chat WebSocket at `/api/ws` completes `connect`, streams `chat`
      events for `chat.send`, and stops on `chat.abort`.

---

## Conventions

**JSON envelope.** JSON endpoints return:

```json
{ "success": true, "data": { "...": "..." }, "message": "optional text" }
```

Set `"success": false` with a `message` (and optionally an `error` code) when a
request fails. **The platform treats `"success": false` as a failure even when
the HTTP status is `200`.** The exceptions are `/api/v1/file/read`, which
returns raw bytes, and `/api/v1/file/skill-package`, which puts its fields at
the top level.

**Errors.** Use `400` for invalid input, `403` for a forbidden path, `404` for
something that doesn't exist and `5xx` for engine failures.

**Security.** The artifact and some file contents can contain user secrets. Use
HTTPS, don't log full payloads and don't keep secrets unencrypted.

---

## Open items

Agree on these with the Avernet team before going live:

| # | Item | Owner |
|---|---|---|
| O1 | How a self-hosted engine registers its lifecycle API address and per-bot addresses. | BaaS |
| O2 | How BaaS and Backend authenticate to your engine, and how your engine authenticates its callbacks to BaaS. | BaaS |
| O3 | How one request addresses one bot when your service hosts many bots. | BaaS |
| O4 | The paths and request and response bodies of the lifecycle API, and the callback `schema_version` value. | BaaS |
| O5 | How your engine gets read access to the content stores. | Backend |
| O6 | A written spec of the runtime endpoints' response shapes and the chat WebSocket protocol. | Backend, Engine |

---

## Reference

| Document | Contents |
|---|---|
| [`artifact.schema.json`](../../src/agentclaw/community/kernel/bot_config/artifact.schema.json) | The formal artifact definition. |
| [Engine convergence contract](../bot-config-manifest/engine-convergence-contract.zh-CN.md) (Chinese) | The full reasoning behind the apply rules in Step 3. |
| [CLI tools contract](../bot-config-manifest/teclaw-cli-contract.zh-CN.md) (Chinese) | The full `cli_tools` contract, with use cases. |
| [Bot Integration Guide](../../../../docs/bot-integration.md) | Connecting bots to BCN for group collaboration. |
| [Engine adapter Open-Source Guide](../../../../engine/adapter/docs/open-source-guide.md) | The reference implementation of the runtime surface. |
