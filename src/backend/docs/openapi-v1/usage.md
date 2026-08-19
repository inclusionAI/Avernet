# Using the `/openapi/v1` public API

**English** | [简体中文](usage.zh-CN.md)

_A usage guide, not an API reference._ The reference is the OpenAPI document the
gateway serves; this file explains the things that document cannot say: which
credential to present for which situation, what the gateway does with it before
your request reaches the backend, how an application gets permission to act for
a user's bot, and what to look at when a call is refused.

Companion documents:

- [`README.md`](README.md) — the delivery status board and the engineering
  handoff for people building this surface.
- [`engine-surface.md`](engine-surface.md) — the endpoint-by-endpoint inventory
  for the engine-runtime (Track C) group.
- `src/gateway/docs/2026-07-21-auth-design.md` — the authentication design this
  implements.

---

## 1. The shape of one call

Everything public goes through the gateway. `/openapi/v1` is reachable only
through it: the backend refuses any request that did not arrive through the
gateway's authenticated path, because such a request carries no signed principal
to verify.

```text
   your client                gateway                          backend
   ───────────                ───────                          ───────
   credentials  ──────────▶   1. resolve the domain
   (headers)                     (first segment after
                                  /openapi/v1 → upstream)
                              2. authenticate: run the
                                 identity chains this
                                 route declares
                              3. strip any inbound
                                 X-Avernet-Principal
                              4. sign the resolved
                                 identity set (HS256,
                                 aud=backend, iss=gateway,
                                 ttl 60s)
                                    │
                                    ▼  X-Avernet-Principal: <jwt>
                                                   5. verify the token once
                                                   6. resolve tenant + caller
                                                   7. admission: may THIS
                                                      caller reach THIS
                                                      operation?
                                                   8. scope every read/write
                                                      by tenant + user_id
```

Four consequences worth internalising before you write any client code:

1. **You never send `X-Avernet-Principal` yourself.** The gateway strips any
   inbound copy and injects its own. A request carrying one is not a shortcut —
   it is a forged header that gets thrown away.
2. **The gateway resolves only the identities a route declares.** If a route's
   rule does not name `app`, your API key is authenticated but never reaches the
   backend, so nothing can be scoped by it. This is why some operations require
   you to send *both* a user credential and an app credential even though either
   one alone would authenticate you.
3. **Authentication and authorization are separate hops.** The gateway answers
   "who is this?"; the backend answers "may they do this?". A `401` from the
   gateway and a `401` from the backend look the same to you and mean different
   things — §9 explains how to tell them apart.
4. **The address selects the upstream.** The first path segment after
   `/openapi/v1` is the gateway's domain selector. `/openapi/v1/bots/**` reaches
   this backend; anything outside a configured domain is refused at the edge and
   never reaches any service.

Base URL: `https://<your-gateway-host>`. Every path in this document is relative
to it. The gateway also serves the aggregated OpenAPI document at `/openapi.json`
and Swagger UI at `/docs` when `module_config.web.enable_api_docs` is on.

---

## 2. The four identities, and the two that matter here

The gateway models a caller as a **set** of identities, not one. A single request
can carry a person *and* the program calling on their behalf, and the backend
sees both.

| Identity | What it proves | Credential | Carries a tenant? |
| --- | --- | --- | --- |
| `user` | a real person | Google OAuth access token | **No** — nothing in a user credential says which tenant a person acts for |
| `app` | a registered third-party application | app API key | Yes — from its registration |
| `bot` | a bot acting as itself | bot session token | Yes |
| `access_key` | a tenant-level machine key | access-key token | Yes |

**On `/openapi/v1/bots/**` only `user` and `app` are usable.** That is not a
guideline, it is enforced twice:

- the gateway's `route_security` declares only `user` and `app` for these paths,
  so a `bot` or `access_key` credential is never even resolved here;
- the backend's verifier refuses an identity set that names neither a user nor an
  app, whatever else it carries.

A `bot` credential is meaningful elsewhere (the collaboration session-file
routes declare it). An `access_key` currently resolves for no route on this
surface at all.

### Tenancy, in one paragraph

The tenant is the data-isolation key: every read is confined to it and no write
can name another. It comes from whichever *machine* identity is on the request —
the app's registration says which tenant it belongs to. A request naming only a
user asserts no tenant and resolves to the internal default (`teamclaw`), which
is the correct scope for a first-party caller on our own frontend. If a request
carries two identities that name **different** tenants, the whole token is
rejected: one request cannot belong to two tenants.

---

## 3. Getting credentials

### 3.1 An application API key

An application is registered with the gateway, which returns a plaintext API key
**exactly once**:

```bash
curl -X POST https://<gateway-host>/admin/apps \
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

What to know about that key:

- **32 base62 characters.** The registry stores only a salted PBKDF2 hash plus
  an 8-character lookup prefix, so a database read cannot recover a usable
  credential — and neither can we. Lose it and you get a new one; there is no
  "show key again".
- **`status` must be `ACTIVE`.** Authentication compares the value exactly, so a
  record registered `INACTIVE` returns `201` and then never authenticates.
- **`tenant` is the isolation scope** your calls will read and write within.
  A brand-new tenant reads an empty dataset until it has data of its own; that
  is isolation working, not a bug.
- **Legacy JWT app tokens still work** through a deprecated lookup path during
  the transition window. They cannot be converted (every one of them starts with
  the same `eyJhbGci` prefix, which the prefix-based lookup cannot key on), so
  holders must rotate onto an API key rather than be migrated.
- **`POST /admin/apps` is unauthenticated in the community build** — it is a
  single-box / development convenience. A production deployment must put an
  admin credential in front of it. Do not expose it.

### 3.2 A user credential

The `user` identity is a **Google OAuth access token**, verified by the gateway
against Google's userinfo endpoint on every request that carries one. There is
no cookie or session fallback: your users complete Google's own login/consent
flow, your client holds the resulting access token, and you present it per call.

The verified `sub` becomes the user id this API scopes by — the same value you
put in `?user_id=`.

> Enterprise deployments overlay a different `user` chain (a corp IdP) via
> configuration. The wire contract below is unchanged; only how the token is
> verified differs.

### 3.3 Access keys

Issued through `POST /admin/access-keys` (same production caveat as above). They
identify a tenant, not a person, and no `/openapi/v1` route declares the
`access_key` identity today — so they cannot call this surface. Listed here so
you stop looking for a way to use one.

---

## 4. What to send, per scenario

Credentials go in headers. Every strategy accepts `Authorization: Bearer <…>`,
and each also has a dedicated header — which you need whenever a request carries
more than one credential, since there is only one `Authorization` header to go
around.

| Identity | Dedicated header | Also accepted as |
| --- | --- | --- |
| `user` | `x-google-token: <access token>` | — (this one is header-only) |
| `app` | `x-avernet-app-token: <api key>` | `Authorization: Bearer <api key>` |
| `bot` | `x-avernet-bot-token: <token>` | `Authorization: Bearer <token>` (non-JWT only) |
| `access_key` | `x-avernet-access-key-token: <token>` | `Authorization: Bearer <token>` |

The `Authorization` fallback is shared, so **when you send both a user and an
app credential, use the dedicated headers.** That is the normal shape for a
third-party integration.

### Scenario A — a person calling for themselves

The first-party case: our own frontend, a CLI a developer runs, a script using
someone's own token.

```bash
curl 'https://<gateway-host>/openapi/v1/bots?user_id=<google-sub>&page=1&page_size=20' \
  -H 'x-google-token: <google access token>'
```

Scope: the internal default tenant. `user_id` must be the caller's own id —
naming anyone else is `403`.

### Scenario B — an application calling with a user present

Both credentials on the wire. Required for the operations that are a consent
moment or a tenant-level surface (§5, §7).

```bash
curl -X POST 'https://<gateway-host>/openapi/v1/bots/20260813_a7k2m9p1/authorized-apps?user_id=<google-sub>' \
  -H 'x-google-token: <google access token>' \
  -H 'x-avernet-app-token: <api key>'
```

Scope: the **app's** tenant, because a machine identity asserts one and a user
does not. `user_id` must still be the calling person's own id.

### Scenario C — an application calling alone, for a user

No human on the wire. The application presents only its own key and names the
user it acts for in `?user_id=`. This is the integration case, and it works only
where a **grant** exists (§7) and only on operations that admit a machine caller
(§6).

```bash
curl 'https://<gateway-host>/openapi/v1/bots/20260813_a7k2m9p1/sessions?user_id=<the-delegating-user>' \
  -H 'x-avernet-app-token: <api key>'
```

Scope: the app's tenant, and within it exactly what that user has authorized
this application to reach — nothing else, and never more than the user
themselves can reach right now.

---

## 5. Addressing an operation

Four parameters do all the work. Three of them are query parameters on every
method, including `PUT`, `POST` and `DELETE` — this surface never puts identity
in a body or in a path segment.

### `bot_id` — in the path, always

```text
/openapi/v1/bots                        the account-level collection
/openapi/v1/bots/{bot_id}               one bot
/openapi/v1/bots/{bot_id}/<component>   one component of that bot
```

The bot is the address, not an argument. `sessions`, `skills`, `routines`,
`resources`, `engine`, `identity`, `approvals`, `models`, `connection`,
`startup-script`, `authorized-apps` all hang off `{bot_id}`.

Because `{bot_id}` is a wildcard segment, the literals served in that position
are names no bot can have. Today that list is:

```text
approvals  authorized  ceiling  check-name  connection  engine  identity
loadtest   logs        mcp      models      resources   routines
sessions   skills
```

Nine of those — `approvals`, `connection`, `engine`, `identity`, `models`,
`resources`, `routines`, `sessions`, `skills` — are held only by the **retiring**
component-first addresses; once those are removed the list is `authorized`,
`ceiling`, `check-name`, `loadtest`, `logs`, `mcp`. Separately, `messages` is
reserved ahead of its routes: the gateway already serves the chat WebSocket at
`/openapi/v1/bots/messages/ws/**`, so the name is held for the HTTP endpoint
intended there.

### `?user_id=` — required almost everywhere

The end user the call acts for. Same value on every operation, same meaning on a
read as on a write.

```text
GET    /openapi/v1/bots/b-1?user_id=u-42
PUT    /openapi/v1/bots/b-1?user_id=u-42        {"bot_name": "Ada"}
DELETE /openapi/v1/bots/b-1?user_id=u-42
POST   /openapi/v1/bots/b-1/skills?user_id=u-42 <raw zip>
```

How it is authorized depends on who is calling:

- a caller **naming a person** must name themselves → anything else is `403`;
- an **application acting alone** names the user who granted it → checked
  against the grant, and a user who granted nothing is answered `404`, exactly
  as a nonexistent bot is. Guessing a `user_id` buys nothing.

Four operations take no `user_id`, because they have no user dimension:
`GET /bots/check-name`, `GET /bots/mcp/servers`, `GET /bots/mcp/servers/{server_code}`,
`GET /bots/mcp/tenants`. They still require an authenticated caller.

> **One trap.** `GET /openapi/v1/bots/logs/**` also takes `user_id`, but there it
> means *whose traces to read* over a tenant-level observability surface — a
> caller presenting both a user and an app identity may point it at someone
> else. Same spelling, opposite contract. Do not share client code between the
> two.

### `?owner_id=` — only when the bot is not yours

The owner of the bot you are addressing; defaults to the caller. You need it
only to reach a bot **shared** with you, and only on the operations that offer
it: the engine-runtime groups (`sessions`, `engine`, `models`, `approvals`,
`connection`), the two skills collection operations, and the authorization
operations.

It exists because `bot_id` alone does not identify a bot — the retired `default`
convention gave many owners a bot with the same id, so the pair `(bot_id,
owner_id)` is the real address.

Who may operate a shared bot: its **owner**, or a **collaborator at member level
or above**. Public visibility grants operation to nobody. Anyone else is answered
byte-identically to a bot that does not exist — a masked `404`, never a `403`.

### `?stage=` — which runtime you mean

`draft` (default), `verify`, or `online`.

- `draft` is the bot's own workspace, and the only runtime a personal bot has.
- `verify` and `online` exist while a publish record is live.
- A stage with no live runtime is `409 "No live runtime at the requested stage"`
  — never a silent fallback to another stage.
- **Reads serve all three stages; writes accept only `draft`.** A published
  runtime is what a release produced; `PUT …?stage=online` is
  `409 "The requested stage is read-only"` and writes nothing — not to the
  published runtime, and not to the draft as a substitute.

Taken by the engine-runtime group plus `…/engine/config` and `…/identity[/{file_type}]`.
Startup script, MCP, resources, skills and routines are draft-only today.

### Pagination

Every list endpoint takes `page` (1-based, default 1) and `page_size` (default
20, max 100), and answers `Envelope[Page[T]]` with a `total` counting all
matches.

---

## 6. What an application may reach (admission)

Every operation on this surface declares how it treats a caller with **no human
on the wire**. This is the table you need when planning an integration, because
it decides which calls Scenario C can make at all.

| Mode | Meaning | Examples |
| --- | --- | --- |
| **grant-checked, own bot** | Admitted iff a live grant covers `(app, bot, delegating user)`. The bot is always the delegating user's own. | `GET/PUT/DELETE /bots/{bot_id}`, `…/restart`, `…/status`, `…/passport`, `…/engine/config`, `…/startup-script`, `…/identity/**`, `…/resources/**`, `…/routines/**`, the four `…/skills/{skill_id}` operations |
| **grant-checked, addressed bot** | Same check, against the bot named by `(bot_id, owner_id)` — so a **shared** bot is reachable. | `…/sessions/**`, `…/engine/{status,available,capabilities}`, `…/approvals/**`, `…/models/**`, `…/connection`, `GET/POST …/skills` |
| **grant-filtered** | Admitted unconditionally; the *result* is narrowed to the granted bots. | `GET /bots`, `GET /bots/authorized` |
| **user-gated** | No bot dimension. Admitted only while the app holds at least one live grant from the named user. | `GET /bots/ceiling` |
| **open** | The answer is identical for every authenticated caller in the tenant. | `GET /bots/check-name`, `GET /bots/mcp/servers`, `…/mcp/servers/{server_code}`, `…/mcp/tenants` |
| **refused** | `401`. Also what any operation *absent* from the table gets. | `POST /bots`, all three `…/authorized-apps` operations, `…/bots/logs/**`, `…/mcp/servers/{server_code}/config`, `…/mcp/servers/{server_code}/permissions`, `…/loadtest/**` |

The reasons behind the refusals, since they shape what an integration must ask a
human to do:

- **Creating a bot** — no bot exists yet for a grant to cover, and creation
  spends the user's quota. Auto-granting the new bot would invent consent
  nobody gave.
- **Granting, listing and withdrawing authorizations** — delegation is a human
  act. An application must not be able to widen its own access, withdraw a
  competitor's, or survey what else reaches a bot.
- **Bot logs** — tenant-level observability where `user_id` means "whose traces",
  not "whose call". A grant covers a bot; it does not translate into that.
- **MCP configuration** — account-level state with no bot dimension. A grant is
  consent to reach a bot, not to reconfigure an account. (The MCP *catalogue*
  reads are a different thing and are open.)

**The invariant behind all of it:**

> An application's reach is exactly its granting user's reach, and never more.

Not a copy taken at consent time — the live thing. Every app-only request is
re-adjudicated against the same collaborator gate the human would face, so
removing the delegator from a bot ends the application's access on the next
request, with no revocation needed.

---

## 7. How an application gets authorized for a user's bot

This is the flow that turns Scenario B into Scenario C.

### The consent call

```bash
curl -X POST \
 'https://<gateway-host>/openapi/v1/bots/20260813_a7k2m9p1/authorized-apps?user_id=<user>&owner_id=<bot-owner>' \
  -H 'x-google-token: <the user’s google access token>' \
  -H 'x-avernet-app-token: <your api key>'
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

1. **Both identities are mandatory.** The user's, because they are consenting;
   the application's, because the record names it. The application is **never a
   parameter** — it is read off the verified principal — so a request cannot
   point a grant at any application but the caller. "You cannot grant to someone
   else's app" is true by construction, not by a check.
2. **The delegator must be able to operate the bot** — its owner, or a
   collaborator at member level or above. Anyone else gets the masked `404`.
   The rule is *you may delegate exactly the access you have*.
3. **`owner_id` defaults to the caller.** Omit it for your own bot; name it to
   delegate on a bot shared with you.
4. **It is idempotent.** Re-granting a live authorization returns it unchanged,
   so a partner retrying a timed-out request is not punished for one that
   actually succeeded. Two *different* users delegating the same application on
   the same bot are two separate grants, not a repeat — they are lending two
   different authorities.
5. **The record names two people.** `user_id` is the delegating user, whose
   access is lent; `owner_id` is the bot's owner, who may never have heard of
   your application. Everything the application may later do is scoped by the
   first; the second is what lets the owner keep sight of it.

### The application's view of what it may reach

```bash
curl 'https://<gateway-host>/openapi/v1/bots/authorized?user_id=<user>' \
  -H 'x-avernet-app-token: <your api key>'
```

`user` here is optional at the edge — this is the one operation an integration
calls to discover its own scope, so it works with no human on the wire.

It is also the **only complete** view: a delegated bot the user does not own
appears in no listing of that user's bots, so without this it would be
undiscoverable.

### The owner's view, and withdrawal

```bash
# which applications can reach my bot, and who let each one in
curl 'https://<gateway-host>/openapi/v1/bots/{bot_id}/authorized-apps?user_id=<user>' \
  -H 'x-google-token: <token>'

# withdraw one
curl -X DELETE 'https://<gateway-host>/openapi/v1/bots/{bot_id}/authorized-apps/4711?user_id=<user>' \
  -H 'x-google-token: <token>'
```

Both need only a user credential, deliberately: a withdrawal that required the
application's cooperation would be no withdrawal at all — that is precisely the
situation it exists for (a credential lost, rotated, or a relationship ended).

Who sees and withdraws what:

- the **owner** sees every grant standing against their bot, whoever delegated
  it, and withdraws *all* of an application's grants on that bot. Machine access
  to a bot is never invisible to the person who owns it.
- a **collaborator** sees and withdraws only their own. Theirs is the loan they
  made; a colleague's is not theirs to call in.

### Three ways access ends

1. **Explicit withdrawal**, as above.
2. **The delegator loses access to the bot** — removed as a collaborator,
   demoted below member level. The grant row is still there and is now inert:
   every request re-asks the live question, so access ends on the next call with
   nothing to clean up.
3. **The bot is deleted.** Deletion withdraws every authorization on it, before
   anything destructive happens, so a failure leaves the bot intact and
   retryable rather than leaving live authorizations against a dead bot.

### What this is not

There is no OAuth authorization-code flow, no `/authorize`, no `/token`, no
scopes, and no consent screen we host. A design for one exists
(`src/gateway/docs/2026-07-25-third-party-delegated-oauth/`) and was explicitly
kept as a **reference note, not a spec** — the shipped mechanism is the grant
record described above. Do not build a client against that document.

---

## 8. Response conventions

Every endpoint answers the same envelope, on success and on documented failure
alike:

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
- **`message`** is always English, and on failures it is deliberately coarse for
  the auth-related statuses — every `401` says exactly `"Unauthorized"`, every
  `403` says `"Forbidden"`. Telling a caller which part of a rejected credential
  to fix is telling them how to forge the next one.
- **`data`** is present but `null` on errors and on empty results.
- **`request_id`** mirrors the `X-Trace-ID` response header. **Quote it in any
  support request** — the server logs the specific reason for a refusal against
  that id and returns none of it to you.
- **List results** are `Envelope[Page[T]]`: `{"total": n, "items": [...]}`.
- **Deletes** answer `{"deleted": true}`. A failed delete is an error envelope,
  never `deleted: false`.

### Status codes

| Status | When |
| --- | --- |
| `400` | Malformed input the schema could not reject — bad log query, invalid resource path, invalid bot name, unsupported engine |
| `401` | No credential, an unverifiable one, or a caller shape this operation does not admit |
| `403` | `user_id` names a user the authenticated caller may not act for |
| `404` | Not found — **or** it exists and is not yours, **or** you are an application with no grant for it. All three are byte-identical by design |
| `409` | Conflicts with current state — name taken, quota reached, no live runtime at the stage, stage is read-only, device not ready |
| `413` | Body over a published limit (startup script 24 KiB, skill package, file preview) |
| `422` | Failed validation — a missing or malformed query parameter, most often `user_id` |
| `500` | Internal error |
| `501` | Engine-runtime only: this bot's engine does not declare the capability |
| `502` | An upstream service failed (engine, device, passport, MCP, skill storage) |
| `504` | An engine request timed out |

**`404` deserves its own note.** A masked `404` is the surface's answer to every
"you may not know this exists" case, so on a failing integration it does not
mean "wrong id". Work the list in §9.

---

## 9. When a call is refused

The response body is deliberately uninformative. Diagnose from the outside in.

### `401`

**First: is it the gateway's or the backend's?** They mean different things.

Both use the same envelope, and `code` tells them apart:

- the gateway's carries `code: 401001`, and its `message` names the identity
  that could not be resolved, e.g. `unauthenticated: no credential for user`;
- the backend's carries `code: 401000` and the fixed message `"Unauthorized"`.

A **gateway** 401 means your credential did not authenticate:

1. no credential for a required identity — check §4 for which headers this
   operation needs; the commonest miss is sending only an API key to an
   operation whose rule requires a user too;
2. a present-but-invalid credential — an expired Google token, a key that is not
   `ACTIVE`, a key from the wrong environment;
3. `Authorization: Bearer` collision — two credentials, one header. Use the
   dedicated headers.

A **backend** 401 means you authenticated but the caller shape is not admitted:

1. **an application acting alone on an operation that refuses one** — check the
   table in §6. This is the single most common cause for an integration;
2. the signed principal did not verify. That is a deployment problem, not a
   client one — see §10;
3. the request did not come through the gateway at all (the backend logs
   `no X-Avernet-Principal header on …`).

### `403`

Exactly one cause: `?user_id=` is not the id of the caller the credential names.
For a Google-authenticated caller, `user_id` must be the token's `sub`.

Note that an application acting alone **cannot** get a 403 this way — its
`user_id` is checked against the grant instead, and a bad one is a `404`.

### `404` on something you are sure exists

In order of likelihood:

1. **No grant.** An application acting alone with no live grant for
   `(app, bot, owner, user)` is answered exactly as for a nonexistent bot.
   Confirm with `GET /openapi/v1/bots/authorized`.
2. **The delegator lost access.** The grant exists; the delegating user is no
   longer the owner or a member-level collaborator. Re-adjudicated live on every
   request, so this appears with no revocation event anywhere.
3. **`owner_id` missing on a shared bot.** It defaults to the caller, so
   omitting it on someone else's bot addresses *your* bot of that id — which
   probably does not exist.
4. **Wrong tenant.** Your app's tenant is the isolation scope; a bot created in
   another tenant is not visible, period.
5. **`user_id` names the wrong person** — for an app caller, the user who
   granted you, not the bot's owner. They differ whenever the bot is shared.

### `422`

Almost always a missing or empty `user_id`. Every operation takes it except the
four catalogue reads listed in §5, and it always goes in the **query string** —
whatever the method, whatever the body.

### `409` on the engine-runtime group

- `"No live runtime at the requested stage"` — you asked for `verify` or
  `online` on a bot that has no live publish there (a personal bot never has
  one). Do not retry against another stage; there is no fallback by design.
- `"The requested stage is read-only"` — a write against a published stage.
  Nothing was written. Publishing again will not make it land; write to `draft`
  and publish.
- `"Bot device is not ready"` / `"Bot has no active device"` — the bot exists but
  its container is not up. Poll `GET …/{bot_id}/status`.

---

## 10. What must be true of the deployment

Client-side debugging stops here; these are the operator's checks.

**One shared HMAC key.** The gateway signs the principal token and the backend
verifies it with the same secret.

| Side | Where it comes from (community) |
| --- | --- |
| gateway | `AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE`, via `user_config.principal_signer.secret_name` |
| backend | `AGENTCLAW_SECRET_GATEWAY_PRINCIPAL_SIGNING_KEY_VALUE`, via `secret_names.gateway_principal_signing_key` |

There is **no fallback key on either side**, deliberately: a committed shared
secret is a committed credential. The key is resolved once at boot, so rotating
it needs a restart on both sides.

**Both sides log a fingerprint at boot** — a truncated SHA-256, safe in a log and
useless to a reader. Diagnose a key mismatch by diffing two log lines rather than
by printing a credential:

```text
backend:  gateway principal verification is configured (secret='...', key fp=eb128a7a, key len=38, aud='backend', iss='gateway')
gateway:  principal signer configured (secret='principal_signing_key', key fp=eb128a7a, key len=38, kid='bare', iss='gateway', ttl=60s)
```

| What you see | What it means |
| --- | --- |
| fingerprints differ | the two ends hold different secrets — the usual cause |
| gateway `key fp=unset` | the gateway resolved no key and cannot sign at all |
| same fp, different `key len` | one side's value carries whitespace (only appears across a mixed-version rollout) |
| fingerprints match | not a key problem — check `iss`, clock skew, and whether either process predates the last rotation |

**Missing key behaves differently by environment, and that is the point:**

| Environment | No usable key ⇒ |
| --- | --- |
| `pre` / `prod` | the process **refuses to boot**, so a rollout fails loudly instead of serving a surface that 401s while looking healthy |
| local / dev | it boots and **every `/openapi/v1` request answers 401** — these legitimately have no key |
| singlebox | resolves nothing at all; `/openapi/v1` denies there and no config knob changes that |

**Two unenforced couplings**, each of which 401s everything if broken in one
release only:

- `aud` is fixed to `backend` in backend code and signed by the gateway from the
  upstream server's name in `upstreams.servers:`. Renaming that server breaks it.
- `iss` is fixed to `gateway` in backend code and configurable at
  `user_config.principal_signer.issuer`. Changing it requires changing the
  backend constant in the same release.

**Token TTL is 60s** with 5s of clock-skew leeway. Two containers more than a
few seconds apart will reject live tokens.

**The admission table has a counterpart at the edge.** The gateway's
`route_security` decides which identities are *resolvable* for a path; the
backend's `admission.py` decides which operations admit a machine caller once it
arrives. Both must agree that a refused operation still requires a human — an
operation left open at both hops because someone edited only one is the hole the
pair exists to prevent.

**One L7 note.** A hop in front of the gateway must pass WebSocket `Upgrade`
through and impose **no** read timeout on `/openapi/v1/bots/messages/ws/**`. The
credential is checked once, at the handshake, and the connection is designed to
outlive its expiry; an idle deadline tears down healthy sockets.

---

## 11. The surface at a glance

| Group | Address | What it does |
| --- | --- | --- |
| bots | `/openapi/v1/bots`, `/openapi/v1/bots/{bot_id}` | create, list, read, update, delete, restart, name check, quota ceiling, auth-status poll, runtime status, passport, engine config, startup script |
| sessions | `…/{bot_id}/sessions` | conversation sessions and their messages |
| engine | `…/{bot_id}/engine` | runtime status, capabilities, availability |
| approvals | `…/{bot_id}/approvals` | the bot's approval mode |
| models | `…/{bot_id}/models` | models the bot's engine offers |
| connection | `…/{bot_id}/connection` | **finished socket URLs for chat** |
| skills | `…/{bot_id}/skills` | install, list, activate, deactivate, remove local skills |
| routines | `…/{bot_id}/routines` | scheduled routines, runs, and run history |
| resources | `…/{bot_id}/resources` | the bot's workspace files — list, stat, upload, download, preview, mkdir, delete |
| identity | `…/{bot_id}/identity` | the bot's identity files |
| authorized-apps | `…/{bot_id}/authorized-apps`, `…/bots/authorized` | the grant record (§7) |
| mcp | `…/bots/mcp` | MCP marketplace catalogue and per-account server config |
| logs | `…/bots/logs` | trace-level observability, across bots (user **and** app required) |
| loadtest | `…/bots/loadtest` | an echo endpoint and socket, for measuring the relay |

**Chat is not in this API.** `GET …/{bot_id}/connection` returns finished socket
URLs and your client opens the socket itself — that keeps the engine's frame
format from becoming a public contract. The socket is served by the gateway at
`/openapi/v1/bots/messages/ws/**` and relayed to the engine proxy; its credential
travels in the handshake's query string, because a browser's WebSocket API takes
a URL and a subprotocol and can attach nothing else.

### Creating a bot is two steps, sometimes

`POST /openapi/v1/bots` answers either `201 Envelope[Bot]` (done) or
`202 Envelope[BotAuthPending]` (an authorization is being issued). On `202`, poll
`POST …/{bot_id}/auth-status` — a POST because it *completes* the creation when
the authorization is issued, not a read. The `GET` spelling is the retiring one.

### Retiring addresses

Forty-two operations answer at both a new bot-first address and their old
component-first one (`…/bots/sessions/{bot_id}` and friends). **Nothing was
removed**, and the old addresses are not aliases: they publish the *old*
parameter names in the *old* places and translate, so an unmigrated client keeps
working byte-for-byte.

Two reasons to migrate anyway:

- some retiring addresses do not take `stage` and answer for `draft`
  unconditionally — including two writes, which therefore return `200` and write
  the draft where their replacements return `409` and write nothing;
- the reserved-name list shrinks when they go, freeing nine words that a bot
  cannot currently be named.

---

## 12. Checklist for a new integration

1. Register the application; store the API key (§3.1). Confirm `status` is
   `ACTIVE` and note your tenant.
2. Decide per call which scenario you are in (§4). Use the dedicated headers, not
   `Authorization`, as soon as you send two credentials.
3. Have the user grant you their bot (§7), with both credentials on the wire.
4. Confirm your scope with `GET /openapi/v1/bots/authorized`.
5. Check §6 for every operation you intend to call alone. Anything **refused**
   needs a human on the wire — design that into your product, not into a retry.
6. Send `user_id` on every call but the four catalogue reads; send `owner_id`
   whenever the bot is not the delegating user's own; send `stage` only when you
   mean a published runtime.
7. Log `request_id` from every response. It is the only handle on a refusal whose
   reason the surface will not tell you.
