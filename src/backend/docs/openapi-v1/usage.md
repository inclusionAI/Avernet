# Using the `/openapi/v1` API

**English** | [简体中文](usage.zh-CN.md)

_A usage guide, not an API reference._ The reference is the OpenAPI document
served at `/openapi.json`; it describes shapes. This file describes how to
*call* the API: which credential to present in which situation, how your
application gets permission to act on a user's bot, what every operation
expects on the wire, and what to do when a call comes back refused.

> Building this API rather than calling it? [`README.md`](README.md) is the
> engineering handoff, and [`engine-surface.md`](engine-surface.md) the
> endpoint inventory for the engine-runtime group.

---

## 1. Before you start

There is **one** address. Everything in this document is a path under it:

```text
https://<your-avernet-host>/openapi/v1/...
```

Ask your platform operator for the host. The same host serves the machine-
readable description at `/openapi.json` and a browsable one at `/docs`.

Three properties of the API worth knowing up front, because they shape every
call:

- **A bot is the noun.** Almost every operation addresses one bot, and the bot
  is a path segment: `/openapi/v1/bots/{bot_id}/<component>`.
- **Every call says who it is for.** A `?user_id=` query parameter names the end
  user the call acts on behalf of. It is required nearly everywhere, on reads
  and writes alike.
- **One response shape.** Success and failure both come back in the same
  envelope, so a client parses one structure everywhere (§7).

---

## 2. Who is calling: two kinds of caller

The API recognises two kinds of caller, and a single request may present both.

| Caller | Credential | How it is presented |
| --- | --- | --- |
| **A person** | their SSO session, resolved through BUService | `x-one-id: <token>` header, **or** the `IAM_TOKEN` cookie |
| **An application** | the API key issued to your application | `Authorization: Bearer <api key>` |

**The API key goes in `Authorization` and nowhere else.** There is no alternative
header for it; presented anywhere else it authenticates as no application at all.

The two credentials never compete for the same slot — the person's identity
travels in its own header or in a cookie — so a request that needs both simply
carries both. That is the normal shape for a third-party integration.

> Other credential types exist elsewhere on the platform (bot session tokens,
> tenant access keys). They are **not** accepted here; presenting one is the
> same as presenting nothing.

That gives three calling shapes. Every operation in this API is described in
terms of them.

### Shape A — a person calling for themselves

A first-party client: our own workbench, a CLI a developer runs, a script using
someone's own token.

```bash
curl 'https://<host>/openapi/v1/bots?user_id=<user-id>&page=1&page_size=20' \
  -H 'x-one-id: <sso token>'
```

`user_id` must be the caller's own id. Naming anyone else is `403`.

### Shape B — an application calling with the user present

Both credentials on the wire. Required by the operations that record a consent
decision or read across an organisation (§5, §6).

```bash
curl -X POST 'https://<host>/openapi/v1/bots/20260813_a7k2m9p1/authorized-apps?user_id=<user-id>' \
  -H 'x-one-id: <sso token>' \
  -H 'Authorization: Bearer <api key>'
```

`user_id` must still be the id of the person whose token you are presenting.

### Shape C — an application calling alone, for a user

No human involved. Your application presents only its own key and names in
`?user_id=` the user it is acting for. **This is the integration case**, and it
works only where that user has authorized your application for the bot (§5) and
only on the operations that accept a caller with no human (§6).

```bash
curl 'https://<host>/openapi/v1/bots/20260813_a7k2m9p1/sessions?user_id=<the authorizing user>' \
  -H 'Authorization: Bearer <api key>'
```

What you can reach in this shape is exactly what that user can reach right
now — never more, and it shrinks the moment their own access does.

---

## 3. Getting credentials

### 3.1 Your application's API key

An application is registered on the platform and receives a plaintext API key
**once**. On a self-hosted or single-box install that registration is:

```bash
curl -X POST https://<host>/admin/apps \
  -H 'Content-Type: application/json' \
  -d '{
        "app_name": "acme-scheduler",
        "owners":   "acme-platform-team",
        "app_type": "SERVER",
        "tenant":   "acme",
        "creator":  "alice",
        "status":   "ACTIVE"
      }'
```

```json
{
  "id": 4711,
  "app_name": "acme-scheduler",
  "owners": "acme-platform-team",
  "app_type": "SERVER",
  "tenant": "acme",
  "status": "ACTIVE",
  "env": "",
  "api_key": "7Qk2mP9nR4vT6wY1zA3bC5dE8fG0hJ2k"
}
```

On a managed deployment your platform operator does this for you and hands you
the key. Either way:

- **Store it at issuance.** Only a one-way hash is kept, so the key cannot be
  shown again — a lost key is replaced, not recovered.
- **`status` must be `ACTIVE`.** A record registered any other way is created
  successfully and then never authenticates.
- **`tenant` is your data boundary.** Everything you read and write lives inside
  it, and a new tenant reads empty until it has data of its own.
- **`app_id`** (the `id` above) is the number that identifies your application
  in authorization records and in `DELETE …/authorized-apps/{app_id}`.
- The registration endpoint is a development convenience and is **not
  authenticated in the community build**. A production install must put an
  admin credential in front of it.

If you hold an older JWT-style app token it still authenticates during the
transition window, but it cannot be converted — plan to rotate onto an API key.

### 3.2 A user's credential

A person is authenticated by the platform's **SSO**, resolved through BUService.
Your client verifies nothing itself — it presents what the SSO session already
gave it, in one of two accepted forms:

- **`x-one-id: <token>`** — the subject token, forwarded explicitly. This is the
  server-to-server form, and the one a third-party integration uses.
- **the `IAM_TOKEN` cookie** — carried automatically by a browser that already
  holds a session. Natural for a first-party or browser-based client; a server
  integration should not be holding one.

Either form resolves to the same person, and that person's id is the value you
put in `?user_id=`.

> A self-hosted install may be wired to a different identity provider, in which
> case user tokens come from that provider instead. Nothing else below changes,
> and the API key is presented the same way regardless.

---

## 4. Writing a request

Four parameters do all the addressing. Three of them are **query parameters on
every method**, including `PUT`, `POST` and `DELETE` — this API never puts
identity in a body or in a path segment.

### `bot_id` — in the path

```text
/openapi/v1/bots                        the account-level collection
/openapi/v1/bots/{bot_id}               one bot
/openapi/v1/bots/{bot_id}/<component>   one component of that bot
```

`sessions`, `skills`, `routines`, `resources`, `engine`, `identity`,
`approvals`, `models`, `connection`, `startup-script`, `harness` and
`authorized-apps` all hang off `{bot_id}`.

A handful of literals are served in the `{bot_id}` position, so a bot cannot be
named any of them:

```text
approvals  authorized  ceiling  check-name  connection  engine  identity
loadtest   logs        mcp      messages    models      resources
routines   sessions    skills
```

### `?user_id=` — on nearly every operation

The end user the call acts for. Same value on every operation, same meaning on a
read as on a write.

```text
GET    /openapi/v1/bots/b-1?user_id=u-42
PUT    /openapi/v1/bots/b-1?user_id=u-42        {"bot_name": "Ada"}
DELETE /openapi/v1/bots/b-1?user_id=u-42
POST   /openapi/v1/bots/b-1/skills?user_id=u-42 <raw zip>
```

- Calling **as a person** (shapes A and B): it must be your own id. Anything
  else is `403`.
- Calling **as an application alone** (shape C): it is the user who authorized
  you. A user who authorized you for nothing is answered `404` — the same answer
  a nonexistent bot gets, so guessing a `user_id` gains nothing.

Four operations take no `user_id`, having no user dimension:
`GET /bots/check-name`, `GET /bots/mcp/servers`,
`GET /bots/mcp/servers/{server_code}`, `GET /bots/mcp/tenants`. They still
require a credential.

> **One trap.** `GET /openapi/v1/bots/logs/**` also takes `user_id`, but there it
> means *whose traces to read* rather than *whose call this is*, and a caller
> presenting both credentials may point it at someone else. Same spelling,
> opposite meaning — do not share client code between the two.

### `?owner_id=` — only for a bot that is not yours

The owner of the bot you are addressing. It defaults to the caller, so you need
it only to reach a bot **shared** with you, and only on the operations that
publish it: the runtime group (`sessions`, `engine`, `models`, `approvals`,
`connection`), the two skills collection operations, and the authorization
operations.

It exists because `bot_id` alone does not identify a bot — the same id can exist
under more than one owner, so `(bot_id, owner_id)` is the real address.

Who may operate a shared bot: its **owner**, or a **collaborator at member level
or above**. A bot being publicly visible grants operation to nobody. Anyone else
gets the same answer as for a bot that does not exist — a `404`, never a `403`.
The `harness` group is the one exception and sets the bar higher: owner, or a
collaborator at **admin** level.

### `?stage=` — which runtime you mean

`draft` (default), `verify`, or `online`.

- `draft` is the bot's own workspace, and the only runtime a personal bot has.
- `verify` and `online` exist while a corresponding release is live.
- A stage with no live runtime answers
  `409 "No live runtime at the requested stage"` — there is no fallback to
  another stage.
- **Reads serve all three; writes accept only `draft`.** A released runtime is
  replaced by releasing again, never edited, so `PUT …?stage=online` answers
  `409 "The requested stage is read-only"` and writes nothing — not to the
  release, and not to the draft as a substitute.

Taken by the runtime group plus `…/engine/config` and
`…/identity[/{file_type}]`. Startup script, MCP, resources, skills and routines
are draft-only.

### Pagination

Every list operation takes `page` (1-based, default 1) and `page_size` (default
20, max 100), and answers a page carrying `total` — the count of all matches,
not of the page.

---

## 5. Getting authorized for a user's bot

This is what turns shape B into shape C: a user lends your application their
access to one bot.

### The authorization call

```bash
curl -X POST \
 'https://<host>/openapi/v1/bots/20260813_a7k2m9p1/authorized-apps?user_id=<user>&owner_id=<bot owner>' \
  -H 'x-one-id: <the user’s sso token>' \
  -H 'Authorization: Bearer <your api key>'
```

```json
{
  "code": 201000,
  "message": "Created",
  "data": {
    "app_id": 4711,
    "app_name": "acme-scheduler",
    "user_id": "u-42",
    "bot_id": "20260813_a7k2m9p1",
    "owner_id": "u-owner",
    "granted_at": "2026-08-19T09:14:02Z"
  },
  "request_id": "b0a6d2f4e8c94b1a9f3d5e7c60218a4d"
}
```

Five properties of this call, each of which someone eventually trips over:

1. **Both credentials are mandatory.** The user's, because they are consenting;
   yours, because the record names your application. Your application is
   **never a parameter** — it is read from your key — so a request cannot
   authorize any application but the caller. You cannot grant access to someone
   else's application, and nobody can grant it to yours.
2. **The authorizing user must be able to operate the bot** — its owner, or a
   collaborator at member level or above. Anyone else gets the `404`. The rule
   is *you may lend exactly the access you have*.
3. **`owner_id` defaults to the caller.** Omit it for the user's own bot; name
   it when they are authorizing you on a bot shared with them.
4. **It is idempotent.** Re-authorizing returns the existing record unchanged,
   so retrying a timed-out request is safe. Two *different* users authorizing
   you on the same bot are two separate records, not a repeat — they are lending
   two different authorities.
5. **The record names two people.** `user_id` is the authorizing user, whose
   access you borrow; `owner_id` is the bot's owner, who may never have heard of
   your application. Everything you may later do is scoped by the first.

### Discovering your own scope

```bash
curl 'https://<host>/openapi/v1/bots/authorized?user_id=<user>' \
  -H 'Authorization: Bearer <your api key>'
```

Works with no human on the wire — it is the one call an integration makes to
find out what it may reach. It is also the **only complete** view: a bot the
user does not own but authorized you on appears in no listing of that user's
bots, so without this you could not discover it.

### The user's view, and withdrawal

```bash
# which applications can reach this bot, and who let each one in
curl 'https://<host>/openapi/v1/bots/{bot_id}/authorized-apps?user_id=<user>' \
  -H 'x-one-id: <sso token>'

# withdraw one
curl -X DELETE 'https://<host>/openapi/v1/bots/{bot_id}/authorized-apps/4711?user_id=<user>' \
  -H 'x-one-id: <sso token>'
```

Both need only the user's credential, deliberately: a withdrawal that required
your cooperation would be no withdrawal at all — and that is exactly the
situation it exists for (a key lost, rotated, or a relationship ended).

- The bot's **owner** sees every authorization standing against their bot,
  whoever granted it, and withdrawing removes all of an application's access to
  that bot.
- A **collaborator** sees and withdraws only what they granted themselves.

### Three ways your access ends

1. **Withdrawal**, as above.
2. **The authorizing user loses access to the bot** — removed as a collaborator,
   or demoted. Your access ends on your next call. Nothing is revoked and there
   is no event to observe; the question is asked live every time.
3. **The bot is deleted**, which withdraws every authorization on it.

### What this is not

There is no OAuth authorization-code flow here: no `/authorize`, no `/token`, no
scopes, no consent screen we host. Authorization is the call above and nothing
else. (A design for an OAuth flow exists in this repository as a reference note;
it is not implemented, so do not build a client against it.)

---

## 6. What your application can do alone

Every operation declares how it treats a caller with **no human on the wire**.
This is the table to plan an integration against, because it decides which calls
shape C can make at all.

| Behaviour | Which operations | What it means |
| --- | --- | --- |
| **Authorized bot only** | `GET/PUT/DELETE /bots/{bot_id}`, `…/restart`, `…/status`, `…/passport`, `…/engine/config`, `…/startup-script`, `…/identity/**`, `…/resources/**`, `…/routines/**`, `…/skills/{skill_id}/**` | Works on a bot the named user authorized you for. Anything else answers `404`. |
| **Authorized bot, including shared ones** | `…/sessions/**`, `…/engine/{status,available,capabilities}`, `…/approvals/**`, `…/models/**`, `…/connection`, `GET/POST …/skills` | The same, and these accept `owner_id`, so a bot merely shared with the authorizing user is reachable. |
| **Filtered result** | `GET /bots`, `GET /bots/authorized` | Always allowed; the listing is narrowed to the bots you were authorized for. |
| **Needs any authorization from that user** | `GET /bots/ceiling` | No bot dimension, so the bar is the relationship: allowed while you hold at least one authorization from the named user. |
| **Always allowed** | `GET /bots/check-name`, `GET /bots/mcp/servers`, `…/mcp/servers/{server_code}`, `…/mcp/tenants` | Catalogue and availability reads with the same answer for everyone. |
| **Requires a human** — `401` | `POST /bots`, all three `…/authorized-apps` operations, `…/{bot_id}/harness/**`, `…/bots/logs/**`, `…/mcp/servers/{server_code}/config`, `…/mcp/servers/{server_code}/permissions`, `…/loadtest/**` | Present the user's credential too (shape B). |

Why those last ones need a person, since they are what an integration has to
design a human into:

- **Creating a bot** spends the user's quota, and no authorization can cover a
  bot that does not exist yet.
- **Granting, listing and withdrawing authorizations** — authorization is a
  human act. An application must not be able to widen its own access, withdraw a
  competitor's, or survey what else reaches a bot.
- **Bot logs** are an organisation-wide observability surface where `user_id`
  means "whose traces". An authorization covers a bot; it does not carry that
  meaning.
- **MCP configuration** is account-level state with no bot dimension. Being
  authorized on a bot is not permission to reconfigure an account. (The MCP
  *catalogue* reads are a different thing and are always allowed.)
- **Harness** diagnoses and rewrites a bot's live configuration files. It is a
  maintenance surface rather than a delegated one, and it asks for admin-level
  access rather than the member-level bar the rest of the API uses.

**The rule behind all of it:** your reach is exactly the authorizing user's
reach, re-checked on every request. Not a snapshot taken when they authorized
you — the live thing.

---

## 7. Responses

Every operation answers the same envelope, on success and on failure alike:

```json
{
  "code": 200000,
  "message": "OK",
  "data": { },
  "request_id": "b0a6d2f4e8c94b1a9f3d5e7c60218a4d"
}
```

- **`code`** is six digits: the HTTP status (3) plus a business subcode (3).
  `200000` OK, `201000` Created, `202000` Accepted, `404000` not found.
- **`message`** is always English. On authentication and permission failures it
  is deliberately coarse — every `401` says `"Unauthorized"`, every `403` says
  `"Forbidden"` — so it will not tell you which part of a rejected credential to
  fix.
- **`data`** is present but `null` on errors and on empty results.
- **`request_id`** mirrors the `X-Trace-ID` response header. **Quote it in any
  support request:** the specific reason for a refusal is recorded against it
  and is not returned to you.
- **Lists** carry `{"total": n, "items": [...]}` in `data`.
- **Deletes** answer `{"deleted": true}`. A failed delete is an error envelope,
  never `deleted: false`.

| Status | When |
| --- | --- |
| `400` | Input the schema accepted but the server could not use — bad log query, invalid resource path, invalid bot name, unsupported engine |
| `401` | No usable credential, or an operation that requires a human (§6) |
| `403` | `user_id` names someone other than the person you authenticated as |
| `404` | Not found — **or** it is not yours, **or** you were not authorized for it. All three are identical by design |
| `409` | Conflicts with current state — name taken, quota reached, no live runtime at the stage, stage read-only, device not ready |
| `413` | Body over a published limit (startup script 24 KiB, skill package, file preview) |
| `422` | Failed validation — a missing or malformed query parameter, most often `user_id` |
| `500` | Internal error |
| `501` | Runtime group only: this bot's engine does not offer the capability — see `…/engine/capabilities` |
| `502` | A dependency of the platform failed |
| `504` | The bot's runtime did not answer in time |

---

## 8. When a call is refused

The response body is deliberately uninformative, so diagnose from what you sent.

### `401`

Read `code`:

- **`401001` — your credential was not accepted.** Either it is missing for a
  required identity (check §2 and §6: the commonest miss is sending only an API
  key to an operation that needs a person too), or it is present and invalid —
  an expired SSO session, a key that is not `ACTIVE`, a key from another
  environment. Also check the API key is in `Authorization: Bearer` and nowhere
  else: under any other header it authenticates as no application.
- **`401000` — your credential is fine, but this operation does not accept a
  caller with no human.** Check §6 and re-issue the call in shape B. For an
  integration this is by far the most common `401`.

> If *every* call in one environment answers `401`, including ones that should
> succeed, that environment is misconfigured. Raise it with your platform
> operator rather than debugging your client.

### `403`

Exactly one cause: `?user_id=` is not the id of the person you authenticated as.
It must be the id the SSO credential you presented resolves to.

An application calling alone cannot get a `403` this way — its `user_id` is
checked against the authorization instead, and a wrong one is a `404`.

### `404` on something you are sure exists

In order of likelihood:

1. **Not authorized.** Calling alone, with no live authorization for this bot
   from this user. Confirm with `GET /openapi/v1/bots/authorized`.
2. **The authorizing user lost access to the bot** — no longer its owner or a
   member-level collaborator. Checked live on every call, so this appears with
   no revocation anywhere.
3. **`owner_id` missing on a shared bot.** It defaults to the caller, so
   omitting it addresses *that user's own* bot of the same id — which probably
   does not exist.
4. **Wrong tenant.** Your key's tenant is the data boundary; a bot created in
   another tenant is invisible.
5. **`user_id` names the wrong person** — for an application, the user who
   authorized you, not the bot's owner. They differ whenever the bot is shared.

### `422`

Almost always a missing or empty `user_id`. Every operation takes it except the
four catalogue reads in §4, and it always goes in the **query string** —
whatever the method, whatever the body.

### `409` on the runtime group

- `"No live runtime at the requested stage"` — you asked for `verify` or
  `online` on a bot with no live release there (a personal bot never has one).
  There is no fallback; do not retry against another stage.
- `"The requested stage is read-only"` — a write against a released stage.
  Nothing was written, and releasing again will not make it land. Write to
  `draft`, then release.
- `"Bot device is not ready"` / `"Bot has no active device"` — the bot exists but
  its runtime is not up. Poll `GET …/{bot_id}/status`.

---

## 9. What the API covers

| Group | Address | What it does |
| --- | --- | --- |
| bots | `/openapi/v1/bots`, `…/{bot_id}` | create, list, read, update, delete, restart, name check, quota ceiling, authorization-status poll, runtime status, passport, engine config, startup script |
| sessions | `…/{bot_id}/sessions` | conversation sessions and their messages |
| engine | `…/{bot_id}/engine` | runtime status, capabilities, availability |
| approvals | `…/{bot_id}/approvals` | the bot's approval mode |
| models | `…/{bot_id}/models` | models the bot's engine offers |
| connection | `…/{bot_id}/connection` | **ready-to-use chat socket URLs** |
| skills | `…/{bot_id}/skills` | install, list, activate, deactivate, remove local skills |
| routines | `…/{bot_id}/routines` | scheduled routines, runs, run history |
| resources | `…/{bot_id}/resources` | the bot's workspace files — list, stat, upload, download, preview, mkdir, delete |
| identity | `…/{bot_id}/identity` | the bot's identity files |
| harness | `…/{bot_id}/harness` | diagnose the bot's live config, preview / apply / roll back a patch, and read the diagnostic report and its history |
| authorized-apps | `…/{bot_id}/authorized-apps`, `…/bots/authorized` | the authorization record (§5) |
| mcp | `…/bots/mcp` | MCP marketplace catalogue, and per-account server config |
| logs | `…/bots/logs` | trace-level observability across bots (needs both credentials) |
| loadtest | `…/bots/loadtest` | an echo endpoint and socket, for measuring the platform |

### Chatting with a bot

Chat does not go through this HTTP API. Call
`GET /openapi/v1/bots/{bot_id}/connection` — it answers with finished WebSocket
URLs and an expiry, and your client opens the socket itself. The credential
already travels in the URL, so the handshake needs no headers, which is what
lets a browser open it directly.

The connection is designed to outlive the credential's expiry: it is checked
once, at the handshake. If you put a proxy in front of your client, do not give
that path an idle read timeout.

### Creating a bot is sometimes two steps

`POST /openapi/v1/bots` answers either `201` with the bot, or `202` with a
pending-authorization payload. On `202`, poll
`POST /openapi/v1/bots/{bot_id}/auth-status` until it resolves — it is a `POST`
because it *completes* the creation, not a read. (A `GET` spelling exists and is
being retired.)

### Startup scripts

`PUT …/{bot_id}/startup-script` appends a `bash` body to the bot's start
sequence. Worth knowing before you use it:

- it runs on **every** start the platform composes — creation, restart, release
  — with no de-duplication, so **make it idempotent**;
- an edit takes effect on the **next** start, never on a running container, so
  the first write always needs a restart;
- **do not put secrets in the body.** It is stored as written and appears in
  platform logs in recoverable form;
- body limit 24 KiB (`413` above it), no time limit on the run — the start is
  reported only once your script exits, so background anything long-running;
- output goes to `/home/admin/logs/startup_script.log` inside the container, and
  there is no API to read it yet;
- two kinds of bot cannot run one at all, and `GET …/startup-script` reports
  `supported: false` for them; a write is refused `409` rather than stored where
  it would silently never run.

### Older addresses

Every address this API served before bot-first addressing still answers, with
its original parameter names in their original places, so an existing client
keeps working unchanged. New clients should use the `{bot_id}`-first addresses
in §4: some older ones ignore `stage` and always act on the draft — including
two writes, which therefore report success where their replacements answer `409`
and write nothing.

---

## 10. Integration checklist

1. Get your API key and note your `app_id` and tenant (§3.1). Confirm it is
   `ACTIVE`.
2. Decide, per call, which shape you are in (§2). The person's identity goes in
   `x-one-id` or the `IAM_TOKEN` cookie; the API key goes in
   `Authorization: Bearer` and nowhere else.
3. Have each user authorize you on their bot (§5), with both credentials on the
   wire.
4. Confirm your scope with `GET /openapi/v1/bots/authorized`.
5. Check §6 for every operation you intend to call alone. Anything marked
   **requires a human** needs a person in your product flow — not a retry.
6. Send `user_id` on every call but the four catalogue reads; send `owner_id`
   whenever the bot is not the authorizing user's own; send `stage` only when you
   mean a released runtime.
7. Log `request_id` from every response. For a refusal whose reason the API will
   not tell you, it is the only handle support has.
