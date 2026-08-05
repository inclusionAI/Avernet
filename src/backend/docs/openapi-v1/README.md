# `/openapi/v1` Public API + Tenant Isolation — Team Handoff

**English** | [简体中文](README.zh-CN.md)

_Living coordination doc for the multi-session effort to ship the public
`/openapi/v1` API. This is the shared map: what's done, what's left, who's on
what, and how the pieces fit together._

> **📌 Keep this file current — it's the whole point.**
> The moment a work item lands (a stage isolated, a category wired, a
> decision made), **update the status board and any affected section in the
> same PR that does the work.** We each read this to get the *bigger picture*
> without re-reading every other branch. A stale board is worse than none —
> it makes someone redo or collide. Rule of thumb: _if your change moved a
> checkbox, move it here too._
>
> How to update: flip the `State` cell (`⬜ TODO` → `🔧 IN PROGRESS <name>` →
> `✅ DONE — PR #___`), add a dated line to the **Changelog** at the bottom,
> and edit any prose that's now wrong. Small edits, often.

Read this alongside the deeper engineering handoff and the SDD docs in
`src/backend/specs/` — `2026-07-26-tenant-isolation-foundation/` (Track A
Stage 1, merged as PR #456) and `2026-07-27-openapi-v1-bots-track-b/` (Track B
bots, merged as PR #494). Each carries `spec.md`, `plan.md`, `tasks.md`.

For **Track C** (wrapping the bot's *engine* runtime), the endpoint-by-endpoint
ruling lives in a companion reference: **[`engine-surface.md`](engine-surface.md)**
([简体中文](engine-surface.zh-CN.md)). This README stays the single status board;
that file holds the inventory.

---

## The big picture (read this first)

**Goal:** implement the public `/openapi/v1` API, whose callers are **external
registered tenants**. It lives under
`src/backend/src/agentclaw/community/adapters/http/openapi_v1/*`. The **bots**
category is implemented (PR #494); the other six are still **route definitions
with stub handlers**.

> 🔒 **The surface is still not callable end-to-end, but no longer because of a
> stub.** `require_principal` now really verifies the gateway's signed
> `X-Avernet-Principal` token and `resolve_avernet_tenant` really reads the
> tenant out of it (see **The auth seam** below). The gateway's signing PR
> ([#599](https://github.com/inclusionAI/Avernet/pull/599)) **has merged**, so
> `dev` does forward the header — a `user` caller now round-trips end to end
> (verified against the real signer, 2026-08-02). What still stands between an
> **external tenant** and a `200` is who may call: `route_security` requires a
> `user` identity resolved by the Google chain, which a tenant presenting an
> access key cannot satisfy, and since 2026-08-02 the backend independently
> refuses any identity set that names no end user. Widening that is the
> delegation workstream, not a config line. "bots is done" still means handlers,
> contracts and tests are done.

The catch: the internal `/api/...` surface and the public `/openapi/v1` surface
share the **same tables, repositories, and services**. So a public endpoint
that returned real data would — without isolation — read the *internal*
tenant's data. That's the problem this effort exists to prevent.

The work therefore splits into **three tracks**:

- **Track A — Tenant-isolation foundation.** Make every data category
  tenant-scoped *underneath both API surfaces*, before any public endpoint is
  wired. **Track A implements NO endpoint by design** — it's plumbing.
- **Track B — Public API implementation.** Wire the seven `/openapi/v1`
  category handlers to the existing services. **This is where the endpoint/API
  code actually lands.** Each category depends on its data being isolated
  (Track A) first. **2 of 7 done: bots (PR #494), mcp (PR #610).**
- **Track C — Engine (runtime) surface.** _Added 2026-07-30._ Wrap the engine
  adapter's client-facing HTTP behind `/openapi/v1/bots/{bot_id}/…`, and replace
  the `get_device_connection` hand-off with one sanitised socket-info endpoint.
  **16 endpoints — implemented, PR #630.**

> ⚠️ **The one confusion to avoid:** "isolation Stage N is done" does **not**
> mean any API endpoint was implemented. A Track A stage is plumbing only (the
> reusable mechanism + that category's records). The API endpoints land in
> Track B — done for bots, still stubs for the other six.
>
> ⚠️ **Track C has no Track A stage, and that is correct.** Tracks A and B pair
> up (isolate a category, then wire its endpoints); Track C does not. Its data
> lives on the bot's device, not in a backend table, so there is nothing to add
> `avernet_tenant` to and **no DDL**. Isolation comes entirely from resolving
> `bot_id` through the bots guard (Stage 1 ✅) before touching the device — the
> same argument that gives `identity` no stage of its own. Don't go looking for
> a Track A stage that doesn't exist.

---

## Who's working on what

We split by **vertical slice**: each person owns a data category end-to-end —
its **Track A** isolation stage *and* its **Track B** endpoints. That way a
Track B category is never blocked on a Track A stage the *other* person owns.
(Your `mcp` example: `mcp` Track B depends on `mcp` Track A, so both go to one
owner.)

| Person | Owns (vertical slices) | Track A stages | Track B endpoint groups |
|---|---|---|---|
| **totalfrank** | bots, mcp, **skills** (shared) | 1 (bots ✅), 5 (mcp), 4 (skills, shared) | bots, mcp, skills (shared) |
| **lucas-xzp** | resources, routines, identity, **skills** (shared) | 2 (resources), 6 (routines), 4 (skills, shared) | resources, routines, identity, skills (shared) |

- **totalfrank** also owns the **reusable Track A mechanism** (built in Stage 1 /
  PR #456) — the pattern every other stage copies.
- **skills is co-owned by both** (third tier, but the trickiest — see the note
  in its endpoint table). Split its Track A stage and endpoints between you and
  agree a shared sub-plan before starting.
- **identity** (Track B only) has no Track A stage of its own: its data is a bot
  sub-resource, so it's already scoped by **bots isolation (Stage 1 ✅)**. It's
  assigned to **lucas-xzp** for balance; its one dependency is already satisfied,
  so this creates no cross-person block.

### Priority tiers (what to pick up first)

| Tier | Categories | Owners |
|---|---|---|
| **P1 — first** | bots, mcp, resources, routines | bots + mcp → totalfrank; resources + routines → lucas-xzp |
| **P2 — second** | identity | identity → lucas-xzp |
| **P3 — third** | skills | **co-owned** (totalfrank + lucas-xzp) — the most involved category |

Within each lane, do your **P1** slices before P2 before P3. Skills (P3) is the
shared, complex one — tackle it together once the P1/P2 work is moving.

> **Shared gate — LIFTED 2026-07-27.** Anything touching bots waited on
> **PR #456**; it merged, so that one-time gate is gone. Both owners can run
> their slices in parallel now.

_See **Endpoints per component** below for exactly which endpoints each slice
must implement._

---

## Status board (update as work lands)

### Track A — Tenant-isolation foundation
| Stage | Scope (data) | Owner | Pri | State | Done-when |
|---|---|---|---|---|---|
| 1 | Bot records (`ac_bots` / `BotModel`) | totalfrank | P1 | ✅ **DONE — PR #456 merged 2026-07-27** | — |
| 2 | Resources (`ac_resource`) | lucas-xzp | P1 | ✅ DONE — Phase 0 (branch `rongzhi_0727`) | column + guards + tests green; internal API unchanged — verified: `to_dict()` excludes tenant, guard uses direct expression not lambda |
| 3 | Channels (`ac_channel_config`) | — | ❌ **DROPPED** | Stage never started; its Track B component was removed 2026-08-03 | n/a |
| 4 | Skills (skill tables) | totalfrank + lucas-xzp | P3 | ⬜ TODO | same |
| 5 | MCP configuration (`ac_user_mcp_config` + `ac_bot_mcp_call_config`) | totalfrank | P1 | ✅ DONE — **PR #564** | PR #564 merges |
| 6 | Routines | lucas-xzp | P1 | ⬜ TODO | same |

> Stage 1 also builds the **reusable mechanism** (see below) that every later
> stage copies. It's the foundation, not just "bots."

### Track B — Public API implementation (where the endpoints land — 2 of 7 done)
_Ordered by priority tier._
| Category | Owner | Pri | Router | State | Depends on |
|---|---|---|---|---|---|
| bots | totalfrank | P1 | `openapi_v1/bots/router.py` | ✅ **DONE — PR #494 merged 2026-07-29** (13/13 endpoints) | ~~Track A stage 1~~ ✅ |
| mcp | totalfrank | P1 | `openapi_v1/mcp/router.py` | ✅ **DONE — PR #610** (6/6 endpoints) | ~~Track A stage 5~~ ✅ (PR #564) |
| resources | lucas-xzp | P1 | `openapi_v1/resources/router.py` | 🔧 IN PROGRESS (PARTIAL) — 9 handlers all wired but DEFINITION-ONLY / NOT PUBLIC-READY | Track A resources ✅(Phase 0); Track B all 9 endpoints wired stub→service; gated on auth workstream (gateway principal seam) + DDL deploy before public exposure |
| routines | lucas-xzp | P1 | `openapi_v1/routines/router.py` *(stub)* | ⬜ TODO | Track A routines (lucas-xzp) |
| channels | — | ❌ **REMOVED (2026-08-03)** | *(deleted)* | Router, schemas and both published paths deleted — see the channels section below | n/a |
| identity | lucas-xzp | P2 | `openapi_v1/identity/router.py` *(stub)* | ⬜ TODO | bots isolation (Stage 1 ✅) |
| skills | totalfrank + lucas-xzp | P3 | `openapi_v1/skills/router.py` *(stub)* | ⬜ TODO | Track A skills (shared) |

### Track C — Engine (runtime) surface (5 of 5 groups implemented — PR #630)
_All groups depend only on **bots isolation (Stage 1 ✅)** — no Track A stage, no
DDL. Full ruling and per-endpoint mapping in
**[`engine-surface.md`](engine-surface.md)**._

| Group | Endpoints | Owner | Pri | Router | State |
|---|---|---|---|---|---|
| sessions | 7 | ⬜ unassigned | P1 | `openapi_v1/engine_runtime/sessions/` | ✅ **IMPLEMENTED — PR #630** (personal bots only; `service` → 501) |
| engine (read-only) | 3 | ⬜ unassigned | P1 | `openapi_v1/engine_runtime/engine/` | ✅ **IMPLEMENTED — PR #630** |
| connection | 1 | ⬜ unassigned | P1 | `openapi_v1/engine_runtime/connection/` | ✅ **IMPLEMENTED — PR #630** |
| approvals | 3 | ⬜ unassigned | P2 | `openapi_v1/engine_runtime/approvals/` | ✅ **IMPLEMENTED — PR #630** |
| models | 2 | ⬜ unassigned | P2 | `openapi_v1/engine_runtime/models/` | ✅ **IMPLEMENTED — PR #630** |

> **Scope rule (why only these).** Wrap engine HTTP the frontend reaches
> **directly** through proxypass (`src/frontend/src/requestConfig.ts:189-205`).
> Engine routes the frontend reaches **via the backend** — `/api/cron` (already
> the `routines` category), `/api/file`, `/api/skills`, `/api/mcp`,
> `/api/resource-materializations`, `/api/bash`, `/api/bot/config`,
> `/api/work-items` — are already fronted by a backend contract and stay out.
> AICoding-only routes stay out. **WebSockets are not wrapped**: the new
> `…/connection` endpoint returns one complete socket URL, credential included,
> and the caller builds the connection itself.
>
> `engine/switch` and `engine/restart` are deliberately excluded — wrapping
> `switch` would be a back door around #494's `engine`-immutability ruling on
> `PUT /openapi/v1/bots/{bot_id}`, and `restart` would give one bot two restart
> verbs. `session-favorites` and the `/api/openclaw` HTTP trio are **deferred,
> not cancelled** (both additive later). Reasons in `engine-surface.md`.
>
> **Routines is Track C's worked precedent, not a Track B one.** Backend
> `/api/cron` → `CronRelayService` → `DeviceAdapterTransport` → engine has been
> the shape in production all along, and `openapi_v1/routines/router.py:29`
> already imports `CronRelayServiceProtocol`. Read it before writing a handler.

### Cross-cutting (not per-stage)
| Item | State | Note |
|---|---|---|
| Real caller-identity verifier (auth workstream) | ✅ **DONE both halves** — backend PR [#634](https://github.com/inclusionAI/Avernet/pull/634), gateway PR [#599](https://github.com/inclusionAI/Avernet/pull/599) **merged** | `require_principal` + `resolve_avernet_tenant` verify the gateway's signed `X-Avernet-Principal` (HS256, `aud=backend`) and read tenant + owner from it. The wire contract was checked by round-tripping the **real** gateway signer into the **real** backend verifier (2026-08-02): user/bot/app/access_key shapes, secret non-projection, `aud`/`iss` refusal. **A `user` caller works end to end.** What remains is *which* callers are admitted — see the identity-admission row below |
| **Identity admission: `user` only** | ✅ **DONE 2026-08-02** | `verify_principal_token` refuses an identity set naming no end user, so `bot` / `app` / `access_key` callers get `401` by design rather than by whether a handler asks for the owner. Widening it is delegation (auth design §15), not config. SDD: `specs/2026-08-02-public-api-user-only-principal/` |
| **No cross-repo test pins the principal wire shape** | ⬜ TODO | Both sides are tested against their own hand-written idea of the payload (`test_verifier.py` builds dicts; the gateway tests its own models). Renaming a field on one side leaves both suites green and 401s production |
| Tenant-leading indexes (F2, **MANDATORY** policy) | ⬜ TODO | before multi-tenant go-live |
| Background/scheduled work revisit | ⬜ TODO | before a 2nd tenant holds real data |
| **Bot identity keys collide across tenants** ([#556](https://github.com/inclusionAI/Avernet/issues/556)) | ⬜ TODO (totalfrank) | Passport, auth relationships, BCN, policy row are keyed on `bot_id`/`owner_id` with no tenant axis, and every owner's first bot is literally `"default"`. **Should gate enabling multi-tenancy.** Stopgapped in #494 by `sync_to_bcn=False` on the public update path |
| Async create ≠ authorized bot ([#559](https://github.com/inclusionAI/Avernet/issues/559)) | ⬜ TODO (totalfrank) | the pending create spec is never persisted; completion rebuilds it from the polling request. Pre-existing on `dev`; latent (community Passport always issues) |
| Swallowed external identity writes ([#560](https://github.com/inclusionAI/Avernet/issues/560)) | ⬜ TODO (totalfrank) | owner-grant on create and Passport metadata on update log-and-continue, against `AGENTS.md:203-204`. One ruling settles both sites; recommendation is *report partial success* |

> The three issues above came out of #494's review and are **pre-existing on
> `dev`**, not regressions — they're recorded here because they are decisions
> the whole effort inherits, not bots-only bugs. #556 in particular is the one
> that must be settled before a second tenant holds real data.
| **Stage 5 unique-key swap on `ac_user_mcp_config`** | ⬜ TODO (DDL below) | **before a 2nd tenant writes MCP config** — not before deploy |

> **❌ Channels removed (2026-08-03).** Parked since 2026-07-29 as a
> deprioritization; now deleted outright. Parking was the wrong shape for it:
> the component was *published*, so a parked stub was not a dormant row on a
> board — it was six operations in the document the gateway serves, each
> answering 500. The Track A stage was never started, so there is no data work
> to unwind. See the channels section under **Endpoints** for what was removed.

---

## Schema changes applied out-of-band (no migration files in-repo)

Per the standing decision, tenant-isolation schema changes are applied on the
platform out of band, so **these statements are the authoritative record**.
Hand them to whoever applies DDL together with the ordering notes.

**Stage 1 — `ac_bots`** (already applied):

```sql
ALTER TABLE ac_bots
  ADD COLUMN avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw'
    COMMENT 'data-isolation tenant; existing rows are the internal teamclaw tenant';
```

**Stage 5 — MCP configuration** (PR #564). Three statements, **two different
deadlines**:

```sql
-- 1. Column adds. MUST land BEFORE the code deploy: a SELECT naming a column
--    that does not exist fails outright, so a code-first deploy takes MCP
--    config reads down. NOT NULL DEFAULT backfills existing rows in place and
--    is inert against currently-deployed code, so DDL-first is safe.
ALTER TABLE ac_user_mcp_config
  ADD COLUMN avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw'
    COMMENT 'data-isolation tenant; existing rows are the internal teamclaw tenant';

ALTER TABLE ac_bot_mcp_call_config
  ADD COLUMN avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw'
    COMMENT 'data-isolation tenant; existing rows are the internal teamclaw tenant';

-- 2. Unique-key swap. NOT required before the code deploy — with one tenant
--    the old key and the new one accept exactly the same rows. It becomes
--    load-bearing the moment a SECOND TENANT WRITES MCP config, because
--    (user_id, server_code, env) rejects a second tenant's row for a user id
--    it shares — a duplicate-key error against a row it cannot see.
--    Create-before-drop so uniqueness is never unenforced. Adding a leading
--    column only loosens a unique key, so every existing row stays valid.
ALTER TABLE ac_user_mcp_config
  ADD UNIQUE KEY uix_user_mcp_config_tenant
    (avernet_tenant, user_id, server_code, env) GLOBAL;
ALTER TABLE ac_user_mcp_config
  DROP INDEX uix_user_mcp_config;
```

`ac_bot_mcp_call_config` needs **no** key change: its key
`(bot_pk, server_code, engine_type, env)` leads with `ac_bots.id`, a global
primary key, so the tenant is already functionally determined and the
collision above is not representable.

Local and singlebox runtimes need no DDL — `Base.metadata.create_all` builds
both tables from the models.

> **Sequencing decision — DECIDED 2026-07-27:** per-category **vertical slices**.
> Each owner isolates a category (Track A) then implements its endpoints
> (Track B) back-to-back, rather than finishing all of Track A before any
> Track B. This is precisely what keeps the two of us from blocking each other.

---

## Track A — the reusable mechanism (built in Stage 1, PR #456)

Category-agnostic; reuse as-is. These files are **on `dev`** (PR #456):

- `utils/avernet_tenant.py` — per-request tenant carrier.
  `DEFAULT_AVERNET_TENANT = "teamclaw"` (internal tenant; owns all current
  data; **never hand it to an external tenant**). `get_current_avernet_tenant()`
  (total), `avernet_tenant_scope()` (set + guaranteed reset),
  `bind_current_avernet_tenant(fn)` (carry tenant into a raw
  `threading.Thread`/`ThreadPoolExecutor` target — `asyncio.to_thread`/
  `create_task` already copy context, so they need nothing).
- `utils/avernet_tenant_guard.py` — the **guard pattern**, model-agnostic since
  Stage 5. A model opts in with `register_avernet_tenant_guard(Model)` placed
  immediately after the class; the model must declare
  `avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")`.
  - `do_orm_execute` **read guard** on the `Session` class, installed once →
    appends `with_loader_criteria(Model, avernet_tenant ==
    get_current_avernet_tenant(), include_aliases=True)` **per registered
    model**; skips column/relationship loads + a `skip_avernet_tenant_guard`
    option. Also constrains `Query.update()`/`Query.delete()`, so writes need
    no filter. An option naming a model the statement does not touch is a
    no-op — that is what makes one listener safe for N models.
  - `before_insert` **insert guard** per model → stamp when unset, raise
    `CrossTenantInsertError` on an explicit conflicting tenant.
  - `register_avernet_tenant_guard` validates against the **mapper's columns**,
    not `hasattr`: a model declaring `avernet_tenant` as a plain value would
    otherwise register and the guard would emit `WHERE 1 = 1` — a silent, total
    bypass.
  - registration is idempotent per model; `guarded_models()` exposes the
    registry for tests and diagnostics.
  - Stage 1 built this welded to `BotModel` inside `plugin_api/models.py`;
    Stage 5 lifted it out so `core/` models can register without `plugin_api`
    importing them. `plugin_api/models.py` re-exports `CrossTenantInsertError`.
- `adapters/http/middleware.py` — `AvernetTenantMiddleware`, a **pure ASGI**
  middleware (NOT `BaseHTTPMiddleware` — ContextVar robustness). Sets each
  request's tenant. **Covers every request already; Track A stage 2+ does not
  touch it.**
- `adapters/http/openapi_v1/dependencies.py` — `resolve_avernet_tenant(request)`:
  the single seam. Category-agnostic. **No longer a stub** — it and
  `require_principal` both read the verified gateway principal; see **The auth
  seam** below. The owner-side seam on top of the latter is
  `openapi_v1/principal.py::caller_owner_id`.

All paths are under
`src/backend/src/agentclaw/community/`.

---

## The auth seam — how a caller becomes a tenant + an owner

Both public seams read **one** header and verification happens **once** per
request. SDD: `src/backend/specs/2026-07-30-gateway-principal-verifier/`.

```
gateway            verifies credentials → resolves identity set → signs it
  │                (HS256, aud = the upstream's name, TTL 60s, principals[])
  ▼  X-Avernet-Principal
AvernetTenantMiddleware → resolve_avernet_tenant(request)  ─┐
                                                            ├─ verify once,
route dependency        → require_principal(request)       ─┘  cache on scope
                             │
                             └→ caller_owner_id(principal) → owner-scoped calls
```

- `core/gateway_principal/` — the verifier and **our** DTOs for the wire shape.
  The backend never imports gateway types (Rule 7 / §9); it projects.
- `utils/gateway_principal_config.py` — resolves the shared key through
  `SecretResolver` under `SecretNamesConfig.gateway_principal_signing_key`.
  That name **defaults**, so a deployment configures only the *value*: the corp
  secret store (corp overlays also override the name),
  or `AGENTCLAW_SECRET_GATEWAY_PRINCIPAL_SIGNING_KEY_VALUE` (community).
  **Singlebox resolves nothing** — no secret store, no local stand-in — so
  `/openapi/v1` denies there and no config knob changes that; giving singlebox a
  key is a deliberate change, not a config line. There is no dev fallback key on
  this side on purpose: a committed shared secret is a committed credential. The
  key is resolved once at boot, so rotating it needs a restart on both sides.

  **An unresolvable key behaves differently by environment**, and the
  difference is the whole point:

  | Environment | No usable key ⇒ |
  | --- | --- |
  | `pre` / `prod` | **the process refuses to boot** — `init_principal_verifier_config` raises, so a rollout fails loudly instead of serving a surface that 401s while looking healthy |
  | local / dev / singlebox | **every `/openapi/v1` request answers 401** — these legitimately have no key, so they stay bootable and deny instead |

  Either way the trigger is the same: no such secret, an empty value, or a
  resolver that raises.

  **The gateway now behaves identically** — one contract, one rule. It shipped a
  committed dev fallback key until 2026-08-04; that was removed rather than made
  louder, because no peer ever accepted those tokens (this side has never had a
  fallback to match) and all it bought was a gateway that looked healthy while
  every request failed one hop away. Missing key there ⇒ boot refused in
  `pre`/`prod`, otherwise every signature attempt refuses and the forwarder
  answers `500 principal signing failed`.

### Diagnosing a 401 on this surface

Both halves log a **key fingerprint** at boot — a truncated SHA-256, safe in a
log, useless to a reader. They are the same eight characters when and only when
the two ends hold the same secret, so the first question is answered by diffing
two lines rather than by printing a credential:

```text
backend:  gateway principal verification is configured (secret='...', key fp=eb128a7a, key len=38, aud='backend', iss='gateway')
gateway:  principal signer configured (secret='principal_signing_key', key fp=eb128a7a, key len=38, kid='bare', iss='gateway', ttl=60s)
```

| What you see | What it means |
| --- | --- |
| fingerprints differ | the two ends hold different secrets — the usual cause |
| gateway `key fp=unset` | the gateway resolved no key and cannot sign at all |
| same fp, different `key len` | one side's value carries whitespace (both strip, so this only appears across a mixed-version rollout) |
| fingerprints match | not a key problem — check `iss`, clock skew, and whether either process predates the last rotation (each resolves once at boot) |

Per-request, the backend logs one line per failure, and it names the cause:

```text
rejected forwarded principal on GET /openapi/v1/bots: principal token rejected:
Signature verification failed [verifier key fp=eb128a7a, expects aud='backend'
iss='gateway'; unverified caller-supplied header alg='HS256' kid='bare']
```

**The two halves of that suffix do not carry equal weight, and the difference
decides what you should do about it.**

`verifier key fp`, `aud` and `iss` come from this process's own configuration.
They are trustworthy, and together with the gateway's boot line they are what
a diagnosis should rest on.

`alg` and `kid` come from the token's JOSE header, which a failed signature
means nothing has authenticated. Anyone who can reach the surface can stamp
`kid: bare` on a token they minted. So `kid='bare'` is **not** evidence the
gateway sent it — treating it as such during a burst of forged traffic would
have you rotate a shared secret that was never broken. Read it as a hint that
is useful mainly in the negative: an unexpected `alg`, an unfamiliar `kid`, or
a header that will not parse says *look somewhere other than the key*.

When the fingerprints match and forged-looking traffic persists, the token is
not coming from your gateway, whatever its `kid` claims. None of this reaches
the caller: every failure answers the same fixed `401 Unauthorized`.

A **missing** header logs distinctly (`no X-Avernet-Principal header on ...`)
and is not an auth failure at all — the gateway injects that header on every
forwarded request, so its absence means the request did not come through the
gateway's authenticated path. Chasing signing keys for that one is chasing the
wrong half of the system.

- `aud` and `iss` are fixed in code here, not configurable — one wire contract,
  one spelling. They are **not** symmetric on the signing side, though:
  - `aud` is not configurable there either (the gateway signs it from the
    upstream server's own name), so a knob here could only break the contract.
  - `iss` **is** configurable there — `user_config.principal_signer.issuer`,
    since gateway #673. Its default is `gateway`, which this constant matches,
    so the contract holds as shipped. **Changing the gateway's `issuer`
    requires changing the backend constant in the same release**, or every
    request 401s. Same unenforced coupling as the `aud` ↔ `servers:` name.
- What gets rejected, all as an identical `401`: bad signature, `alg: none`, an
  `aud` for another upstream, wrong `iss`, expired, a missing required claim, an
  unknown `type` tag, a renamed contract field, an identity set that disagrees
  about its tenant, **a tenant claiming to be `teamclaw`** (that one would
  hand an external caller every internal row), and **an identity set naming no
  end user** (see below).
- The gateway's tenant id **is** the `avernet_tenant` value — no mapping table.
  So a real external tenant reads an empty dataset until it has data; that is
  isolation working, not a bug.

**Two things you inherit if you own a Track B category:**

1. **Only a `user` caller is admitted — `bot`, `app` and `access_key` are refused
   at verification.** _Decided 2026-08-02._ Owner id is derived only from a
   `user` principal. `app.owners` is free-text org attribution and the
   access-key registry has no owner column, so neither names a person to scope
   by; `bot` does carry `owner_id`, but letting a bot act as its owner across
   the whole public contract is a grant nobody made. **The refusal happens in
   `verify_principal_token`, not in `caller_owner_id`** — that is the load-bearing
   part. A per-handler refusal only covers handlers that ask for the owner, and
   four in `resources/router.py` don't; refusing the identity set means an
   unscopeable caller cannot reach *any* route, present or future. What an `app`
   / `access_key` caller should own is still unsettled (auth design §14 Q4);
   delegation (§15) is the designed way to widen this. SDD:
   `src/backend/specs/2026-08-02-public-api-user-only-principal/`.
2. **A mapped error raised in a dependency is now enveloped too.** `@envelope_errors`
   only wraps the handler, so the seam's 401 (raised in a dependency) escaped it;
   the lookup now lives in `responses.py::mapped_error_response` and the app's
   catch-all consults the same table. If you add a dependency that raises a
   domain error, it already answers in the envelope.

---

## Recipe — extend Track A to a new data category (e.g. resources)

1. Find the model(s)/table(s) (e.g. `ResourceModel`, `ac_resource`) and every
   module that queries them (grep the model class) — the Session-class read
   guard already covers all of them.
2. Add `avernet_tenant = Column(String(64), nullable=False,
   server_default="teamclaw")` to each model; keep it **OUT of `to_dict()`**.
3. Register the two guards for the model, following the `BotModel` block in
   `plugin_api/models.py`. Multiple models → extend the one `do_orm_execute`
   listener with more `with_loader_criteria` options + a `before_insert` per
   mapped class (don't add N Session listeners).
4. DDL (out-of-band, no migration file): `ALTER TABLE <table> ADD COLUMN
   avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw';` — **no index** (F2).
   `NOT NULL DEFAULT 'teamclaw'` backfills existing rows. **Apply DDL before the
   reading code deploys.**
5. In-request thread audit: grep the category's services for raw
   `threading.Thread`/`ThreadPoolExecutor`/bare `run_in_executor` whose body
   touches the model; wrap the target with `bind_current_avernet_tenant`.
6. Tests (copy the Stage-1 shape): red→green cross-tenant isolation across the
   read methods; cross-tenant update/delete no-ops; bare
   `session.query(Model).all()` filtered; insert stamps tenant + conflicting
   insert raises; `to_dict()` key set unchanged; existing internal suite passes
   **unmodified**.
7. Arch boundary: any new cross-module import (e.g. `utils.avernet_tenant` into
   a module that didn't have it) must be added to that module's `README.md`
   `## Context Boundary`; then run `tests/community/architecture/`.

**Stage done-when:** step-6 checkboxes green, internal suite unmodified-and-green,
CI all-green. Then **update the status board above.**

---

## Track B — the reusable primitives (built with bots, PR #494)

**Read this before starting any category.** The bots slice built the shared
public-API layer once; the remaining six categories are meant to *use* it, not
rebuild it. Everything below is category-agnostic and lives in
`adapters/http/openapi_v1/`:

- **`responses.py`** — the envelope builders (`envelope`, `page`, `created`,
  `accepted`, `deleted`) and the `@envelope_errors` decorator. The decorator
  maps domain errors to enveloped responses via `ENVELOPE_ERRORS`, a
  `{exception type: (http status, fixed message)}` dict. The rules it enforces,
  all of which your category inherits:
  - **Messages are fixed, never `str(exc)`** — internal ids and internal-language
    text must not reach an external caller.
  - **Both 404 paths are byte-identical** ("not found" vs "exists but not
    yours / other tenant"), so a caller cannot probe for existence.
  - Order matters: **list specific leaf errors before their base class**; lookup
    returns on the first `isinstance` match in insertion order.
  - Add *your* category's errors to `ENVELOPE_ERRORS`. Anything unmapped escapes
    to the app 500 handler — which now envelopes it too, but with a generic
    message.
- **`contracts.py`** — `Envelope[T]` / `Page[T]` / `Deleted` / `NameCheck` plus
  `ErrorEnvelope` and `ERROR_RESPONSES`. `ERROR_RESPONSES` is attached **once**
  in `openapi_v1/__init__.py::build_public_router()`, so every route on every
  group documents the real failure shape in the generated schema. You get this
  for free; don't re-declare it per handler.
- **`principal.py::caller_owner_id(principal)`** — the single seam that turns the
  `require_principal` value into the caller's owner id. **Scope every service
  call with it.** Tenant confines data to the tenant; owner id confines it to
  the caller. Both are needed.
- **`clusters.py`** — the public `cluster_name` enum (`ACRA` / `ANDC`) in strict
  bijection with the engine (`ANDC` ⟺ `teclaw`, `ACRA` ⟺ everything else),
  derived on read and validated on create. Reuse if your category exposes a
  cluster; don't invent a second mapping.
- **`errors.py`** — dependency-free public error types (`MissingPrincipalError`,
  `ClusterMismatchError`, `UnsupportedEngineError`) so the schema / cluster modules
  can raise without importing the service layer.
- **`PUBLIC_API_PREFIX`** + the app-level handlers in `adapters/http/app.py` —
  `RequestValidationError`, `DomainError`, `StarletteHTTPException` and the
  catch-all are all path-scoped to the public prefix, so a failure raised
  *before* or *outside* a handler (unknown path, wrong method, body validation)
  still answers with the envelope. Internal `/api` routes keep FastAPI's
  `{"detail": ...}`. **This is closed structurally — you don't need to add
  anything per category.**

### Recipe — implement a category's endpoints

1. Land that category's **Track A stage first** (see the Track A recipe above).
   Without it a correct handler still reads the internal tenant's rows.
2. Replace the stub handlers in `openapi_v1/<category>/router.py` with real ones
   that call the existing services. Depend on `require_principal`, take
   `request: Request` (the `@envelope_errors` decorator needs it for
   `request_id`), and scope every call with `caller_owner_id(principal)`.
3. Return `Envelope`/`Page` via the `responses.py` builders. Binary streams
   (e.g. resource download) bypass the envelope — that's the one exception.
4. Add your domain errors to `ENVELOPE_ERRORS` with fixed public messages.
5. Put `extra="forbid"` on public request models. An unknown or immutable field
   should be a 422, not a silent no-op — that's how `engine` is rejected on bot
   update.
6. **If a behavior is shared with the internal `/api` surface, extract it into
   `core/` and call it from both** rather than copying. #494 did this for the
   create + Passport orchestration (`core/bot_management/create_flow.py`) and
   the readiness policy (`core/bot_management/readiness.py`) — otherwise the two
   surfaces answer the same question differently within a release.
7. Tests: unit (response builders / mapping), endpoint (all handlers, success +
   each mapped error), and **cross-tenant isolation against the real Track A
   guard** (a foreign `{id}` must be a masked 404). Keep the internal suite
   unmodified and green.
8. Own SDD (`spec.md`/`plan.md`/`tasks.md`) and own PR per category. Use
   `src/backend/specs/2026-07-27-openapi-v1-bots-track-b/` +
   `openapi_v1/bots/router.py` as the worked reference, and
   `src/backend/specs/2026-07-30-openapi-v1-mcp-track-b/` +
   `openapi_v1/mcp/router.py` for the second — the pattern for a category that
   **extracts shared logic** out of a live internal router (recipe step 6) into
   `core/mcp/` and proves the extraction by leaving the internal suite unmodified.

> **Architecture gate:** `tests/community/architecture/` now also runs
> `test_service_api_conformance.py` — the Service API gate that `api/README.md`
> had promised in two places but that was never written. If you give a Protocol
> in `api/` real signatures, register its `(Protocol, ConcreteService)` pair
> there.

---

## Endpoints per component (what each slice must implement)

The tables below are the **per-component endpoint checklists** for Track B —
who owns them, and exactly what lands. Source of truth is the **served router**
(`openapi_v1/<category>/router.py` — implemented for bots, stubs carrying the
route definitions for the rest); descriptions are cross-checked against the v1
contract overview in **PR #363** (`docs/api-endpoints.zh-CN.md`, a Chinese
endpoint reference by totalfrank — still open/draft as of 2026-07-29; kept here
as reference).

## Addressing rule

**Every operation is addressed `/openapi/v1/bots/<component>/…`.** The
component's **literal** name comes first; a bot-scoped operation takes
`{bot_id}` as the first segment *after* it — never before it, and never with a
`/bot/` segment in between.

```text
/openapi/v1/bots/<component>            # the component's own collection
/openapi/v1/bots/<component>/{bot_id}   # …scoped to one bot
```

The `bots` component is the one exception, and only because it *is* the
component the base names: it owns `/openapi/v1/bots` and
`/openapi/v1/bots/{bot_id}`, and its own sub-resources (`/status`, `/passport`,
`/restart`, `/auth-status`, `/engine-config`) hang off the bot record beneath
it. Those are properties of the bot, not other components borrowing the bot's
address.

**Why.** Three components used to break this — `identity` carried a redundant
`/bot/` segment, and `connection`/`engine`/`approvals`/`sessions`/`models`/
`skills` put `{bot_id}` *before* their own name. That made a router file unable
to state its own address (a reader of `engine_runtime/sessions/router.py` could
not tell whether `/openapi/v1/bots/{bot_id}/sessions` was served there or by a
`{bot_id}`-shaped route in the bots component), and it blocked a second owner
under the same base — the reason BCS moved its own control plane to
`/openapi/v1/bots/collaboration/{bot_id}`
(`src/bcs/docs/plans/2026-08-03-bcn-collaboration-paths-design.md`). Normalized
in the `2026-08-03-openapi-v1-path-normalization` spec; a test
(`tests/…/openapi_v1/test_path_convention.py`) asserts the rule against the
generated document, so a route that breaks it fails there rather than in review.

**Reserved names.** Because the `bots` component keeps the bare
`/openapi/v1/bots/{bot_id}`, a bot whose id equals a component name is
unreachable at that address. The set is fixed, and the same test asserts this
list still equals the literals the routes actually publish:

<!-- reserved-component-names -->
```text
approvals  ceiling  check-name  connection  engine  identity  logs
mcp  models  resources  routines  sessions  skills
```

**Reserved ahead of their routes.** A second, separate list — names claimed here
before any route publishes them. They are *not* reserved for the reason above:
no route serves them, so nothing is currently unreachable at those addresses.
They are reserved because something else already occupies the address and a
component is intended there, so a bot id must not be allowed to take it in the
meantime.

<!-- reserved-component-names-unrouted -->
```text
messages
```

- `messages` — the gateway serves the bot's chat WebSocket at
  `/openapi/v1/bots/messages/ws/**`, relayed to the engine proxy
  (`src/gateway/configs/application.yaml`). That claim is on the **socket plane
  only**, so an HTTP request to the address still reaches this service; the name
  is held for the HTTP endpoint intended there. See
  `src/gateway/specs/2026-08-03-gateway-path-specific-domain-routing/`.

A name in this list must move to the routed list above the moment a route
publishes it — the convention test asserts the two lists stay disjoint, so
adding the route without moving the name fails there rather than in review.

> **Mount order is load-bearing.** `build_public_router()` includes the literal
> sub-groups **before** the bots group, so `/openapi/v1/bots/resources` resolves
> ahead of the `/openapi/v1/bots/{bot_id}` wildcard. Only the components that
> serve a single-segment collection root (`resources`, `routines`, plus the
> bots-owned `check-name`/`ceiling`) actually depend on it now — every other
> component is reachable only at two segments or more — but keep any new group
> in the `_SUBGROUPS` list, above the bots router, rather than reasoning about
> the exception each time.

All responses use the `Envelope[T]` / `Page[T]` shapes from
`openapi_v1/contracts.py` unless noted (binary streams bypass the envelope).

### ✅ totalfrank · P1 — bots (13 endpoints) · `openapi_v1/bots/router.py` — **IMPLEMENTED (PR #494)**
All 13 wired to the internal bot services. Kept here as the reference shape for
the other six: this is what "done" looks like per category.

| Method | Path | Purpose | Success |
|---|---|---|---|
| POST | `/openapi/v1/bots` | Create a bot; may need Passport authorization | `201 Envelope[Bot]` or `202 Envelope[BotAuthPending]` |
| GET | `/openapi/v1/bots` | List caller's bots (`keyword`, `engine`, `status`, paged) | `Envelope[Page[Bot]]` |
| GET | `/openapi/v1/bots/check-name` | Bot-name availability (`name`) | `Envelope[NameCheck]` |
| GET | `/openapi/v1/bots/ceiling` | Bot-creation quota ceiling | `Envelope[Ceiling]` |
| GET | `/openapi/v1/bots/{bot_id}` | Bot details | `Envelope[Bot]` |
| PUT | `/openapi/v1/bots/{bot_id}` | Update bot (engine immutable) | `Envelope[Bot]` |
| DELETE | `/openapi/v1/bots/{bot_id}` | Delete bot | `Envelope[Deleted]` |
| POST | `/openapi/v1/bots/{bot_id}/restart` | Restart (re-provision device) | `Envelope[Bot]` |
| GET | `/openapi/v1/bots/{bot_id}/auth-status` | Poll Passport auth; completes creation when ISSUED | `Envelope[BotAuthStatus]` |
| GET | `/openapi/v1/bots/{bot_id}/status` | Runtime / device readiness | `Envelope[BotStatus]` |
| GET | `/openapi/v1/bots/{bot_id}/passport` | Get the bot's Agent Passport | `Envelope[Passport]` |
| GET | `/openapi/v1/bots/{bot_id}/engine-config` | Read engine config (free-form JSON) | `Envelope[dict]` |
| PUT | `/openapi/v1/bots/{bot_id}/engine-config` | Write engine config (free-form JSON) | `Envelope[dict]` |

_Deliberately **not** exposed on bots: `engine_options` on create (nothing
downstream reads `BotCreateSpec.extra_properties` yet, so advertising it would
promise something the server ignores), and `cluster_name`/`engine_options` on
update. With `extra="forbid"` these are now a 422 rather than a silent drop._

_Internal `/api/bots` changed too, all intentional and covered by #494: the
create preflight also rejects a taken bot name (so a duplicate fails **before**
the external Passport application); create persists the configured engine
registry widened to include the bot's own active engine; update's duplicate-name
check compares owner **and** `bot_id` together; deleting the default bot raises
`BotOperationNotAllowedError` (internal response shape unchanged, public → 409)._

### ❌ channels — **REMOVED (2026-08-03)**
The component is deleted: router, schemas, package, mounted entry and its two
published paths. It was never implemented, and unlike an unwritten component it
was **published** — an integrator reading the served document saw a channels API
and got a 500 on every call. Parking it kept that cost with none of the benefit.

Nothing was lost that a re-add would need: the six operations (DingTalk config
CRUD + a status toggle) are recorded above in the `2026-07-27` history and in the
PR that removed them. If channels come back, they come back as a designed
component, not as a resurrected stub.

### ✅ totalfrank · P1 — mcp (6 endpoints) · `openapi_v1/mcp/router.py` — **IMPLEMENTED (PR #610)**
Marketplace + tenants + the caller's unified per-server config. All 6 wired to
the internal MCP services through the shared `core/mcp/` flow (extracted from the
internal router so both surfaces answer identically); owner-scoped via
`caller_owner_id`, tenant-scoped by the Stage 5 guard.
| Method | Path | Purpose | Success |
|---|---|---|---|
| GET | `/openapi/v1/bots/mcp/servers` | List marketplace servers (`keyword`, paged) | `Envelope[Page[McpServer]]` |
| GET | `/openapi/v1/bots/mcp/tenants` | List MCP tenants | `Envelope[list[McpTenant]]` |
| GET | `/openapi/v1/bots/mcp/servers/{server_code}` | Server detail | `Envelope[McpServerDetail]` |
| GET | `/openapi/v1/bots/mcp/servers/{server_code}/permissions` | Caller's permission for a server | `Envelope[McpPermission]` |
| GET | `/openapi/v1/bots/mcp/servers/{server_code}/config` | Read caller's unified server config | `Envelope[McpConfig]` |
| PUT | `/openapi/v1/bots/mcp/servers/{server_code}/config` | Write config (pushed to devices) | `Envelope[McpConfig]` |

_Delivered decisions (PR #610): paths stay nested (`/openapi/v1/bots/mcp/...`);
`sync_mode` dropped from the write body (no single-device push path — `extra=
"forbid"` makes it a 422); a failed device push rolls the write back and answers
502 (mirrors the internal surface); `endpoint_env`/`transport_protocol` are
strict enums (`PROD`/`PRE`, `SSE`/`STREAMABLE_HTTP`). **Preserved fail-open:** a
marketplace outage still reports the caller as permitted (advisory endpoint; the
MCP server enforces) — pinned by a test so it reads as a decision, not a bug._

### 🟩 lucas-xzp · P1 — resources (9 endpoints) · `openapi_v1/resources/router.py`
Unified file/link/folder abstraction; storage location never exposed.
| Method | Path | Purpose | Success |
|---|---|---|---|
| GET | `/openapi/v1/bots/resources` | List (`bot_id`, `type`, paged) | `Envelope[Page[Resource]]` |
| GET | `/openapi/v1/bots/resources/check-name` | Name availability (`name`) | `Envelope[NameCheck]` |
| POST | `/openapi/v1/bots/resources` | Create (file placeholder / link / folder) | `201 Envelope[Resource]` |
| POST | `/openapi/v1/bots/resources/upload` | Upload raw bytes as a resource (`application/octet-stream`) | `201 Envelope[Resource]` |
| GET | `/openapi/v1/bots/resources/{resource_id}` | Get | `Envelope[Resource]` |
| PUT | `/openapi/v1/bots/resources/{resource_id}` | Update | `Envelope[Resource]` |
| DELETE | `/openapi/v1/bots/resources/{resource_id}` | Delete | `Envelope[Deleted]` |
| GET | `/openapi/v1/bots/resources/{resource_id}/download` | Download bytes (**raw, not enveloped**) | `application/octet-stream` |
| GET | `/openapi/v1/bots/resources/{resource_id}/preview` | Preview | `Envelope[Preview]` |

_Note: upload is finalized as a raw `application/octet-stream` body (not
multipart). This diverges from PR #363's multipart summary — implementation
follows the route; switching to multipart would be a contract change._

### 🟪 totalfrank + lucas-xzp · P3 — skills, co-owned (6 endpoints: Track B contract ratified) · `openapi_v1/skills/router.py`
The Skills public API uses the `/openapi/v1/bots/skills` route group. Local
Skill upload and lifecycle operations belong to a specific bot.

> **Ratified with totalfrank.** Existing Local Skills are stored through a bot's
> device file system; they are not reusable tenant-global assets. Track B
> therefore uses a per-bot **upload** → **activate/deactivate** → **delete**
> lifecycle and does not expose a separate installation concept. Reusable
> tenant-level Skills are deferred until Skill Center provides independent
> storage and distribution. The public API does not expose a cross-bot Skill
> catalog: list and upload require `bot_id` as a query parameter, and list uses
> the optional `active` filter instead of a separate active-list route.
> `skill_id` maps to `ac_skill.id` and uniquely identifies a Skill, so operations
> on a specific Skill do not repeat `bot_id`.

Upload accepts one raw `application/zip` body and creates an Inactive Skill.
A same-name upload updates the existing Skill in that Bot scope while preserving
its ID and desired Active/Inactive state. For a service Bot, the optional
`owner_entity_id` on list/upload locates the owner scope only after permission
verification: the Bot owner remains the Local Skill owner, while an authorized
collaborator is recorded only as the operation actor. Reads use database desired
state and remain available while the Bot is offline; mutations require a ready
Bot and must compensate on runtime synchronization failure.

The router stubs now expose exactly this ratified contract. They define the
transport shape only; the Track B implementation slices wire persistence,
package storage, authorization, and runtime synchronization behind it.

| Method | Path | Purpose | Success | Status |
|---|---|---|---|---|
| GET | `/openapi/v1/bots/skills` | Local Skills of one bot (`bot_id` required; `owner_entity_id`, `active`, `keyword`, paged) | `Envelope[Page[Skill]]` | in stub |
| GET | `/openapi/v1/bots/skills/{skill_id}` | Skill detail | `Envelope[Skill]` | in stub |
| POST | `/openapi/v1/bots/skills/upload` | Upload raw ZIP (`bot_id` required; `owner_entity_id` optional; Inactive on create) | `201/200 Envelope[SkillUpload]` | in stub |
| POST | `/openapi/v1/bots/skills/{skill_id}/activate` | Activate a Skill | `Envelope[SkillState]` | in stub |
| POST | `/openapi/v1/bots/skills/{skill_id}/deactivate` | Deactivate a Skill | `Envelope[SkillState]` | in stub |
| DELETE | `/openapi/v1/bots/skills/{skill_id}` | Delete a Skill | `Envelope[Deleted]` | in stub |

### 🟩 lucas-xzp · P1 — routines (7 endpoints) · `openapi_v1/routines/router.py`
Scheduled/triggered agent tasks (the former "cron"); trigger is a nested object.
| Method | Path | Purpose | Success |
|---|---|---|---|
| GET | `/openapi/v1/bots/routines` | List (`bot_id`, `status`, paged) | `Envelope[Page[Routine]]` |
| POST | `/openapi/v1/bots/routines` | Create | `201 Envelope[Routine]` |
| GET | `/openapi/v1/bots/routines/{routine_id}` | Get | `Envelope[Routine]` |
| PATCH | `/openapi/v1/bots/routines/{routine_id}` | Update (partial) | `Envelope[Routine]` |
| DELETE | `/openapi/v1/bots/routines/{routine_id}` | Delete | `Envelope[Deleted]` |
| POST | `/openapi/v1/bots/routines/{routine_id}/run` | Run now | `Envelope[RoutineRun]` |
| GET | `/openapi/v1/bots/routines/{routine_id}/runs` | Execution history (paged) | `Envelope[Page[RoutineRun]]` |

### 🟩 lucas-xzp · P2 — identity (3 endpoints) · `openapi_v1/identity/router.py`
Read/write a bot's identity markdown files (RULES, SOUL, …), `file_type` is an
enum whitelist. No own Track A stage — scoped by bots isolation (Stage 1 ✅).
| Method | Path | Purpose | Success |
|---|---|---|---|
| GET | `/openapi/v1/bots/identity/{bot_id}` | List identity files + whether each exists | `Envelope[IdentityFileList]` |
| GET | `/openapi/v1/bots/identity/{bot_id}/{file_type}` | Read one identity file | `Envelope[IdentityFile]` |
| PUT | `/openapi/v1/bots/identity/{bot_id}/{file_type}` | Overwrite one identity file (`content`) | `Envelope[IdentityFileRef]` |

### ⬜ unassigned · Track C — engine runtime (16 endpoints)
Not a Track B category — these wrap the **engine adapter** on the bot's device
rather than a backend service. The per-endpoint checklist, the engine route each
one maps to, and the ruling on the ~72 engine routes that are *not* wrapped live
in **[`engine-surface.md`](engine-surface.md)**. Summary:

| Group | Endpoints | Public paths |
|---|---|---|
| sessions | 7 | `/openapi/v1/bots/sessions/{bot_id}…` — personal bots only |
| engine | 3 | `/openapi/v1/bots/engine/{bot_id}/{status,capabilities,available}` |
| models | 2 | `/openapi/v1/bots/models/{bot_id}`, `…/{bot_id}/{model_id}` |
| approvals | 3 | `/openapi/v1/bots/approvals/{bot_id}/mode` (GET/PUT), `…/modes` |
| connection | 1 | `/openapi/v1/bots/connection/{bot_id}` — complete WS URL, replaces `get_device_connection` |

---

## Definition of done (whole `/openapi/v1` effort)

1. **Track A:** every data category (bots, resources, skills, mcp,
   routines) carries `avernet_tenant` and is guarded, Stage-1 test shape green.
   — _2 of 6 (bots ✅, mcp ✅ PR #564)._
2. Internal API unchanged throughout (no `to_dict()` leaks; internal suites
   unmodified). — _holding: full `tests/community` green at #494 (9171 passed,
   3 skipped)._
3. **Track B:** the seven `/openapi/v1` categories' handlers implemented and
   tenant-safe, each with its own tests + PR. — _2 of 7 (bots ✅, mcp ✅)._
4. F2 tenant-leading indexes in place (mandatory policy). — _⬜_
5. Background/scheduled work revisited for per-tenant correctness. — _⬜_
6. `require_principal` / `resolve_avernet_tenant` wired to the real verifier
   (auth workstream) — the point at which a second tenant can safely hold real
   data, and the point at which the public surface stops answering 401.
   — _✅ both halves merged ([#634](https://github.com/inclusionAI/Avernet/pull/634), [#599](https://github.com/inclusionAI/Avernet/pull/599)); a `user` caller round-trips.
   Admitting an **external tenant** is now a separate, deliberate step:
   `route_security` must accept its credential **and** delegation must give that
   credential an end user to scope by (auth design §15)._
7. **Cross-tenant external identity settled ([#556](https://github.com/inclusionAI/Avernet/issues/556))** — Passport, auth
   relationships and BCN carry a tenant axis, so the BCN sync can be re-enabled
   on the public path. — _⬜ (added 2026-07-29; gates enabling multi-tenancy)._
8. **Track C:** the five engine-runtime groups (16 endpoints) implemented,
   owner-scoped and capability-aware, and `…/connection` returning socket URLs
   so no external caller ever sees a proxypass target or a raw device token.
   — _✅ 5 of 5 (PR #630). Like every other category it answers 401 until item 6
   lands; the singlebox E2E flow is blocked on the same event._

---

## Cross-cutting deferred items

- **F2 — tenant-leading indexes (MANDATORY corp policy).** Tables with a tenant
  column must have tenant-leading indexes. Deferred to the dedicated index
  work; **required before multi-tenant go-live**. When done: prepend
  `avernet_tenant` to the query-backing composites (`idx_owner` →
  `(avernet_tenant, owner_id)`, `idx_bot_id_entity_id`, `idx_entity`, search
  index) via **create-new-then-drop-old** (the naming convention ties an
  index's name to its columns; create before drop so there's no index-less
  window). Leave low-cardinality (`idx_status`, `idx_is_delete`) and
  unique-lookup (`idx_binding_id`) indexes alone.
- ~~**Real caller-identity verifier.**~~ **DONE, both halves** — see **The auth
  seam** above. `caller_owner_id`'s tolerance of an object exposing `user_id` is
  what let this land with zero handler changes, exactly as intended. Gateway
  [#599](https://github.com/inclusionAI/Avernet/pull/599) has merged and the wire
  shapes were verified by round-trip, so a `user` caller works end to end.
- **Owner semantics for `app` / `access_key` callers.** Still open, and now
  explicit: those callers are **refused at verification** (2026-08-02), not
  silently unscoped. Settling what they own is the delegation workstream (auth
  design §15); a `route_security` rule alone would not be enough, because a
  credential that names no person still cannot be owner-scoped.
- **Background/scheduled work.** Resolves to the default tenant now (correct
  while all data is `teamclaw`); revisit before a 2nd tenant holds real data
  (scheduled scans, pollers, sync loops in skill_center / governance / dormant /
  device pollers).
- **Recommended:** an arch guard/lint flagging new raw `threading.Thread` /
  `ThreadPoolExecutor` in core so future in-request spawns can't silently drop
  the tenant.

---

## Gotchas learned in Track A Stage 1 (save yourself the round-trips)

- Register the `do_orm_execute` read guard on the **`Session` class** (covers
  every runtime incl. the out-of-tree corp DB plugin), not one plugin.
- Use a **direct expression** in `with_loader_criteria`, not a lambda — the
  lambda form is cached and would pin the first tenant (a leak). Verified.
- `before_insert` fires **before** `server_default`, so an unset insert is
  genuinely `None` there and gets stamped; the raise branch only triggers on an
  explicit conflicting tenant.
- Run `tests/community/architecture/` after ANY new cross-module import — the
  context-boundary guard failed CI **twice** in Stage 1 for undeclared
  `utils.avernet_tenant` imports (declare them in the module's README).
- `uv sync` needs `--default-index https://pypi.org/simple` (the pinned aliyun
  mirror is blocked in the sandbox). The local pre-push hook can't run singlebox
  here — push with `--no-verify` and rely on remote CI. **`--no-verify` also
  applies to force-pushes** (a plain `git push` runs the ~10-min hook and times
  out).
- cwd drifts to repo root after `git` commands that `cd` there; `cd src/backend`
  before `uv run`.

## Gotchas learned in Track B bots (PR #494)

- **The envelope leaks where you don't look.** A handler-level decorator only
  covers failures *inside* the handler. Unknown path (404), wrong method (405)
  and body-validation (422) are raised **before** the router runs and were
  answering `{"detail": ...}` — the first three things a new integrator hits.
  Fixed once in `app.py`, path-scoped to `/openapi/v1`; don't re-solve it.
- **Map the base class last.** `ENVELOPE_ERRORS` returns on the first
  `isinstance` match in insertion order, so a specific leaf listed *after* its
  base never wins.
- **Errors that aren't in your category's hierarchy still escape.** The
  engine-config failures are plain `RuntimeError` siblings, not
  `BotServiceError` subclasses — each documented propagation path needed its own
  entry. Grep what your service can actually raise; don't assume one base covers it.
- **Never forward the exception's body headers** on an error response —
  `Content-Length`/`Content-Type` from the discarded body describe the wrong
  bytes. Do forward the protocol ones (`Allow` on 405, `WWW-Authenticate` on 401).
- **`extra="forbid"` is how immutability is expressed.** Without it, `engine` on
  a bot update is silently dropped and the caller thinks it worked.
- **Extract, don't copy, anything the internal surface also does.** Two copies of
  the create/Passport orchestration would have drifted within a release. The
  extraction must be behavior-preserving — prove it by leaving the internal
  suite unmodified.
- **A behavior-preserving extraction still surfaces internal bugs.** Four
  internal `/api/bots` defects only became visible once the logic was read out of
  the router (see the bots table note). Expect that, and decide deliberately
  whether to fix them in the same PR.
- Sixteen rounds of automated review on one wiring PR is what it took. Budget
  for review rounds, and file the questions that can't be settled inside a wiring
  PR as issues (#556 / #559 / #560) rather than patching around them.

---

## Changelog (append a dated line whenever you move the board)

- **2026-07-27** — Handoff README created. Track A Stage 1 (bots + reusable
  mechanism) complete and in **PR #456**, awaiting approval. Track B not
  started.
- **2026-07-27** — Work split assigned by **vertical slice** (no cross-person
  blocking): **totalfrank** = bots, mcp; **lucas-xzp** = resources,
  skills, routines, identity. Sequencing decision resolved → per-category
  vertical slices. Added **Endpoints per component** checklists (from the stub
  routers + PR #363), flagged the `/openapi/v1/bots/...` vs top-level path
  divergence and the two proposed ★ skills endpoints.
- **2026-07-27** — Added **priority tiers**: P1 = bots, mcp, resources, routines;
  P2 = identity; P3 = skills. **Skills is now co-owned** by totalfrank
  + lucas-xzp (its Track A stage and its endpoints), being the most involved
  category. Priority columns added to both status boards; per-component headers
  tagged with tier.
- **2026-07-27** — Skills endpoints merged from two tables (5 in-stub + 2
  proposed) into a **single 7-row table with a Status column**, so the full
  surface reads as 7 at a glance instead of looking like 2.
- **2026-07-27** — **Track A Stage 1 merged (PR #456).** Bots carry
  `avernet_tenant`; the reusable mechanism (carrier, guards, middleware,
  `resolve_avernet_tenant` seam) is on `dev`. The shared bots gate is lifted.
- **2026-07-28** — **Track A Stage 2 (ac_resource) DONE** (lucas-xzp, branch `rongzhi_0727`, pending rebase/push). Factory-extended PR #456's BotModel guard to `(BotModel, ResourceModel)`: one Session read listener chaining `with_loader_criteria` (direct expression, not lambda) + per-mapper `before_insert`. Red→green: resource tenant isolation/guard and routines/identity indirect-isolation tests. `to_dict()` excludes tenant. **DDL (ac_resource ADD COLUMN) submitted by lucas-xzp on the platform; must land before deploy. `ac_bot_publish` verified not read by this round's openapi_v1 handlers — left to service_bot owner or a later verify/online stage.** Stage 6 (routines) stays ⬜ TODO — no table, indirect isolation already covered by Session 0's ac_bots guard; real DONE lands with Track B routines handlers.
- **2026-07-29** — **Track B bots merged (PR #494) — first public category
  implemented.** All 13 `/openapi/v1/bots` endpoints wired to the internal
  services, owner-scoped via `caller_owner_id`, tenant-scoped by the Track A
  guard (cross-tenant `{bot_id}` → masked 404, proven against the real guard).
  Added the **shared Track B primitives** the other six categories reuse
  (`responses.py`, `contracts.py`/`ERROR_RESPONSES`, `principal.py`,
  `clusters.py` ACRA/ANDC, `errors.py`) and closed envelope escape structurally
  in `app.py`. Extracted `core/bot_management/create_flow.py` +
  `readiness.py` so both surfaces share one implementation. New architecture
  gate `test_service_api_conformance.py`. Full suite 9171 passed / 3 skipped.
  Board moved: Track A stage 1 → merged, Track B bots → done; added the
  **Track B primitives + recipe** section and the Track B gotchas.
- **2026-07-29** — Three inherited decisions filed as issues from #494's review
  and added to the cross-cutting board: **[#556](https://github.com/inclusionAI/Avernet/issues/556)** cross-tenant bot identity
  collision (**gates enabling multi-tenancy**; now DoD item 7),
  **[#559](https://github.com/inclusionAI/Avernet/issues/559)** async create can provision a bot other than the one authorized,
  **[#560](https://github.com/inclusionAI/Avernet/issues/560)** swallowed external identity writes. All pre-existing on `dev`.
  Also recorded the **401-until-auth-lands** state prominently up top — the
  public surface is implemented but not yet callable.
- **2026-07-29** — **Track A Stage 5 (MCP configuration) done — PR #564.** Two
  tables isolated: `ac_user_mcp_config` and `ac_bot_mcp_call_config`. Track B
  `mcp` is unblocked. Four things worth knowing before you copy this stage:
  1. The Stage 1 guard is now **model-agnostic** — `utils/avernet_tenant_guard`
     with `register_avernet_tenant_guard(Model)`. Later stages register rather
     than re-implement. `plugin_api/models.py` no longer holds the guard bodies.
  2. **Check your table for a unique key that includes the isolated dimension.**
     `ac_user_mcp_config` had `UNIQUE (user_id, server_code, env)`, which makes
     "two tenants, same user id" fail with a duplicate-key error at *write*
     time even though isolation reads correctly. `ac_bots` had no unique key, so
     Stage 1 never hit this. The key must lead with `avernet_tenant`.
  3. Its **deploy deadline differs from the column's**: the column before the
     code deploy, the key swap before a second tenant writes. Both recorded in
     the new "Schema changes applied out-of-band" section above.
  4. **Scope first, then isolate.** Four of the six `mcp` endpoints turned out
     to be MCP Center over HTTP with no local table at all, and
     `ac_entity_device_binding` — which the config write path does reach — needs
     nothing, because the query joins through `ac_bots` and
     `with_loader_criteria` applies to join clauses. Verified, not assumed.
- **2026-07-29** — **Channels deprioritized (not cancelled)**, Track A stage 3
  and Track B endpoints both parked with scope intact.
- **2026-07-30** — **Track B mcp merged (PR #610) — second public category
  implemented (2 of 7).** All 6 `/openapi/v1/bots/mcp` endpoints wired to the
  internal MCP services through a **shared `core/mcp/` flow** extracted from the
  internal `/api/mcp` router (masking / `extInfo` stripping / network-type
  allowlist in `presentation.py`; the write→push→rollback + read in
  `config_flow.py`; typed domain errors in `errors.py`), so both surfaces answer
  identically. The internal router now calls that flow — proven behavior-
  preserving by `test_mcp_config_internal_unchanged.py` + `test_mcp.py` passing
  **unmodified**. Public handlers owner-scoped via `caller_owner_id`,
  tenant-scoped by the Stage 5 guard (cross-tenant config invisible + un-
  overwritable, proven against the real guard through the flow). Decisions:
  nested paths (**resolves the path divergence for mcp**), `sync_mode` dropped
  (`extra="forbid"` → 422), push-failure rolls back → 502, strict
  `endpoint_env`/`transport_protocol` enums (no `DEV`). **Preserved fail-open**
  permission on a marketplace outage (advisory endpoint), pinned by a test. Board
  moved: Track B mcp → done; the "worked reference" list now points here as the
  second example, specifically for the *extract-shared-logic* pattern.
- **2026-07-30** — **Gateway principal verification landed (backend half).** The
  two seams PR #456 placed are real: `require_principal` verifies the gateway's
  signed `X-Avernet-Principal` (HS256, `aud=backend`, `principals[]`) and
  `resolve_avernet_tenant` reads the tenant out of it — **no handler, router or
  middleware changes**, which is what those seams were for. New
  `core/gateway_principal/` (our DTOs + verifier, no gateway imports) and
  `utils/gateway_principal_config.py` (env, **no dev fallback key** — unset means
  401). Built against gateway PR
  [#599](https://github.com/inclusionAI/Avernet/pull/599)'s contract without
  waiting for it to merge, since #599 explicitly leaves the component verifier to
  us. Deliberate guards: a wire tenant of `teamclaw` is refused, a disagreeing
  identity set is refused, forwarded secrets are not projected, and 401 is
  byte-identical for "no credential" and "bad credential". One necessary
  side-fix: the mapped-error lookup moved into
  `responses.py::mapped_error_response` and the app catch-all consults it, because
  the seam raises in a *dependency* — without it an unauthenticated public request
  would have answered 500 instead of 401. Board moved: the cross-cutting verifier
  row and DoD item 6 → backend half done. **Still gated upstream** on #599 merging
  and on `route_security.yaml` admitting this surface's real callers; and `app` /
  `access_key` callers 401 until somebody rules on what they own. SDD:
  `src/backend/specs/2026-07-30-gateway-principal-verifier/`.
- **2026-07-30** — **Track C implemented (PR #630)** — all 16 engine-runtime
  endpoints across five groups, plus `core/engine_runtime/` (the relay and the
  connection service) and its Service API Protocols. Seven things worth knowing
  before you touch this track or copy from it:
  1. **Track C changes nothing outside its own prefix.** An `Envelope.warning`
     field was added and then removed: across both OSS engines the only
     *limited* capability this surface can reach is `SESSION_CREATE` on
     `claude_code`, whose caveat describes how the session key is established
     rather than a degraded result — so the field would have been permanently
     empty on 15 of 16 endpoints and on all six other categories. `501`/`504`
     live in a per-group dict for the same reason.
  2. **The engine's own text never reaches a caller.** Capability caveats and
     the `limited`/`fallback` explanations are internal engineering prose and
     not always English; only capability *names* are published. Field
     descriptions and docstrings are published verbatim into the OpenAPI
     document, so rationale belongs in `#` comments — a gate now fails the build
     on internal markers in published text.
  3. **The sessions group serves `personal` bots only.** The engine accepts
     `user_id` on session list, logs it, and **drops it**, so a device returns
     every session it holds. On a `service` bot that is every caller's. Gated
     before the forward, not filtered after.
  4. **`GET /api/engine/status` is the one engine route with no envelope** — it
     returns `EngineManager.status()` raw. Treating it as enveloped fails every
     call against a healthy device.
  5. **Any engine 404 is a resource, not a missing capability.** The transport
     raises its not-found error for unknown session ids and model ids too;
     mapping it to 501 would tell a caller its bot lost the sessions capability.
  6. **Isolation is "don't undo it", not "build it".** No table, no DDL, no
     Track A stage — but the guard cannot see a device call, so the isolation
     sweep asserts the transport was *never invoked* across all 16 routes rather
     than just checking for a 404.
  7. **The singlebox E2E flow is blocked on the auth workstream**, not on this
     module — every `/openapi/v1` route answers 401, so a flow could only assert
     401s. `engine_runtime` stays on `SINGLEBOX_E2E_EXEMPT` until the gateway
     verifier lands.
- **2026-07-30** — **Track C added — the public API now wraps the engine too.**
  Previously the frontend got a connection from `get_device_connection` and
  called the bot's engine adapter itself through `/proxypass/{target}`; that
  hand-off publishes proxypass topology and a raw device token, and makes the
  engine — never designed as a public contract — the surface an integrator codes
  against. Track C wraps the engine's client-facing HTTP behind
  `/openapi/v1/bots/{bot_id}/…` instead. Five things worth knowing:
  1. **16 endpoints, not 89.** The engine serves 89 HTTP routes + 6 WS across 25
     routers. The scope rule wraps only what the frontend reaches *directly*
     (sessions 7, engine 3, models 2, approvals 3) plus one new `…/connection`.
     Backend-mediated engine routes stay with the backend contract that already
     fronts them, and **nodes was dropped** — the frontend proxypasses it, so
     the rule would wrap it, but the product does not need node inventory
     publicly.
  2. **`/api/cron` was already this.** Backend `/api/cron` →
     `CronRelayService` → `DeviceAdapterTransport` → engine has been in
     production all along, and the `routines` stub already imports
     `CronRelayServiceProtocol`. Track C generalises an existing shape rather
     than inventing one.
  3. **No Track A stage, no DDL** — the first track for which that's true by
     construction, not by luck. Added the caveat up top so nobody hunts for a
     stage that doesn't exist.
  4. **WebSockets are not wrapped.** `…/connection` hands back one ready-to-use
     `wss://` URL with the credential in it; the caller owns the socket. No
     `POST /chat`, no SSE relay of the engine's frame format.
  5. **Two exclusions are contract decisions, not laziness.** `engine/switch`
     would be a back door around #494's `engine`-immutability ruling, and
     `engine/restart` would give one bot two restart verbs.

  Full inventory, per-endpoint mapping and the ruling on every non-wrapped
  engine route: **[`engine-surface.md`](engine-surface.md)**. Board moved: Track
  C section added (0 of 6 groups), DoD item 8 added. **Owners still unassigned.**
- **2026-08-02** — **Identity admission decided: `user` only.** `verify_principal_token`
  now refuses an identity set that names no end user, so `bot` / `app` /
  `access_key` callers answer `401` **by design** rather than as a side effect of
  `caller_owner_id` raising on an empty owner. Four things worth knowing:
  1. **The refusal moved because the old placement was skippable.** A 401 that
     comes from `caller_owner_id` only covers handlers that call it — and four in
     `resources/router.py` (`list_resources`, `create_resource`, `get_resource`,
     `update_resource`) never do; they scope on a caller-supplied `bot_id`. An
     unscopeable caller reached them and got data. Refusing the identity set
     during verification makes the rule hold for every route, including ones not
     written yet. An invariant every handler must remember is not an invariant.
  2. **`bot` lost its owner fallback.** `VerifiedCaller.user_id` no longer falls
     back to `bot.owner_id`. It was unreachable (no `route_security` rule
     requires or accepts `bot`), but it was a standing grant — a bot acting as
     its owner across the whole public contract — that nobody had decided to
     make. A bot-facing surface should re-add it deliberately.
  3. **A user *plus* other identities is still accepted.** The gateway forwards
     the whole set it resolved, so a route declaring `user: required, app:
     optional` yields two principals. The rule is "must contain a user", not
     "must contain nothing else" — the strict form would refuse a request the
     gateway considers valid.
  4. **Auth is now attached at `build_public_router()`**, alongside
     `ERROR_RESPONSES`, so a future route cannot omit it by construction rather
     than by `test_public_routes_require_principal` catching it afterwards.
     Zero behaviour change — every route already declared `PrincipalDep`, and
     FastAPI caches the dependency, so it resolves once per request.

  Also corrected on this board: gateway [#599](https://github.com/inclusionAI/Avernet/pull/599)
  **has merged** (it was still recorded as open in three places), and the
  principal wire contract was verified by round-tripping the real gateway signer
  into the real backend verifier — the shapes match, forwarded secrets are not
  projected, and `aud`/`iss` mismatches are refused. **Filed as still-open:** no
  cross-repo test pins that contract, so a rename on either side leaves both
  suites green and 401s production. Full suite 10204 passed / 3 skipped. SDD:
  `src/backend/specs/2026-08-02-public-api-user-only-principal/`.
- **2026-08-03** — **`/openapi/v1/bots` path normalization + channels removed.**
  Every component's routes now live under `/openapi/v1/bots/<component>/…` with
  `{bot_id}` as the first segment *inside* the component — see the new
  **Addressing rule** section, which is now the thing to read before adding a
  component. Three shapes were in use and only one was the intended one:
  `identity` carried a redundant `/bot/` segment, and `connection`, `engine`,
  `approvals`, `sessions`, `models` and `skills` put `{bot_id}` ahead of their
  own name, which left those router files unable to state their own address and
  left no room for a second owner under the shared base (the collision BCS hit
  from the other side and solved the same way). `skills` also gained a literal
  `catalog` segment, because its two resource families would otherwise both want
  `/openapi/v1/bots/skills/{…}`. `channels` was deleted rather than parked.
  41 published paths, down from 43. No handler, schema, status code, auth rule
  or tenant-scoping rule changed — addresses only, and **no compatibility
  aliases**: the surface has no reachable external caller yet, so there was no
  contract to preserve. The gateway's pinned `bots.openapi.json` was regenerated
  through the real compat gate (`--allow-breaking`); it had been stale since
  Track C, carrying 32 paths against the backend's 43. A new test,
  `tests/…/openapi_v1/test_path_convention.py`, asserts the rule — and this
  file's reserved-name list — against the generated document, so both fail here
  rather than in review. SDD:
  `src/backend/specs/2026-08-03-openapi-v1-path-normalization/`.
- **2026-08-04** — **Track B Skills contract finalized at six endpoints.** Removed
  the separate `/skills/active` route in favor of the collection's optional
  `active` filter. Pinned Bot-scoped raw ZIP upload, same-name replacement,
  owner-versus-collaborator semantics, offline reads, ready-Bot mutation gating,
  and compensation on runtime synchronization failure. Skill Center publication
  and reusable tenant-level Skills remain a later contract.
