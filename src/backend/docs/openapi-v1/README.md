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
`src/backend/src/agentclaw/community/adapters/http/openapi_v1/*`. The **bots**,
**mcp**, **channels**, and **skills** categories have implemented handlers; the
remaining categories retain their independently tracked readiness states below.

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
  (Track A) first. Skills implementation/CI is complete but its schema and
  pre-production release gates remain pending; see its board row.
- **Track C — Engine (runtime) surface.** _Added 2026-07-30._ Wrap the engine
  adapter's client-facing HTTP behind `/openapi/v1/bots/{bot_id}/…`, and replace
  the `get_device_connection` hand-off with one sanitised socket-info endpoint.
  **18 endpoints across six groups — implemented** (PR #630 baseline; engine
  restart and read-only nodes added later).

> ⚠️ **The one confusion to avoid:** "isolation Stage N is done" does **not**
> mean any API endpoint was implemented. A Track A stage is plumbing only (the
> reusable mechanism + that category's records). The API endpoints land in
> Track B — each category has its own state on the board; implementation does
> not by itself make a category release-complete.
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
| 3 | Channels (`ac_channel_config`) | Bot Workshop | P1 | ✅ **IMPLEMENTED 2026-08-19; DDL PENDING** | tenant column + tenant-leading index + guards + isolation tests; deploy DDL before code |
| 4 | Skills (skill tables) | totalfrank + lucas-xzp | P3 | ⬜ TODO | same |
| 5 | MCP configuration (`ac_user_mcp_config` + `ac_bot_mcp_call_config`) | totalfrank | P1 | ✅ DONE — **PR #564** | PR #564 merges |
| 6 | Routines | lucas-xzp | P1 | ⬜ TODO | same |

> Stage 1 also builds the **reusable mechanism** (see below) that every later
> stage copies. It's the foundation, not just "bots."

### Track B — Public API implementation
_Ordered by priority tier._
| Category | Owner | Pri | Router | State | Depends on |
|---|---|---|---|---|---|
| bots | totalfrank | P1 | `openapi_v1/bots/router.py` | ✅ **DONE — PR #494 merged 2026-07-29** (13/13 endpoints) | ~~Track A stage 1~~ ✅ |
| token / caller identity | liaoxianhao | P1 | `openapi_v1/token/router.py` | 🔧 IN PROGRESS — both capabilities migrated; OCB Gateway SOFA sync pending | User principal + browser HttpOnly `IAM_TOKEN` cookie |
| mcp | totalfrank | P1 | `openapi_v1/mcp/router.py` | ✅ **DONE — PR #610** (6/6 endpoints) | ~~Track A stage 5~~ ✅ (PR #564) |
| resources | lucas-xzp | P1 | `openapi_v1/resources/router.py` | 🔧 IN PROGRESS (PARTIAL) — 7 handlers all wired but DEFINITION-ONLY / NOT PUBLIC-READY | Track A resources ✅(Phase 0); Track B all 7 endpoints wired stub→service, files-only and path-addressed; gated on auth workstream (gateway principal seam) + DDL deploy before public exposure |
| routines | lucas-xzp | P1 | `openapi_v1/routines/router.py` *(stub)* | ⬜ TODO | Track A routines (lucas-xzp) |
| channels | Bot Workshop | P1 | `openapi_v1/channels/router.py` | ✅ **IMPLEMENTED 2026-08-19** — 6/6 draft DingTalk Channel operations | Track A stage 3 code ✅; deploy-before-code DDL below; OCB/Sofapy schema copy remains independent |
| identity | lucas-xzp | P2 | `openapi_v1/identity/router.py` *(stub)* | ⬜ TODO | bots isolation (Stage 1 ✅) |
| skills | totalfrank + lucas-xzp | P3 | `openapi_v1/skills/router.py` | 🔧 **IMPLEMENTATION + CI COMPLETE; RELEASE PENDING** — six ratified Local Skill operations | #725 cleanup-work DDL must deploy before code; [pre-production acceptance runbook](skills-track-b-preprod-acceptance.md) remains **PRE-PROD PENDING** |

### Track C — Engine (runtime) surface (6 of 6 groups implemented)
_All groups depend only on **bots isolation (Stage 1 ✅)** — no Track A stage, no
DDL. Full ruling and per-endpoint mapping in
**[`engine-surface.md`](engine-surface.md)**._

| Group | Endpoints | Owner | Pri | Router | State |
|---|---|---|---|---|---|
| sessions | 7 | ⬜ unassigned | P1 | `openapi_v1/engine_runtime/sessions/` | ✅ **IMPLEMENTED — PR #630**; operators + stages 2026-08-09 |
| engine (read/write) | 4 | ⬜ unassigned | P1 | `openapi_v1/engine_runtime/engine/` | ✅ **IMPLEMENTED — PR #630**; process restart added 2026-08-17 |
| connection | 1 | ⬜ unassigned | P1 | `openapi_v1/engine_runtime/connection/` | ✅ **IMPLEMENTED — PR #630** |
| approvals | 3 | ⬜ unassigned | P2 | `openapi_v1/engine_runtime/approvals/` | ✅ **IMPLEMENTED — PR #630** |
| models | 2 | ⬜ unassigned | P2 | `openapi_v1/engine_runtime/models/` | ✅ **IMPLEMENTED — PR #630** |
| nodes | 1 | joseph | P2 | `openapi_v1/engine_runtime/nodes/` | ✅ **IMPLEMENTED — 2026-08-19**; read-only list matching the frontend |

> **Scope rule (why only these).** Wrap engine HTTP the frontend reaches
> **directly** through proxypass (`src/frontend/src/requestConfig.ts:189-205`).
> Engine routes the frontend reaches **via the backend** — `/api/cron` (already
> the `routines` category), `/api/file`, `/api/skills`, `/api/mcp`,
> `/api/resource-materializations`, `/api/bash`, `/api/bot/config`,
> `/api/work-items` — are already fronted by a backend contract and stay out.
> The read-only `/api/nodes` inventory is wrapped because the current frontend
> reaches it directly; no node write operations exist in either frontend or
> Engine HTTP today. AICoding-only routes stay out. **WebSockets are not wrapped**: the new
> `…/connection` endpoint returns one complete socket URL, credential included,
> and the caller builds the connection itself.
>
> `engine/switch` remains deliberately excluded: wrapping it would be a back
> door around #494's `engine`-immutability ruling on
> `PUT /openapi/v1/bots/{bot_id}`. Engine-process restart is now exposed as
> `POST /openapi/v1/bots/{bot_id}/engine/restart`; it relays the daemon restart
> and is distinct from the bot-level `/restart`, which re-provisions the whole
> container. Because this operation was introduced after bot-first addressing,
> it has no component-first retiring alias. `session-favorites` and the
> `/api/openclaw` HTTP trio remain **deferred, not cancelled**.
>
> **Routines is Track C's worked precedent, not a Track B one.** Backend
> `/api/cron` → `CronRelayService` → `DeviceAdapterTransport` → engine has been
> the shape in production all along, and `openapi_v1/routines/router.py:29`
> already imports `CronRelayServiceProtocol`. Read it before writing a handler.

### Cross-cutting (not per-stage)
| Item | State | Note |
|---|---|---|
| Real caller-identity verifier (auth workstream) | ✅ **DONE both halves** — backend PR [#634](https://github.com/inclusionAI/Avernet/pull/634), gateway PR [#599](https://github.com/inclusionAI/Avernet/pull/599) **merged** | `require_principal` + `resolve_avernet_tenant` verify the gateway's signed `X-Avernet-Principal` (HS256, `aud=backend`) and read tenant + owner from it. The wire contract was checked by round-tripping the **real** gateway signer into the **real** backend verifier (2026-08-02): user/bot/app/access_key shapes, secret non-projection, `aud`/`iss` refusal. **A `user` caller works end to end.** What remains is *which* callers are admitted — see the identity-admission row below |
| **Identity admission: `user`, plus `app` under a grant** | ✅ **DONE 2026-08-10** | Widened from user-only (#950). An **application acting alone** — its own credential, no human on the wire — is admitted on the operations placed in an admission group (`adapters/http/openapi_v1/admission.py`), and reaches only what the user who authorized it has authorized it *for*, re-adjudicated per request. `bot` / `access_key` callers are still refused outright at verification. The end-user requirement moved from `verify_principal_token` to `require_principal`, which every public route declares — so an operation absent from the table refuses a machine caller by omission. SDD: `specs/2026-08-10-openapi-v1-app-only-caller/` (earlier: `specs/2026-08-02-public-api-user-only-principal/`) |
| **No cross-repo test pins the principal wire shape** | ⬜ TODO | Both sides are tested against their own hand-written idea of the payload (`test_verifier.py` builds dicts; the gateway tests its own models). Renaming a field on one side leaves both suites green and 401s production |
| Tenant-leading indexes (F2, **MANDATORY** policy) | ⬜ TODO | before multi-tenant go-live |
| Background/scheduled work revisit | ⬜ TODO | before a 2nd tenant holds real data |
| **Bot identity keys collide across tenants** ([#556](https://github.com/inclusionAI/Avernet/issues/556)) | ⬜ TODO (totalfrank) | Passport, auth relationships, BCN, policy row are keyed on `bot_id`/`owner_id` with no tenant axis, and every owner's first bot is literally `"default"`. **Should gate enabling multi-tenancy.** Stopgapped in #494 by `sync_to_bcn=False` on the public update path |
| Async create ≠ authorized bot ([#559](https://github.com/inclusionAI/Avernet/issues/559)) | ⬜ TODO (totalfrank) | the pending create spec is never persisted; completion rebuilds it from the polling request. Pre-existing on `dev`; latent (community Passport always issues) |
| Swallowed external identity writes ([#560](https://github.com/inclusionAI/Avernet/issues/560)) | 🔧 PARTIAL | Owner-grant writes in shared cloud create/auth completion and Local Bot completion now propagate failures, and an issued Passport identity without `agent_code` fails closed; public OpenAPI Passport metadata updates are normalized to a 502 envelope instead of returning success. The legacy internal update route still logs and continues, and no durable cross-system repair/reconciliation workflow exists yet. |

> The three issues above came out of #494's review and are **pre-existing on
> `dev`**, not regressions — they're recorded here because they are decisions
> the whole effort inherits, not bots-only bugs. #556 in particular is the one
> that must be settled before a second tenant holds real data.
| **Stage 5 unique-key swap on `ac_user_mcp_config`** | ⬜ TODO (DDL below) | **before a 2nd tenant writes MCP config** — not before deploy |

> **✅ Channels restored (2026-08-19).** The 2026-08-03 deletion remains in
> the changelog as historical context: the old published stubs were correctly
> removed because they returned 500. The component has now returned as a fully
> implemented Bot-first API with tenant-isolated persistence, explicit caller
> admission, safe secret projection, and tests. It is not deployable until the
> Stage 3 DDL below has been applied; the OCB/Sofapy gateway schema is a separate
> copy and must be synchronized independently.

---

## Schema changes applied out-of-band (no migration files in-repo)

Per the standing decision, tenant-isolation schema changes are applied on the
platform out of band, so **these statements are the authoritative record**.
Hand them to whoever applies DDL together with the ordering notes.

**Stage 1 — `ac_bots`** (tenant column already applied; Bot Workshop space column pending platform execution):

```sql
ALTER TABLE ac_bots
  ADD COLUMN avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw'
    COMMENT 'data-isolation tenant; existing rows are the internal teamclaw tenant';

-- Bot Workshop Business Space ownership. Run this before deploying code that
-- contains BotModel.space_id: ORM SELECTs read the column, so a missing column
-- breaks Bot reads and creation. NULL represents a legacy row with no explicit
-- space assignment; the public Inventory interprets it as personal:{owner_id},
-- so no one-time backfill is required. The compatibility column may remain
-- during code rollback; platform owners should assess DROP COLUMN only after no
-- deployed version uses it.
ALTER TABLE ac_bots
  ADD COLUMN space_id BIGINT UNSIGNED NULL
    COMMENT 'Bot owning space id, references ac_space.id; NULL uses the owner personal-space fallback';
```

The delivery record for `space_id` must identify the environment, change/version
record, execution time, rollback owner, and result. Team-space support is not
release-ready until that evidence exists.

**Stage 3 — Channels (`ac_channel_config`)**. Both statements must land
**before deploying the Channels code**: the ORM selects `avernet_tenant`, so a
code-first deployment breaks all Channel reads. The default backfills existing
rows into the internal `teamclaw` tenant. The replacement index leads with the
same tenant axis used by the ORM guard, preventing same-owner/same-Bot records
from different tenants from sharing an unscoped lookup path.

```sql
ALTER TABLE ac_channel_config
  ADD COLUMN avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw'
    COMMENT 'data-isolation tenant; existing rows are the internal teamclaw tenant';

ALTER TABLE ac_channel_config
  DROP INDEX idx_type_id_d_bbi,
  ADD INDEX idx_tenant_env_type_id_d_bbi
    (avernet_tenant, env, type, identity_id, deleted, bind_bot_id);
```

Apply the column addition before the index replacement if the platform executes
one statement at a time. A rollback may keep the additive tenant column, but
must not deploy code that references it before the column exists. Record the
environment, change/version record, execution time, result, and rollback owner.

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
  **The backend itself resolves nothing in singlebox** — no secret store, no
  local stand-in, and no backend config knob changes that. The deliberate
  change lives in the *launcher*: `scripts/modules/backend.sh` arms the
  community env var with the same NOT-FOR-PROD dev key the gateway signs with
  (`scripts/modules/gateway.sh`) and BCS verifies with
  (`scripts/modules/bcs.sh`), so a locally forwarded `/openapi/v1` request
  verifies instead of always-401. Export the variable yourself (e.g. in
  `.env.local`) to override it. The application still ships no dev fallback
  key on purpose: a committed shared secret is a committed credential, and the
  launcher default authenticates nothing outside a dev box. The key is
  resolved once at boot, so rotating it needs a restart on both sides.

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
- **Only the machine principals carry a tenant.** _Changed 2026-08-05._ A
  `user` principal has no `tenant` field: nothing in a user credential proves
  which tenant a person acts for, and the gateway's google chain used to fill it
  from a config default — which, left unset as it shipped, sent `null` and 401'd
  every request. `app`, `bot` and `access_key` are each registered to a tenant,
  and that registration is what their principal asserts.
  - An identity set that asserts **no** tenant — a user and nothing else —
    resolves to `DEFAULT_AVERNET_TENANT`. A first-party caller on our own
    frontend *is* an internal caller, which is the scope `teamclaw` names, and
    it is the same tenant every other path in this component resolves to.
  - That fallback is **ours**, decided from the absence of a claim; it is not a
    value the token supplied. A token may *also* name `teamclaw` on a machine
    principal — _changed 2026-08-05_, see below — and both routes reach the same
    scope. A `tenant` smuggled onto a `user` entry is still neither: unknown
    fields are dropped, not honoured, so it never becomes a scope.
  - ⚠️ **Consequence for the public surface.** `route_security` declares
    `user: required` and nothing else for every `/openapi/v1` path, so the
    gateway resolves a user-only set and **every public request now scopes to
    `teamclaw`** — the internal tenant. Nothing gates *which* Google account
    that is (`AuthPlugin.is_allowed` exists in the gateway SPI but no authn
    strategy calls it), so serving real data on this surface needs either that
    whitelist wired up or a route requiring an identity that carries a
    registered tenant. Until then the surface reads internal data for any
    authenticated Google user, which is a widening of what the 401 used to
    prevent by accident.

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
  - **Every converted failure is logged, with its traceback and the handler's
    arguments** — see the next bullet. You inherit this; do not add per-handler
    `try/except: logger.error(...)` around a mapped error.
- **`error_logging.py`** (`adapters/http/`) — the diagnostics half of the fixed
  message rule. Because the response says only "Not found", the log line is the
  *only* record of what actually happened, so `@envelope_errors` emits one per
  failure: exception type, the internal message, the concrete path plus the route
  template, and the arguments the handler was called with. Level follows status
  (`4xx` → warning, `5xx` → error); both carry the traceback, because an error
  reaching this path was raised inside a handler and the trace is a short chain
  of our own frames pointing at the check that refused the request. What you need
  to know when adding a category:
  - **Capture is lazy** — a successful request pays nothing.
  - **Values are summarized, not dumped**: strings truncated, collections capped,
    bytes reduced to a size, request bodies rendered from `model_dump()`, and
    injected services dropped rather than rendered.
  - **Names that look like credentials are redacted** (`token`, `password`,
    `secret`, `authorization`, `api_key`, `signature`, `cookie`, …), at any
    nesting depth. `Request` and `Headers` are opaque by type — `Request` is a
    `Mapping` over its ASGI scope, so walking it would put the raw header list
    in the log. **If your category adds a body field holding a credential whose
    name is not on that list, add the substring to `_SENSITIVE_NAME_PARTS`.**
  - An **unmapped** error is re-raised, and the arguments are stashed on the
    request scope so `app.py`'s handler logs the same detail from further out.
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

## Naming the end user (`?user_id=`)

**Every operation that scopes to a user takes a required `user_id` query
parameter.** Not a body field, not a path segment — the query string, whatever
the method, whatever the body.

```text
GET    /openapi/v1/bots/b-1?user_id=u-42
PUT    /openapi/v1/bots/b-1?user_id=u-42        {"bot_name": "Ada"}
DELETE /openapi/v1/bots/b-1?user_id=u-42
POST   /openapi/v1/bots/b-1/skills?user_id=u-42                  <raw zip>
```

**Why one placement.** The user id is not an attribute of any resource on this
surface — it is *who the call is for*: the same value on every operation and the
same meaning on a read as on a write. A request body describes the resource, so
putting it there makes it read as a property of the thing (in a `PUT
…/bots/{bot_id}` payload, beside `bot_name`, it looks like a field you are
setting on the bot). A path segment *names* the resource, so
`/bots/{bot_id}/users/{user_id}` would claim to address a user beneath a bot —
inverting the ownership and describing something the operation does not return.

Three alternatives were considered and rejected, recorded so the question is not
reopened from scratch (`specs/2026-08-08-openapi-v1-explicit-user-id/plan.md`):

| Rejected | Why |
| --- | --- |
| Body field on the 11 JSON-body writes | Needed a three-row exception table for the writes whose body this API does not define — the two raw-byte uploads and the free-form `PUT …/engine/config` — and split one concept across two placements on the same resource |
| Path segment | Inverts ownership as above; the user-first form `/openapi/v1/users/{user_id}/…` is coherent but closed, because the first segment after `/openapi/v1` is the gateway's **domain selector** |
| `X-Avernet-User-Id` header | Uniform and matches the gateway's delegation sketch (auth design §15), but makes the user transport metadata rather than an argument of the operation |

**`bot_id` is untouched.** It stays in the path where it addresses a bot, and in
the query string where it is a parameter. This change moved none of them.

**What it does not change.** The named user must still be the verified caller.
Naming anyone else is a `403` with a fixed `"Forbidden"` — the body says nothing
about the user asked for, and two rejected ids give byte-identical responses. A
request with no verified principal still answers `401`, exactly as before. The
whole point is to have the contract ready for App-on-behalf-of *before* that
caller exists; admitting it is the delegation workstream (auth design §15), and
the single line it relaxes is the equality check in
`openapi_v1/principal.py::require_user_id`.

**Four operations take no `user_id`,** because they have no user dimension to
scope by. They still require an authenticated caller — that is
`require_principal`'s job — they just have no user-shaped answer to give:

| Operation | Why it takes none |
| --- | --- |
| `GET /openapi/v1/bots/check-name` | Name uniqueness is checked across the tenant; `check_bot_name_exists` takes only the name |
| `GET /openapi/v1/bots/mcp/servers` | Marketplace catalogue — identical for every caller in the tenant |
| `GET /openapi/v1/bots/mcp/servers/{server_code}` | Same |
| `GET /openapi/v1/bots/mcp/tenants` | Same |

Note what is *not* on that list: four resources handlers (`list_resources`,
`create_resource`, `get_resource`, `update_resource`) used to take the value
without using it — user-scoped in principle, scoping on a caller-supplied
`bot_id` without checking the caller owns that bot, the gap
`specs/2026-08-02-public-api-user-only-principal/` records. Three of them went
with the link resources they served, and the surviving `list_resources` now
consumes the value: every files-only handler resolves the workspace through
`_file_coords(bot_id, owner_id, …)`, so the parameter reaches the seam rather
than being accepted and dropped. The ownership gap itself is unchanged — the
`bot_id` is still caller-supplied — and closing it remains a change to those
handlers, not a new parameter.

**Bot Logs is a different exclusion, and the sharpest thing to know here.**
`GET /openapi/v1/bots/logs/traces` has taken a required `user_id` since #692 —
but there it means *whose traces to read*, a filter a caller presenting both a
user and an App identity may point at someone else. Here it means *whose call
this is*, and pointing it at someone else is a 403. **Same spelling, opposite
contract**, and the published document carries both. Do not "unify" them without
deciding which meaning the address should have.

`tests/…/openapi_v1/test_explicit_user_id.py` asserts all of the above against
the generated document — the 56 that take it, the 4 that do not, that `user_id`
is never a body field or a path segment, and that `bot_id`'s placement is
unchanged — so a route that breaks the rule fails there rather than in review.

---

## Operating shared bots and published stages (`?owner_id=`, `?stage=`)

**The engine-runtime groups are an operator console, and who may hold it is
one rule:** the bot's **owner**, or a **collaborator at member level or
above** — the same bar the internal device-connection applies
(`core/engine_runtime/gate.py`, `OPERATOR_LEVEL`). Public visibility grants
operation to **no one**: a public bot's audience converses with it over the
messages channel; operating it stays with its team. Anyone else is answered
**byte-identically to a bot that does not exist** (the masked 404) — not a
403, which keeps its single `user_id`-mismatch meaning. A failed collaborator
lookup refuses (fail closed). Both ids are logged at the refusal; the
response carries neither.

The **sessions** group additionally serves a human who has an accepted BCN
friendship with the addressed Bot. This is a conversation path, not an
operator grant: it is draft-only, exposes only that human's Backend-owned
Expert Chat sessions, and continues to use Expert Chat's existing session,
message, connection, and runtime adapters. BCN is consulted on every request
as the friendship authority; its failure is fail-closed, and Backend's legacy
chat-list row is only a compatibility projection. All non-session
engine-runtime groups keep the owner/collaborator rule above. Friend callers
must name `owner_id` until BCN provides an exact Bot lookup that resolves the
qualified `{bot_id}:{owner_id}` identity.

Two optional query parameters name the target, following the same placement
rule as `user_id` (query string, never a body field or a path segment):

```text
GET /openapi/v1/bots/b-1/sessions?user_id=u-collab&owner_id=u-owner            collaborator, team bot
GET /openapi/v1/bots/b-1/engine/status?user_id=u-owner&stage=online            owner, live runtime
GET /openapi/v1/bots/b-1/connection?user_id=u-collab&owner_id=u-owner&stage=verify
GET /openapi/v1/bots/b-1/engine/config?user_id=u-owner&stage=online            the release's own config
GET /openapi/v1/bots/b-1/identity/RULES?user_id=u-owner&stage=verify           what verify received
```

- **`owner_id`** — the owner of the bot the request addresses. Defaults to
  the caller, so operating one's own bot names nothing extra and **every
  request valid before this change behaves byte-for-byte the same**.
- **`stage`** — which runtime the request addresses: `draft` (default — the
  bot's own workspace, the only runtime a personal bot has), `verify`, or
  `online`. A published stage is live per the rule in
  `core/engine_runtime/stage.py`, shared with cron's runtime targeting:
  `online` while the newest publish record is at `SUCCESS`; `verify` while a
  record validates, or through the promoted record's **retained** verify
  binding while it stays ACTIVE. A stage with no live runtime — including a
  published stage named on a personal bot — is `409` `"No live runtime at
  the requested stage"`, never a fallback to another stage's binding.
  (`eval` has no long-lived runtime and is not addressable.)

### `?stage=` beyond the operator console

Five more operations take it — the ones that read or write a file **on** a
runtime, rather than forwarding a request to it:

```text
GET, PUT  /openapi/v1/bots/{bot_id}/engine/config
GET       /openapi/v1/bots/{bot_id}/identity
GET, PUT  /openapi/v1/bots/{bot_id}/identity/{file_type}
```

Same vocabulary, same default, same liveness rule. Two differences worth
knowing:

- **The reads serve all three stages; the writes accept only the draft.** A
  published runtime is what a release produced and is replaced by publishing
  again, never edited, so `PUT …?stage=verify` or `?stage=online` is refused
  with `409` `"The requested stage is read-only"` and **nothing is written** —
  not to the published runtime, and not to the draft as a substitute. This is
  deliberately not a `200` carrying a no-op flag: automation that checks the
  status code would record the write as landed. It is also a different answer
  from `"No live runtime at the requested stage"`, which would send a caller
  off to publish something and retry; publishing would not make the write land.
- **The list reports one runtime.** `GET …/identity?stage=verify` probes every
  identity file against the verify runtime, so a caller never sees a draft row
  beside a verify row.

These five carry `user_id`, not `owner_id`: they are owner-scoped, not operator
console operations, and reaching another owner's bot through them is not
offered.

**These five retiring addresses do not take `stage`:**

```text
GET, PUT  /openapi/v1/bots/{bot_id}/engine-config
GET       /openapi/v1/bots/identity/{bot_id}
GET, PUT  /openapi/v1/bots/identity/{bot_id}/{file_type}
```

They answer with the contract they were frozen with, which is the draft. A
caller who sends the parameter at one of them gets the draft, because the
parameter is not declared there — including on the two writes, which therefore
answer `200` and write the draft where their replacements answer `409` and write
nothing. That divergence is the strongest reason to migrate.

This is **not** a statement about every retiring address. The engine-runtime
ones — `…/bots/sessions/{bot_id}`, `…/bots/engine/{bot_id}/status` and the rest
— *do* publish `stage` and honour it, because they took it before the freeze, so
it is part of the contract they were frozen with.

### Per-bot device surfaces that are still draft-only

Named so a reader looking for them stops here rather than in the source:

- **Startup script** (`GET`/`PUT`/`DELETE …/{bot_id}/startup-script`) takes no
  `stage`, and that is not an omission. It is backed by
  `ac_bot_startup_script`, keyed `(env, entity_id, bot_id)` — one row per bot,
  not one per runtime — so the parameter would be inert.
- **Config manifest** (`GET`/`PUT`/`DELETE …/{bot_id}/config-manifest`, plus
  `GET …/config-manifest/capabilities`) takes no `stage` for the same reason
  and with the same shape: `ac_bot_config_manifest` is keyed
  `(env, entity_id, bot_id)`, one row per bot.

  Two further things a caller should know before writing against it. The group
  is behind a **deployment switch** and answers `404` where it is not enabled —
  it stays off until the manifest apply engine ships, because until then an
  accepted manifest changes nothing about the bot. And `PUT` is
  **all-or-nothing**: a document is stored only if every part of it is valid and
  supported for that bot, and the `422` carries `data.violations`, one entry per
  reason with the `location` in the submitted document that it applies to.
  `…/capabilities` is answered by the same rules the write refuses with, so it
  can never promise something a `PUT` then rejects.
- **MCP** addresses no bot at all; its six operations are keyed by
  `server_code` and `user_id`. The config write does fan out to every bot's
  **draft** device, which is correct for the same reason the writes above are
  draft-only: a release inlines its MCP credentials into the artifact, so a
  published runtime's config changes by republishing.
- **Resources, skills and routines** read or write per-bot device state and are
  still draft-only. Deferred, with reasons, in
  `specs/2026-08-15-openapi-v1-stage-addressed-bot-files/spec.md`; routines'
  stage pin is [#908](https://github.com/inclusionAI/Avernet/issues/908).

**What an operator sees is device-wide, by documented contract.** The
engine's session collection is not scoped per caller — the engine ports drop
the `user_id` filter — so an admitted operator sees every session on the
addressed runtime, including ones end users' chats created, exactly as the
internal workbench already shows an owner. Sessions created through this
surface stamp the acting caller, so they stay attributable. One caveat: a
multi-instance provider can fan a published stage out to several device
instances; a stage-addressed answer describes the addressed binding's current
instance, not the fleet (cron's fan-out stays internal).

Rejected alternatives, recorded so the question is not reopened
(`specs/2026-08-09-openapi-v1-access-expansion/plan.md`): per-caller session
scoping (the engine ignores per-user filters, and a backend-owned caller→
session index rebuilds the chat product inside an operator console);
admitting public-bot callers as operators (internal reachability is not
public authorization); `stage` as a path segment; a required `owner_id`.

Deferred, not lost — filed as issues: collaborator access to the data
categories ([#906](https://github.com/inclusionAI/Avernet/issues/906),
[#907](https://github.com/inclusionAI/Avernet/issues/907) — since 2026-08-21
these are table edits: flip those groups' `OWNER_SCOPED` rows to
`Check(level)` once the bar is decided, see **Declaring authorization**),
routines' stage
pin ([#908](https://github.com/inclusionAI/Avernet/issues/908)), publish
lifecycle ([#909](https://github.com/inclusionAI/Avernet/issues/909)),
visibility/collaborator management
([#910](https://github.com/inclusionAI/Avernet/issues/910)), delegation
([#911](https://github.com/inclusionAI/Avernet/issues/911)). The skills
group's `owner_entity_id` locator predated `owner_id`; it was reconciled to
`owner_id` on 2026-08-15 with bot-first addressing, and the retiring skills
addresses still publish the old name (spec Open Question 1, closed).

`tests/…/openapi_v1/engine_runtime/test_operator_access.py` sweeps the
operator matrix across all runtime operations covered by the shared sweep;
`…/test_stage_addressing.py` pins the stage behaviour and asserts the two
parameters sit on exactly the operations listed above — optional, in the query,
and on no retiring address that did not already have them;
`tests/community/core/engine_runtime/test_stage.py` pins the liveness rule;
`…/openapi_v1/test_stage_addressed_bot_files.py` pins the five file operations,
including that their retiring twins still read the draft.

---

## Declaring authorization (`AUTHORIZATION`, and why not on the route)

**Every public operation declares what governs it in one table, and nowhere
else** — `openapi_v1/authorization.py`. A handler declares nothing. The router
class reads the row and attaches what it calls for.

This is a deliberate **reversal** of the convention its neighbour
`admission.py` follows, where a route also names its own dependency and
`test_admission_inventory.py` holds the two in step. That redundancy is
valuable: a mislabelled table entry still refuses at the route. It was traded
for a single source, because two declarations for one fact is two things to
keep in step and 43 hand-written ones had already accumulated. If you are
adding an operation, this is the difference that will surprise you: **there is
nothing to write on the route.**

What replaces the lost redundancy is that omission is not survivable:

```text
PublicRouteNotAuthorized: GET /openapi/v1/bots/{bot_id}/new-thing has no row in
AUTHORIZATION. Every public operation must declare what governs it; add a row in
openapi_v1/authorization.py.
```

That is raised while the route's own module is *importing* — earlier than
assembly, far earlier than a request — so the application does not start. A CI
assertion catches the same mistake one step later; this catches it before
anything runs.

### The five modes, two of which are permanent

| mode | who enforces | seam attaches |
| --- | --- | --- |
| `Check(level)` | **the seam** | yes |
| `NoCheck(reason)` | nobody, deliberately | no |
| `ServiceChecked(level, where)` | a service, elsewhere — *scaffolding* | no |
| `OWNER_SCOPED` | nobody yet; resolves `(bot_id, caller)` — *scaffolding* | no |
| `INHERITED` | the address that replaced it — *scaffolding* | no |

`Check`'s level is a parameter rather than five more modes because the bars
really do differ per operation — MEMBER to drive a bot's sessions, ADMIN to
write a channel, OWNER to restart a container or delete the bot.
`NoCheck`'s reason is required because it covers two different situations: an
operation with no bot dimension at all, and one that is bot-scoped and
*intentionally* unguarded (render-screen reads serve share viewers who hold no
Editor relation). Without a written reason a reviewer cannot tell the second
from an oversight.

`INHERITED` carries nothing because there is nothing of its own to carry: a
`deprecated/` address is the replacement's *own endpoint function* re-registered
at the old path, so the row that governs it is the replacement's row. It was
called `SELF_CHECKED` until 2026-08-22, a name that claimed a check no legacy
route performs and collided with `deprecated`'s unrelated `SELF_CHECKED_ROUTES`
— which really does mean a router that checks the grant itself.

The other three are scaffolding and are deleted when their last row leaves
them. `scaffolding_row_count()` publishes how many remain, and a test fails if
a *new* row is ever added in one — the quiet way the final shape becomes
unreachable.

### What the seam does, and what it does not

`openapi_v1/bot_access.py`, on a `Check` row only:

- resolves the bot from `{bot_id}` on the path and the owner from `OwnerIdDep`
  — **the same values the handler acts on**, so no arrangement of query
  parameters aims the check at one bot while the handler acts on another;
- refuses below the level with the masked 404, byte-identical to a bot that
  does not exist;
- **fails closed on every failure.** An unresolvable bot, an unreadable
  collaborator table, an unwired injector: all refuse. The internal
  interceptor does the opposite here (`permission_skipped`, then proceeds) and
  that is not ported;
- writes one `ac_bot_collab_log` row for a non-owner action on a non-read
  method. Reads are not audited — measured against the internal surface, where
  36 of 36 `GET` routes disable the audit and 54 of 54 non-`GET` routes write
  one. An audit failure is logged and dropped: the action already happened, and
  erroring would make a retrying client apply the mutation twice.

**It carries no edit lock**, deliberately for this iteration. Locks that
channels and service publications enforce today are untouched and still fire;
a test asserts that so "no lock in the seam" cannot drift into "no lock
anywhere".

Two things stay outside on purpose: bot-*type* gating (`SUPPORTED_BOT_TYPES`,
answered 501) is a capability question, and whether a *machine* caller is
admitted is `admission.py`'s, with its own seam.

### A router that exists but is not mounted

`openapi_v1/task` declares three operations that `build_public_router` never
mounts — collaboration answers under `/api/v1` today, and its `/openapi/v1`
twin waits for the gateway to declare that domain. It still carries the route
class, so it still needs rows, and `UNMOUNTED_OPERATIONS` names them so the
orphan check does not read them as decisions left behind by a rename.

Keeping the route class on an unmounted router is the point: **whoever mounts
it later cannot do so unguarded** — they have to replace its placeholder rows
with a real decision. Dropping the class would have been the smaller diff and
would have let the surface grow an unchecked group the day someone wired it up.
A test asserts none of those operations is live, so mounting one must delete
its entry.

### Adopting it: what the migration found

82 rows are `Check` today. `ServiceChecked` fell from 92 to **25**, and those
25 are named in `_DEFERRED_OPERATIONS` with a reason each — the burn-down test
asserts that set exactly, so a row cannot join it quietly.

The plan expected 10 deferrals and got 25. Every addition came from tracing a
group rather than reading its row, which is the lesson worth keeping:

- **A recorded level is documentation, and documentation is wrong sometimes.**
  Three skills rows cited a module that never checked them. Re-read the cited
  code before flipping; a keyword search is not a trace.
- **A bar is not always a level.** Ten session operations ask for
  owner-or-collaborator **or**, at draft stage, a BCN-verified friendship. The
  seam adjudicates one level, so `Check` there would have closed a real path
  instead of relocating it.
- **A check with more than one caller does not move, it gets joined.**
  `bot_skill_asset_service` is reached from `/openapi/v1`, from the retiring
  twins, and from `/api/skills` — which no row governs. Deleting its check to
  "finish" the migration would have stripped authorization from two surfaces.
  The row still moves: it names who the *declared* authority is.
  **And a service that is reached only through the seam today can still be the
  wrong place to delete from.** `ServicePublicationFacadeProtocol` has no caller
  outside `/openapi/v1`, but its Service API contract says "Resolve, authorize
  and orchestrate" — deleting the refusal left the contract promising a
  behaviour the implementation had lost, to every future caller.
  `CollaboratorService`'s editor methods are the same story without even the
  docstring to catch it: who may remove an editor is collaboration policy, and
  `AGENTS.md` says delivery adapters do not own domain policy, so an HTTP table
  cannot be its only home. Contracts and layering are the authority;
  **reachability today is not the test.**

  **Ask this of every group, every time.** `skill_set_control_plane` got the
  same treatment as an afterthought and its check was deleted; `/api/skillsets`
  reaches it on four routes with no `CollaboratorPermissionInterceptor`, taking
  `entity_id` and `bot_id` from the query string, so the deletion opened another
  owner's SkillSet to any authenticated caller — a read on three routes and a
  write on one. A P1 review finding caught it. The difference between the two
  groups was one question asked and not asked: *who else calls this?*
- **Two gates at one bar cannot be told apart by their answers.** Where a
  service check stays, a test makes the two disagree and asserts the seam is
  the one that decided.
- **A joined check is paid for twice, so price it rather than assume it.** The
  26 engine-runtime rows resolve the bot in the seam and again in
  `EngineRuntimeRelay.resolve_bot`. Per admitted request that is one extra
  `BotService.get_bot` — row read, device-binding fetch, template fetch — plus,
  **for a collaborator only**, one role query; both level resolvers
  short-circuit on `user_id == owner_id`, so the owner path adds no
  adjudication query at all. Caching the seam's read would recover only the row
  read, because `BotFacts` is built from the binding and template the seam
  never fetches. The removable half is the relay's `require_bot_operator`, and
  it is load-bearing for the **deferred** session rows: `_resolve_session_backend`
  turns its refusal into the draft-stage friendship check. So the redundancy
  ends when those rows migrate — not through a cache, and not through a
  "the seam already checked" flag, which is the bypass argument this whole
  mechanism exists to remove. `engine_runtime/gating.py` carries the accounting
  at the call site.

  **Price it, then delete the ones nothing depends on.** Render-screens is the
  other half of the same rule: its handlers each called `resolve_readable_bot`,
  discarding the result, and on the three `Check(MEMBER)` mutations that was a
  pure re-read of what the seam had just proved. Removable where engine-runtime
  was not, because nothing else stood on it — one caller, no retiring twin, and
  an adapter-local helper rather than a Service API contract. The read keeps it:
  its row is `NoCheck` by design, so it is the only gate that route has. **The
  question to ask of a duplicate is not "is it redundant today" but "what
  breaks when it is gone"** — for `require_bot_operator` the answer was the
  deferred sessions fallback; here it was nothing.

What did not move, and why, is in that feature's `spec.md` *Out of Scope*:
6 harness, 10 sessions, 3 skills, 3 authorized-apps, 2 product chats, 1
connection.

- **An `OWNER_SCOPED` group** ([#906](https://github.com/inclusionAI/Avernet/issues/906),
  [#907](https://github.com/inclusionAI/Avernet/issues/907)) is still ahead, and
  is **not** a migration: it changes behaviour, because collaborators start
  getting through where they used to get a 404.

Specs: `specs/2026-08-21-openapi-v1-collaborator-authorization-seam/` (the
mechanism) and `specs/2026-08-22-openapi-v1-adopt-collaborator-seam/` (the
adoption).

## Addressing rule

**Every bot-scoped operation is addressed
`/openapi/v1/bots/{bot_id}/<component>/…`.** The bot comes first; the
component's literal name hangs off it.

```text
/openapi/v1/bots                        # the account-level collection
/openapi/v1/bots/{bot_id}               # one bot
/openapi/v1/bots/{bot_id}/<component>   # …one component of that bot
```

A bot is the noun this API is about, so the address names the bot before it
names what about the bot, and every operation on one bot shares one prefix. The
bots component's own sub-resources (`/status`, `/passport`, `/restart`,
`/auth-status`, `/engine/config`) sit in that same third position, alongside
`sessions`, `skills` and the rest — a property of the bot is addressed no
differently from a component of it.

**The bot is always a path parameter.** Never a query parameter, never a body
field. Which bot an operation acts on is the *address*, not an argument to the
call, and a client told to put the same id in two places has been told the
address twice. That was the concrete defect this rule fixed: nine operations
took `bot_id` in the query string and one took it in a request body, all of them
under a path that already had somewhere to put it.

**Why it is worth the migration.** Two things follow from it that the old
component-first shape could not give.

*Authorization becomes one mechanism.* `require_granted_bot` reads the addressed
bot off the path, the same way, for every operation on the surface. Under the
old shape seven operations carried their bot somewhere that dependency could not
see it and checked the grant inside their handlers instead — two mechanisms
doing one job, which had already cost one real defect. That was `TODO(#960)`;
bot-first addressing narrowed it from seven operations to four. The four that
remain are the `{skill_id}` skills operations: they resolve by `(skill, actor)`,
so the addressed bot's *owner* arrives on the record rather than on the wire —
a collaborator reaches a skill on someone else's bot routinely — and there is
nothing for the shared dependency to look a grant up against until that read has
happened. They are named in `admission.SKILL_SCOPED_OPERATIONS` and check the
grant in their handlers. **Do not remove those checks believing the dependency
covers them; it does not.**

*The reserved-name list shrinks to the operations that earn it.* Because
`{bot_id}` is a single wildcard segment, any literal served in that position is
a name no bot can be called. Component-first put **every** component name there.
Bot-first moves them one segment deeper, where they collide with nothing.

The operations that keep a literal in that segment are the ones with no single
bot to name: creating a bot, listing them, `check-name`, `ceiling`, the
`authorized` groups, the tenant-wide `mcp` catalogue, `logs` (a trace query that
reads *across* bots), and `loadtest`. They are the only things that do.

Specified in `specs/2026-08-15-openapi-v1-bot-first-addressing`; a test
(`tests/…/openapi_v1/test_path_convention.py`) asserts the rule against the
generated document, so a route that breaks it fails there rather than in review.

**Reserved names.** Because the `bots` component keeps the bare
`/openapi/v1/bots/{bot_id}`, a bot whose id equals a literal in that segment is
unreachable at that address. The same test asserts this list still equals the
literals the routes actually publish:

<!-- reserved-component-names -->
```text
all  approvals  authorized  catalog  ceiling  check-name  connection  engine  identity
loadtest  local  logs  market  metadata  mcp  models  resources  routines  sessions  skills  spaces
source-credentials  with-manifest  work-order-notifications  work-orders
```

Eight of those twenty-five — `approvals`, `connection`, `engine`, `identity`,
`models`, `resources`, `routines`, and `sessions` — are held **only by the
retiring addresses**. Bot-first addressing moved every bot-scoped component out
of that segment. The tenant-level Skill Workbench status route now keeps
`skills` current at this level, so once the deprecated addresses are removed the
list is the seventeen that remain:

```text
all  authorized  catalog  ceiling  check-name  loadtest  local  logs  market  metadata  mcp  skills
source-credentials  spaces  with-manifest  work-order-notifications  work-orders
```

They are still reserved today, and the list above is the accurate one: a bot
whose id is `sessions` is unreachable at `/openapi/v1/bots/sessions` for as long
as the old address answers there.

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
> sub-groups **before** the bots group, so `/openapi/v1/bots/check-name`
> resolves ahead of the `/openapi/v1/bots/{bot_id}` wildcard. Under bot-first
> addressing only the groups that genuinely keep a literal in that segment
> depend on it — the account-level ones listed above, and the retiring addresses,
> which are component-first by definition. The current bot-scoped groups are all
> `{bot_id}`-first and could be mounted in any order. Keep any new group above
> the bots router anyway, rather than reasoning about the exception each time.

## Retiring addresses (the migration window)

**Nothing was removed.** Every address this surface answered before bot-first
addressing still answers — same shape, same parameters, in the same places,
including the `bot_id` in a query string and the one in a request body that the
rule above exists to remove. Forty-two operations, served by
`openapi_v1/deprecated/` — the forty-one re-addressed by bot-first addressing,
plus one that retires a *method*: the auth-status poll became a POST at the
same path (it creates the bot on ISSUED, so it was never a read), and the GET
spelling with its query parameters is kept the same way.

They are not aliases. A retiring address publishes the *old* parameter names in
the *old* locations and translates, so a client that has not moved keeps working
unchanged. `tests/…/openapi_v1/test_legacy_parity.py` drives each retiring
address and its replacement through the same application and asserts they reach
the same decision; that assertion is the whole of the compatibility promise.

How a caller finds out:

| Channel | What it carries |
|---|---|
| The published document | `deprecated: true` on every retiring operation, so a generated client can annotate it |
| Response headers | `Deprecation: @1786752000` and `Sunset: Sun, 15 Aug 2027 00:00:00 GMT`, on **every** response from the address, failures included. The two are spelled differently on purpose: `Deprecation` is an RFC 9745 structured-field date (`@` and seconds since the epoch), `Sunset` an RFC 8594 HTTP-date. |

**The window runs 2026-08-15 → 2027-08-15.** Removal is driven by traffic, not
by the date: the public access log says when an address has no callers left, and
that is when it goes. The sunset is the outer bound a client can plan against,
not a countdown.

**Migration is mechanical.** Move `bot_id` from the query string (or, for
`POST …/routines`, from the request body) into the path, and put the component
after it:

```text
GET  /openapi/v1/bots/sessions/{bot_id}          →  GET  /openapi/v1/bots/{bot_id}/sessions
GET  /openapi/v1/bots/resources?bot_id=b-1       →  GET  /openapi/v1/bots/b-1/resources
POST /openapi/v1/bots/skills/upload?bot_id=b-1   →  POST /openapi/v1/bots/b-1/skills
```

For the auth-status poll the move is the method and where the echoed create
attributes go — from the query string into a JSON body, same names, same
meanings:

```text
GET  /openapi/v1/bots/b-1/auth-status?engine=openclaw&bot_name=N
  →  POST /openapi/v1/bots/b-1/auth-status   {"engine": "openclaw", "bot_name": "N"}
```

One behaviour change ships with the method move, on both spellings: while
the authorization service has no status for the bot yet (the Passport is not
ready), the poll answers 200 with `status: "PENDING"` and a message saying so
— keep polling — where this operation used to answer 502. The internal
`/api/bots/auth-status` route is unchanged.

Two renames come with it, and they are the only parameter changes in the
migration: `skills` spells its owner locator `owner_id` like the rest of the
surface (it was `owner_entity_id`), and `PUT …/approvals/mode` no longer takes
the `session_key` it never read.

All responses use the `Envelope[T]` / `Page[T]` shapes from
`openapi_v1/contracts.py` unless noted (binary streams bypass the envelope).

### ✅ totalfrank · P1 — bots · `openapi_v1/bots/router.py` — **IMPLEMENTED (PR #494; startup script #926)**
The Bot core routes are wired to internal services. Kept here as the reference shape for
the other six: this is what "done" looks like per category.

| Method | Path | Purpose | Success |
|---|---|---|---|
| POST | `/openapi/v1/bots` | Create a bot; may need Passport authorization | `201 Envelope[Bot]` or `202 Envelope[BotAuthPending]` |
| GET | `/openapi/v1/bots` | List caller's bots (`keyword`, `engine`, `status`, paged) | `Envelope[Page[Bot]]` |
| GET | `/openapi/v1/bots/check-name` | Bot-name availability (`name`) | `Envelope[NameCheck]` |
| GET | `/openapi/v1/bots/ceiling` | Selected Space's Bot ceiling | `Envelope[Ceiling]` |
| GET | `/openapi/v1/bots/{bot_id}` | Bot details | `Envelope[Bot]` |
| PUT | `/openapi/v1/bots/{bot_id}` | Update bot (engine immutable) | `Envelope[Bot]` |
| PUT | `/openapi/v1/bots/{bot_id}/space` | Change the Bot's owning Business Space | `Envelope[BotSpaceAssignment]` |
| DELETE | `/openapi/v1/bots/{bot_id}` | Delete bot | `Envelope[Deleted]` |
| POST | `/openapi/v1/bots/{bot_id}/restart` | Restart (re-provision device) | `Envelope[Bot]` |
| POST | `/openapi/v1/bots/{bot_id}/auth-status` | Poll Passport auth (attributes echoed in the body); completes creation when ISSUED — a write, hence a POST | `Envelope[BotAuthStatus]` |
| GET | `/openapi/v1/bots/{bot_id}/auth-status` | Retiring spelling of the poll (attributes in the query string); deprecated — use the POST | `Envelope[BotAuthStatus]` |
| GET | `/openapi/v1/bots/{bot_id}/status` | Runtime / device readiness | `Envelope[BotStatus]` |
| GET | `/openapi/v1/bots/{bot_id}/passport` | Get the bot's Agent Passport | `Envelope[Passport]` |
| GET | `/openapi/v1/bots/{bot_id}/engine/config` | Read engine config (free-form JSON) | `Envelope[dict]` |
| PUT | `/openapi/v1/bots/{bot_id}/engine/config` | Write engine config (free-form JSON) | `Envelope[dict]` |
| GET | `/openapi/v1/bots/{bot_id}/startup-script` | Read the bot's startup script | `Envelope[StartupScript]` |
| PUT | `/openapi/v1/bots/{bot_id}/startup-script` | Set/replace it; takes effect next start | `Envelope[StartupScript]` |
| DELETE | `/openapi/v1/bots/{bot_id}/startup-script` | Clear it | `Envelope[Deleted]` |
| GET | `/openapi/v1/bots/{bot_id}/config-manifest` | Read the bot's configuration manifest (empty when it has none) | `Envelope[ConfigManifest]` |
| PUT | `/openapi/v1/bots/{bot_id}/config-manifest` | Set/replace it; all-or-nothing, `422` lists every violation | `Envelope[ConfigManifest]` |
| DELETE | `/openapi/v1/bots/{bot_id}/config-manifest` | Clear it | `Envelope[Deleted]` |
| GET | `/openapi/v1/bots/{bot_id}/config-manifest/capabilities` | Which manifest constructs this bot accepts | `Envelope[ConfigManifestCapabilities]` |

#### Space-scoped Bot quota

Cloud Bot creation is charged to the selected Business Space. Personal Space
reuses the owner's existing per-user `bots_ceiling` policy and includes both its
numeric Space rows and legacy `space_id IS NULL` rows. Team Space counts every
owner's non-deleted cloud Bot assigned to that exact Space, defaults to 20, and
may be overridden independently through the operator-only
`PUT /api/v1/access/spaces/{space_id}/bots-ceiling`; `DELETE` removes that
override and restores 20. Desktop Bots do not consume either quota.

The public create, authorization-completion, manifest-create, and move-in paths
check capacity against the target Space. The final count and database write are
serialized by a short Space-scoped distributed lock, so parallel requests
cannot both consume the last slot. Existing over-limit Spaces remain readable
and operable; only a new create or move-in is refused. A quota refusal is a 409
whose `data` includes the Space identity plus its `ceiling` and current `used`
count; unavailable quota coordination fails closed with 503. The
legacy `/api` creation path is unchanged and retains its prior owner/device
checks.

#### Creating an Application Coding Bot

Application Coding uses `engine=claude_code` and keeps template-specific creation parameters under `engine_properties.template` rather than flattening them into the Bot request:

```json
{
  "bot_name": "my-app-coding-bot",
  "bot_desc": "application coding bot",
  "engine": "claude_code",
  "cluster_name": "ACRA",
  "bot_type": "personal",
  "engine_properties": {
    "template": {
      "devflow_workflow": "app-flow",
      "yuque_kb_repos": [],
      "code_repos": [],
      "bot_template_config": {
        "preset_capabilities": {},
        "ext_config": {"thetaKey": "value"}
      }
    }
  }
}
```

If create returns 202, echo the same `engine_properties` object in the POST `/{bot_id}/auth-status` body. Legacy top-level `template_type` / `template_config` are not public contract fields and return 422. The HTTP adapter maps `engine_properties.template` to the internal creation contract; it is not the server-derived runtime `extra_properties` envelope.

#### Owning Business Space reassignment

`PUT /openapi/v1/bots/{bot_id}/space` explicitly requires `user_id` and a JSON
body containing one numeric `space_id >= 1`. The target id comes from
`GET /openapi/v1/spaces`; moving a Bot back to personal ownership uses that
user's numeric PERSONAL Space id rather than `null` or an implicit sentinel.
Only the Bot owner may perform the mutation. The owner must still be a member
of the target Space, and a PERSONAL target must belong to that owner. An
application is admitted with `GRANT_CHECKED_OWN_BOT`, so it must hold a live
grant from the acting user for that user's owned Bot. Desktop/local Bots cannot
move to TEAM Spaces and return 409. Repeating the current assignment is a
successful no-op with `changed=false`; a real persistence failure is propagated
rather than reported as success.

Production now binds `BusinessSpaceContextProtocol` to the Spaces
Service-API-backed adapter. Bot creation and `/openapi/v1/bots/all` therefore
resolve numeric PERSONAL/TEAM Space ids through the same membership-gated
contract; accounts whose PERSONAL Space has not yet been initialized retain the
legacy `personal:<user_id>` read fallback without creating data as a GET side
effect.

This operation is deliberately a **narrow assignment update**, not the full
cross-space migration described by the Bot Workshop integration plan. It does
not reconcile collaborators/editors or provide a multi-domain transaction and
compensation workflow. Do not mark “Bot migrate” complete or use this operation
where target-Space membership must replace existing collaborator grants; that
requires the B-line migration Application Service owned jointly with the
Business Space/collaboration team. Regenerate
`src/gateway/configs/schemas/bots.openapi.json`, then copy the schema and matching
route/security configuration to the independently managed OCB/Sofapy Gateway;
Avernet's broad `/openapi/v1/bots/**` rule already covers this path.

#### Startup script — the promises a caller cannot infer from the schema

The script is **appended to the container's start sequence**, after the
platform's own boot steps (bootstrap, engine install, service start,
watchdog) and before the start is reported. Everything below follows from
that one design choice, and none of it is visible in the OpenAPI document.

- **It runs on every start the platform composes, and the platform does not
  dedupe — so it must be idempotent.** Create, restart and republish each
  compose a fresh start command and run the script again.
- **Editing takes effect on the next start, never on a running container.**
  A script written after a bot was created reaches a container only once that
  bot restarts. The first write therefore always needs a restart.
- **A failure degrades rather than blocks — the script's *execution*, that is.**
  A non-zero exit or a crash leaves the agent running: the script is
  guarded so it cannot change the boot's outcome, and it is skipped entirely if
  the boot itself failed. That is not a promise to start anyway if the platform
  *cannot read* your stored script: a start that could not resolve it fails
  rather than bringing up a bot that looks ready and is not provisioned.
- **Limits:** body ≤ **24 KiB** (413 above that, naming the limit). There is
  currently **no cap on the run itself**: the per-run `timeout` was removed, so
  the script runs to completion however long it takes. The start only reports
  once the script exits, and the 600s publish budget the start reports into
  does not stop the script — a run that overstays it leaves the publish marked
  failed while the script (and the container's boot) carries on. A script that
  never exits holds the start open indefinitely, so bound your own long-running
  work (background it with `something &` if it should outlive the start).
  Interpreter is `bash`; the body runs as `admin`, the same user every platform
  step runs as.
- **Each run brackets itself in the log.** The platform writes a timestamped
  `[startup_script] started at ...` line before your body runs and a
  `[startup_script] finished at ... rc=<exit code>` line after it, into the
  same log the body's output goes to — with no cap on the run, those markers
  are how you tell a still-running script apart from a finished one.
- **Do not put secrets in the body.** This is a hard requirement, not advice.
  The body is stored as written, and it is **logged in recoverable form**: the
  backend elides it from its own payload log, but the start command travels to
  the device service, which logs the first 1024 characters of the rendered hook
  at INFO — and the base64 body typically begins inside that window, so the
  opening bytes of your script decode straight out of those logs. The
  backend-side elision does not and cannot cover a downstream log. There is no
  by-reference secret mechanism yet, so anything secret must reach the container
  some other way.
- **There is no API to read the run's result yet.** The script's output goes to
  `/home/admin/logs/startup_script.log` inside the container, and that is the
  only place to see it. A read endpoint was deliberately left out of this
  change: the script shares one exit status with the platform's boot, so any
  such endpoint reports the whole start sequence rather than the script alone,
  and resolving *which* start to report is not solved for a published service
  bot. Tracked as follow-up work.
- **Two kinds of bot cannot run one, and `supported` says which.** A write to
  either is refused with **409** rather than stored where it would silently
  never run; `GET` still answers for them, carrying the reason, so a caller can
  find out before attempting the write.

  - a **teclaw** bot (any engine the platform provisions as teclaw) — its
    container is provisioned without a start sequence at all, so there is
    nothing for a script to ride on;
  - a **desktop** bot (`bot_type == "desktop"`) — its start command is built on
    a separate path that does not carry the stored script.

  Support is answered from the bot's **engine and type** and never from its live
  container, so the answer is the same before the first start, during a restart,
  and while an unrelated lookup is failing. That choice has a cost, and there
  are two known cases where `supported: true` is optimistic — in both, the
  script is stored and the write accepted, but nothing runs it:

  - a legacy **ARCA-direct** bot, created before the BaaS rollout, whose
    container is not built through the shared start sequence. Every bot created
    today is BaaS-backed unless it is teclaw or desktop;
  - a deployment whose containers come from a **`LOCAL`-type BaaS template**
    (single-machine / singlebox installs). The backend composes and sends the
    hook exactly as it does for any other bot, but BaaS skips hook dispatch for
    `LOCAL` and hands off to a `container_ready` callback that does not run it.
    Whether a given install is affected depends on its BaaS template's
    configured type, which is deployment data rather than a property of the bot,
    so this check — answered from the bot record alone — cannot see it.

  Both are the same shape: the refusal covers the cases visible in the bot
  record, and a provisioning path that bypasses the shared start sequence is not
  one of them.
- **Deleting the bot deletes its script, and a failed delete is not reported as
  success.** Bot deletion is a soft update, so nothing cascades to the script
  row — it is removed explicitly, because the body is stored decoded and an
  orphan row keeps plaintext executable content past the life of its owner.

  Inheritance by a later bot is *not* among the reasons, though an earlier
  version of this document said it was: a caller may supply `bot_id` on create
  and soft-deleted bots read as absent, but the uniqueness constraint described
  below means such a create is refused rather than granted the tuple.

  Unlike the bot's skills and skill sets — inert metadata, swept after the
  deletion with failures logged and tolerated — this removal runs **before**
  anything destructive and its failures **propagate**. A deletion that could not
  clear the script fails with the bot still intact and retryable, rather than
  returning success over a row that can still execute.

  It runs a **second** time after the soft delete, mirroring the app-grant
  revocation's two-sweep handling of the same race, which stops a *later* `PUT`
  from passing its existence check.

  The sweeps alone are not enough, because they cannot cancel a request that
  already passed its check and is still in flight — that one can commit after
  both of them. So the write **re-checks the bot after storing** and withdraws
  its row if the bot is gone. A `PUT` that loses this race answers **404**: the
  bot it addressed no longer exists, so reporting a stored script for it would
  be a wrong answer rather than a successful write. If the withdrawal itself
  cannot complete, that failure surfaces instead of being reported as a clean
  404 — the second sweep is a backstop only while that deletion is still
  running, and a write landing after it has finished has nothing else coming
  for its row.

  The re-check asks only whether the bot is gone, and that is sufficient
  because the identifier cannot change hands underneath it. An earlier design
  stamped each row with the writing bot's `ac_bots.id` and compared it on every
  read, on the assumption that `bot_id` is reusable. It is not, so no stamp is
  stored and no read compares one.

  **A stale row cannot execute, and the reason is the key rather than a
  read-time check.** `ac_bots` carries a uniqueness constraint over
  `(bot_id, entity_id, env)` — `uk_bot_id_entity_id_env` in the production
  schema, declared on the ORM as the tenant-scoped
  `uk_bot_id_entity_id_env_tenant` so `create_all` deployments get it too.
  `is_delete` is not part of that key and the repository has no hard delete, so
  a soft-deleted bot goes on occupying its tuple forever and a create cannot
  reissue it. A script row is filed under exactly that tuple, so there is no
  later bot that could inherit one: the row a failed sweep leaves behind is
  unreachable rather than dangerous.

  The sweeps and the re-check remain — an orphan is still plaintext executable
  content nobody should be storing, and the deletion path refuses to report
  success over one — but they are hygiene. What stands between a stale row and
  someone else's container is the constraint.

  The legacy `default`-bot delete is a restart rather than a deletion and keeps
  its script through both sweeps, matching how its skills and config are already
  preserved.
- **Re-running on restart is inherited, not guaranteed by this feature.** The
  script re-runs wherever the platform's own start sequence re-runs. On a
  provider whose restart is destroy-and-create that is every restart; on one
  that restarts a container in place, the sequence does not re-run and
  neither does the script.

_Deliberately **not** exposed on bots: top-level `template_type`/`template_config` and `engine_options` on create (nothing
downstream reads `BotCreateSpec.extra_properties` yet, so advertising it would
promise something the server ignores), and `cluster_name`/`engine_options` on
update. With `extra="forbid"` these are now a 422 rather than a silent drop._

_Internal `/api/bots` changed too, all intentional and covered by #494: the
create preflight also rejects a taken bot name (so a duplicate fails **before**
the external Passport application); create persists the configured engine
registry widened to include the bot's own active engine; update's duplicate-name
check compares owner **and** `bot_id` together; deleting the default bot raises
`BotOperationNotAllowedError` (internal response shape unchanged, public → 409)._

### ✅ Bot Workshop · P1 — channels (6 endpoints) · `openapi_v1/channels/router.py` — **IMPLEMENTED (2026-08-19)**
Bot-scoped draft DingTalk Channel configuration. Every operation requires an
explicit `user_id`; `owner_id` is optional and addresses a Bot shared with the
caller. Human and application callers are admitted through
`GRANT_CHECKED_ADDRESSED_BOT`, so an application must hold a live grant for the
addressed `(owner_id, bot_id)` pair. Reads allow the owner or an operable
collaborator; writes additionally require `ADMIN` collaborator permission. If the
Bot has collaborators, the caller must also hold its Bot edit lock; otherwise
the write returns the standard `423 Edit lock required` envelope.

Only `dingding` and the `draft` stage are public. `verify` and `online` remain
publication outputs. Creates start `inactive`; status changes synchronize the
runtime, and deleting an active Channel deactivates it first. `client_secret`
is write-only and reads expose only `has_client_secret`; the internal
`aix_preview_url` is never projected. The collection is intentionally
unpaginated (`Envelope[list[Channel]]`) because the current product contract
manages the Bot's small configured set rather than a tenant-wide inventory.

| Method | Path | Purpose | Success |
|---|---|---|---|
| GET | `/openapi/v1/bots/{bot_id}/channels` | List draft DingTalk Channels | `Envelope[list[Channel]]` |
| POST | `/openapi/v1/bots/{bot_id}/channels` | Create an inactive Channel | `201 Envelope[Channel]` |
| GET | `/openapi/v1/bots/{bot_id}/channels/{channel_id}` | Read one Channel | `Envelope[Channel]` |
| PATCH | `/openapi/v1/bots/{bot_id}/channels/{channel_id}` | Partially update config | `Envelope[Channel]` |
| DELETE | `/openapi/v1/bots/{bot_id}/channels/{channel_id}` | Deactivate and delete | `Envelope[Deleted]` |
| PUT | `/openapi/v1/bots/{bot_id}/channels/{channel_id}/status` | Activate or deactivate | `Envelope[Channel]` |

**Release gates:** deploy the Stage 3 DDL before Backend code and copy the
regenerated `bots.openapi.json` plus matching route/security configuration to
the independently managed OCB/Sofapy Gateway. Avernet's broad
`/openapi/v1/bots/**` forwarding/security rule already covers these paths.

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

The unified marketplace search also exposes
`POST /openapi/v1/bots/market/mcp-servers`. Its JSON request supports the full
legacy catalogue filter set: `keyword`, `page_num`, `page_size`, `server_codes`,
`platform_server_codes`, `run_modes`, `statuses`, `transport_protocols`,
`host_platforms`, `owners`, `network_types`, `categories`, `tenants`, and
`tags`. Requested network types are intersected with the public allowlist
(`INTERNET`, `OFFICE`); a request containing only hidden network types returns
an empty page. Each `McpMarketItem` is a lossless snake-case equivalent of the
corresponding legacy `GET /api/mcp/market/list` item, including tags and future
catalogue extension fields, while retaining the same `extInfo` removal rule.

`McpServerDetail` is the snake-case equivalent of the legacy
`GET /api/mcp/market/detail` business payload. In addition to the lightweight
list fields and `tools`, its explicit schema covers the legacy catalogue fields,
including `source`, `icon`, `docs`, `endpoints`, `vendor`, `status`, `run_mode`,
`host_platform`, `platform_server_code`, `host_app_name`, `category`, `site`,
marketplace `tenant`, `access_level`, `stdio_configs`, business/domain codes,
ownership records, tags, repository metadata, and launch channels.

Compatibility is lossless: endpoint `headers`, identity `user_id`, endpoint
records on every network present in an otherwise visible server, malformed
legacy values, and future catalogue extension fields are retained. Known
catalogue object keys are translated from camelCase to snake_case; opaque maps
such as endpoint headers, environment-variable names, and tool declarations keep
their original keys. As on the legacy route, tool input-schema `extInfo` is
removed, and a server whose declared network types are all outside the API's
allowlist still resolves as not found. The permissions operation remains the
authoritative caller-specific permission check even though catalogue
`access_level` is also retained in detail for legacy payload compatibility.

_Delivered decisions (PR #610): paths stay nested (`/openapi/v1/bots/mcp/...`);
`sync_mode` dropped from the write body (no single-device push path — `extra=
"forbid"` makes it a 422); a failed device push rolls the write back and answers
502 (mirrors the internal surface); `endpoint_env`/`transport_protocol` are
strict enums (`PROD`/`PRE`, `SSE`/`STREAMABLE_HTTP`). **Preserved fail-open:** a
marketplace outage still reports the caller as permitted (advisory endpoint; the
MCP server enforces) — pinned by a test so it reads as a decision, not a bug._

### 🟩 lucas-xzp · P1 — resources (7 endpoints) · `openapi_v1/resources/router.py`
The bot's workspace files and folders; storage location never exposed. Every
operation is addressed by a workspace-relative `path` query parameter; the bot
is on the path and `user_id` is required on all seven.
| Method | Path | Purpose | Success |
|---|---|---|---|
| GET | `/openapi/v1/bots/{bot_id}/resources` | List a directory (`path`, `type`, paged) | `Envelope[Page[FileEntry]]` |
| GET | `/openapi/v1/bots/{bot_id}/resources/stat` | One entry's metadata (`path`) | `Envelope[FileEntry]` |
| POST | `/openapi/v1/bots/{bot_id}/resources/upload` | Upload raw bytes (`application/octet-stream`, `overwrite`) | `201 Envelope[FileEntry]` |
| GET | `/openapi/v1/bots/{bot_id}/resources/download` | Download bytes (**raw, not enveloped**) | `application/octet-stream` |
| GET | `/openapi/v1/bots/{bot_id}/resources/preview` | Preview as text (1 MB cap → 413) | `Envelope[Preview]` |
| POST | `/openapi/v1/bots/{bot_id}/resources/mkdir` | Create a directory | `201 Envelope[FileEntry]` |
| DELETE | `/openapi/v1/bots/{bot_id}/resources` | Delete a file or directory | `Envelope[Deleted]` |

_Note: upload is finalized as a raw `application/octet-stream` body (not
multipart). This diverges from PR #363's multipart summary — implementation
follows the route; switching to multipart would be a contract change._

_Note: the group carries **no record ids**. #1001 made the engine the source of
truth for files, which left `resource_id` permanently empty on every file
response and three id-addressed routes that only ever resolved link records.
Links left this surface, so the id left the contract rather than being reported
as `""` — an empty string standing in for "no id" is a sentinel a caller cannot
tell from a real address until it fails. `GET /stat` replaces both the
id-addressed single lookup and `check-name`: it answers existence against the
workspace, the same authority the listing reads, so the two cannot disagree._

_Note: `Page` here is applied by the backend over a whole directory listing —
the engine's `ListDirRequest` carries no page, limit or cursor. `page_size`
bounds the response, not the device round trip, and a later page costs what the
first one costs. That is proportionate only because listing is non-recursive; a
`recursive=true` option would have to revisit it._

### 🟪 totalfrank + lucas-xzp · P3 — skills, co-owned (six ratified operations) · `openapi_v1/skills/router.py`

The public surface is a Bot-owned `local://` Local Skill lifecycle. It is not a
catalog, marketplace, Git/Center installation surface, or a general Skill Set
API. The collection's optional `active` filter is the only Active-list
mechanism. Every operation requires a verified principal, is owner/Bot scoped,
and uses the standard `Envelope` / `Page` contract.

| Method | Path | Purpose | Success |
|---|---|---|---|
| GET | `/openapi/v1/bots/{bot_id}/skills` | List exact Bot-owned Local Skill metadata (`bot_id`, optional owner locator, `active`, `keyword`, paged) | `Envelope[Page[Skill]]` |
| POST | `/openapi/v1/bots/{bot_id}/skills` | Create or safely replace one raw `application/zip` Local Skill package | `201 Envelope[SkillUpload]` / `200` replacement |
| GET | `/openapi/v1/bots/{bot_id}/skills/{skill_id}` | Read public metadata for one deployment-wide Skill ID | `Envelope[Skill]` |
| POST | `/openapi/v1/bots/{bot_id}/skills/{skill_id}/activate` | Set desired Active state and synchronously reconcile runtime | `Envelope[SkillState]` |
| POST | `/openapi/v1/bots/{bot_id}/skills/{skill_id}/deactivate` | Set desired Inactive state and synchronously reconcile runtime | `Envelope[SkillState]` |
| DELETE | `/openapi/v1/bots/{bot_id}/skills/{skill_id}` | Recoverably delete one Inactive Local Skill | `Envelope[Deleted]` |

`413101` is documented only on raw ZIP upload. The stable Local Skill business
subcodes are `400101`, `404000`, `409101`–`409104`, `413101`, `502101`, and
`502102`; existing public categories retain their `xxx000` codes. Generated
OpenAPI is contract-tested to expose exactly these six operations.

**Release gate — do not mark Track B complete yet.** The cleanup-work table DDL
from #725 must be applied and verified before application rollout, and the real
owner/collaborator acceptance still needs approved pre-production credentials
and Bot containers. See the [English runbook and rollback procedure](skills-track-b-preprod-acceptance.md).

### 🟩 lucas-xzp · P1 — routines (7 endpoints) · `openapi_v1/routines/router.py`
Scheduled/triggered agent tasks (the former "cron"); trigger is a nested object.
| Method | Path | Purpose | Success |
|---|---|---|---|
| GET | `/openapi/v1/bots/{bot_id}/routines` | List (`bot_id`, `status`, paged) | `Envelope[Page[Routine]]` |
| POST | `/openapi/v1/bots/{bot_id}/routines` | Create | `201 Envelope[Routine]` |
| GET | `/openapi/v1/bots/{bot_id}/routines/{routine_id}` | Get | `Envelope[Routine]` |
| PATCH | `/openapi/v1/bots/{bot_id}/routines/{routine_id}` | Update (partial) | `Envelope[Routine]` |
| DELETE | `/openapi/v1/bots/{bot_id}/routines/{routine_id}` | Delete | `Envelope[Deleted]` |
| POST | `/openapi/v1/bots/{bot_id}/routines/{routine_id}/run` | Run now | `Envelope[RoutineRun]` |
| GET | `/openapi/v1/bots/{bot_id}/routines/{routine_id}/runs` | Execution history (paged) | `Envelope[Page[RoutineRun]]` |

### 🟩 lucas-xzp · P2 — identity (3 endpoints) · `openapi_v1/identity/router.py`
Read/write a bot's identity markdown files (RULES, SOUL, …), `file_type` is an
enum whitelist. No own Track A stage — scoped by bots isolation (Stage 1 ✅).
| Method | Path | Purpose | Success |
|---|---|---|---|
| GET | `/openapi/v1/bots/{bot_id}/identity` | List identity files + whether each exists | `Envelope[IdentityFileList]` |
| GET | `/openapi/v1/bots/{bot_id}/identity/{file_type}` | Read one identity file | `Envelope[IdentityFile]` |
| PUT | `/openapi/v1/bots/{bot_id}/identity/{file_type}` | Overwrite one identity file (`content`) | `Envelope[IdentityFileRef]` |

### ✅ loadtest (2 endpoints) · `openapi_v1/loadtest/router.py` — **IMPLEMENTED**

Two synthetic endpoints that do nothing, so a load run can measure what the
*path* costs — gateway authentication and forwarding, this service's middleware
stack, the framework's request handling — without a database round trip, an
engine call, or a bot's state in the same number. Not a product surface: they
are the baseline every other endpoint's number is read against.

| Method | Path | Purpose | Success |
|---|---|---|---|
| GET | `/openapi/v1/bots/loadtest/hello` | Answer the constant `hello world` | `Envelope[HelloWorld]` (`data.message == "hello world"`) |
| WEBSOCKET | `/openapi/v1/bots/loadtest/ws/echo` | Send every received frame straight back | — (see below) |

**Authentication — the same as every other operation on this surface.** Both
declare `require_principal`, so a caller with no verified `X-Avernet-Principal`
is refused before the handler runs: `401` with the standard `ErrorEnvelope` on
the HTTP endpoint, and close code `1008` on the socket — the handshake is
refused before the socket is accepted, which a client sees as an HTTP `403`.
Through the gateway both inherit `user: required` from `/openapi/v1/bots/**`;
neither is exempted in `route_security`. A measurement taken without the auth
would describe a path no caller can take, which is why they are not exempt.

**Not user-scoped.** Neither takes `?user_id=` — they read and write nothing, so
there is no scope for it to name. See "Naming the end user" above; the HTTP one
is recorded in `test_explicit_user_id.py` alongside the four catalogue reads
that are exempt for the same reason, and it documents no `403`.

**The socket's contract**, written out here because a WebSocket has no OpenAPI
representation and therefore appears in **no** generated artifact — this table
is the whole of it, not a summary of something machine-readable:

- **Frames.** Text and binary are both accepted, and each is echoed back as the
  same type it arrived as. The payload is returned byte for byte: nothing is
  trimmed, re-encoded, parsed, or interpreted, so a driver picks its own payload
  shape.
- **Ordering.** One frame in, one frame out, in order. No batching, no coalescing,
  and no server-initiated frames — the socket says nothing the client did not
  say first.
- **Disconnect.** The client closing ends the connection; the server does not
  close first and sends no close frame of its own. A dropped transport is the
  same outcome. Neither is an error, and neither is logged as one.
- **Lifetime.** No idle timeout, no ping/pong, no message-count or size limit
  beyond whatever the ASGI server and any L7 hop impose.

**Routing.** The HTTP endpoint reaches the backend through the `bots` domain
with no gateway change. The socket needs its own domain — `bots` declares no
`protocols` and so serves HTTP only — which is `bots-loadtest-ws` in
`src/gateway/configs/application.yaml`: socket-only, forwarded verbatim to the
backend, no rewrite. The `ws` segment is what that claim is pinned to, following
`/openapi/v1/bots/messages/ws/**`, so an HTTP endpoint added under `loadtest`
later falls outside it by construction.

> **Known gap.** `AvernetTenantMiddleware` and the public access log both return
> early on a non-HTTP scope, so the socket runs under the **default** tenant and
> writes no access line. Harmless here — it reads and writes nothing — and the
> first thing to fix before any socket route on this surface touches data, since
> a tenant-scoped read under the default tenant is a data-isolation failure
> rather than a missing log.

### Track C — engine runtime (18 endpoints)
Not a Track B category — these wrap the **engine adapter** on the bot's device
rather than a backend service. The per-endpoint checklist, the engine route each
one maps to, and the ruling on the ~72 engine routes that are *not* wrapped live
in **[`engine-surface.md`](engine-surface.md)**. Summary:

| Group | Endpoints | Public paths |
|---|---|---|
| sessions | 7 | `/openapi/v1/bots/{bot_id}/sessions…` — owner/collaborator operators |
| engine | 4 | `/openapi/v1/bots/{bot_id}/engine/{status,capabilities,available,restart}` |
| models | 2 | `/openapi/v1/bots/{bot_id}/models`, `…/models/{model_id}` |
| nodes | 1 | `/openapi/v1/bots/{bot_id}/nodes` — read-only node inventory |
| approvals | 3 | `/openapi/v1/bots/{bot_id}/approvals/mode` (GET/PUT), `…/modes` |
| connection | 1 | `/openapi/v1/bots/{bot_id}/connection` — complete WS URL, replaces `get_device_connection` |

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
8. **Track C:** the six engine-runtime groups (18 endpoints) implemented,
   owner-scoped and capability-aware, and `…/connection` returning socket URLs
   so no external caller ever sees a proxypass target or a raw device token.
   — _✅ 6 of 6 (PR #630 baseline; later engine restart and nodes additions)._

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

- **2026-08-23** — **The seam gets its adopters.** 60 `ServiceChecked` rows and
  8 `INHERITED` twins become `Check`, so `ServiceChecked` falls from 92 to 25
  and `Check` rises from 0 to 82; `scaffolding_row_count()` goes 181 → 99.
  Migrated: render-screens (3), the skill-centre capability hook (19), the
  bot-skill `{skill_id}` assets (7), engine-runtime and diagnostics (16) with
  eight path-addressed twins, the service-publication facade (16), channels (6)
  and editors (5). Each group's service-local check was deleted only where the
  seam is its sole caller; where it also serves a retiring twin or a surface
  outside `/openapi/v1`, the check stays and the row records that the seam is
  the *declared* authority. Two services fall in that second class —
  `bot_skill_asset_service` and `skill_set_control_plane`, the latter only after
  a P1 review finding caught its check being deleted while `/api/skillsets` was
  still relying on it. Two caller-visible changes on the public surface, both
  intended. **A revoked Team-Space
  editor is now refused on 58 of the 82 rows**: the seam resolves through
  `resolve_operable_permission_level`, which checks live Space membership on top
  of the collaborator role, where skill-centre, bot-skill, channels and
  engine-runtime previously asked for the raw role alone. A convergence rather
  than a new policy — render-screens, editors and the publication facade were
  already strict, so there was no single prior behaviour to preserve. The 19
  skill-centre operations also move a denied collaborator from **403** to the
  seam's masked **404** (closing a probing oracle), and the 7 bot-skill operations
  change error code within 404. The burn-down assertion stops being an `xfail`
  and becomes a strict equality against `_DEFERRED_OPERATIONS`. See **Adopting
  it** above.

- **2026-08-22** — **`SELF_CHECKED` renamed to `INHERITED`.** Naming only; no
  row changed mode, no route changed behaviour, and the count in each mode is
  what it was. The old name asserted a property no `deprecated/` route has —
  none of them checks itself, because each is the replacement's own endpoint
  function re-registered at the path it used to have, so what governs it is the
  replacement's row. It also collided with `deprecated`'s `SELF_CHECKED_ROUTES`,
  an unrelated admission concept that *does* mean "this router checks the grant
  itself"; that one keeps its name. See **The five modes** above.

- **2026-08-21** — **One authorization seam, inert on arrival.** Every public
  operation now declares what governs it in `openapi_v1/authorization.py`, and
  `PublicAPIRoute` applies the row itself — a handler declares nothing, and an
  operation with no row raises while its module imports, so the application
  cannot start. 229 rows: 92 `ServiceChecked`, 56 `NoCheck`, 42 `SELF_CHECKED`,
  39 `OWNER_SCOPED`, and **no `Check`** — this change builds the mechanism and
  adopts it nowhere, so no answer changed for any caller (proved by asserting
  no live operation carries the gate, not by sampling statuses). The seam
  (`openapi_v1/bot_access.py`) carries permission level and audit, fails closed
  on every lookup failure, and deliberately carries **no edit lock**; the locks
  channels and service publications enforce are untouched. Reads are not
  audited, matching the internal surface as measured. Adoption is per-group
  follow-up work: a `ServiceChecked` row flips mechanically, an `OWNER_SCOPED`
  row is #906 / #907 and changes behaviour. See **Declaring authorization**
  above. The harness group's check-versus-act defect was found during this work
  and is filed for that group's owner, not fixed here.

- **2026-08-19** — **Bot ownership-Space reassignment added.** Added
  `PUT /openapi/v1/bots/{bot_id}/space` with explicit `user_id`, a numeric
  target `space_id`, owner and target-membership enforcement,
  `GRANT_CHECKED_OWN_BOT` application admission, desktop-to-TEAM refusal,
  idempotent `changed=false` no-op behavior, and tenant/owner-scoped
  persistence whose failures are not swallowed. Production Bot Inventory now
  consumes numeric PERSONAL/TEAM Spaces through the membership-gated Spaces
  Service API adapter. This remains a narrow assignment update; collaborator
  reconciliation and rollback belong to the pending B-line migration workflow.

- **2026-08-19** — **Bot Workshop Channels restored as a complete public API.**
  Added six Bot-first DingTalk draft-configuration operations under
  `/openapi/v1/bots/{bot_id}/channels`, with explicit `user_id`, optional
  shared-Bot `owner_id`, `GRANT_CHECKED_ADDRESSED_BOT` application admission,
  operable-collaborator reads, and ADMIN-gated writes that preserve the existing
  Bot edit-lock rule (423 when a collaborative Bot is not locked by the caller).
  Secrets are write-only; runtime synchronization failures normalize to 502.
  Track A now adds an
  `avernet_tenant` guard and tenant-leading Channel index, with four isolation
  tests. The Stage 3 DDL is deploy-before-code. The 2026-08-03 deletion entry is
  retained below because it records why the former published stubs were removed.

- **2026-08-18** — **Data-init trigger/status contract completed.** The public
  trigger now forwards the `IAM_TOKEN` cookie through the HTTP boundary into a
  typed `DataInitServiceProtocol`; the service persists it only when an
  initialization attempt will actually run. Added
  `GET /openapi/v1/bots/{bot_id}/data-init` for polling the bounded public state
  (`not_started`, `pending_init`, `in_progress`, `completed`, or `failed`)
  without exposing the Bot `ext` bag, credentials, or downstream sync details.
  Regenerated the Gateway `bots.openapi.json`; the independent OCB/Sofapy copy
  and real IAM/Engine/downstream E2E remain deployment verification items.

- **2026-08-17** — **TC bot workshop and local workflows.** Added the
  aggregated `/bots/all` inventory, personal-local device/create/read/restart/
  delete/open-folder workflows, dormant activation, cold-start data
  initialization, and engine-process restart. Local and aggregate inventory
  remain human-only at both Gateway and backend admission; the remaining bot
  surface continues to admit an application only within its live grants. The
  engine restart has only its bot-first address because no earlier public route
  existed to retire. Regenerated Gateway `bots.openapi.json` is the release
  artifact for this surface and must be copied unchanged to the OCB Gateway.

- **2026-08-18** — Migrated the legacy `/api/v1/token/iam` IAM-token return and
  optional Caller preparation as one Bot-scoped operation:
  `POST /openapi/v1/bots/{bot_id}/iam-token`. The client supplies only Bot and
  runtime context; Backend decides whether Caller exchange is required. The
  operation requires a Gateway user principal, re-checks `user_id`, refuses
  app-only callers, uses the standard Envelope, and disables response caching
  through shared middleware. The OCB Gateway SOFA config still needs the
  corresponding deployment-side update.

- **2026-08-15** — **Bot-first addressing.** Every bot-scoped operation moved
  to `/openapi/v1/bots/{bot_id}/<component>/…`, reversing the component-first
  rule of 2026-08-03. Nine operations that took `bot_id` in the query string
  and one that took it in a request body now take it on the path, where the
  address already had somewhere to put it — the duplicate field the client was
  asked to fill twice is gone. Two contract fixes ride along: `skills` spells
  its owner locator `owner_id` like the rest of the surface, and
  `PUT …/approvals/mode` no longer takes the `session_key` it never read.
  `TODO(#960)` is narrowed from seven operations to four, not closed: with the
  bot on the path, `require_granted_bot` checks the grant for every operation
  except the four `{skill_id}` skills ones, whose bot owner is only known after
  the skill is read and which therefore still check in their handlers
  (`admission.SKILL_SCOPED_OPERATIONS`). Bot-scoped
  component names left the `{bot_id}` segment, so the reserved-name list falls
  from fifteen to six once the old addresses go. **Nothing was removed** — all
  forty-one prior addresses still answer, at the same shape, marked
  `deprecated` with `Deprecation`/`Sunset` headers; the window runs to
  **2027-08-15** and removal is driven by traffic. See **Retiring addresses**
  above and `specs/2026-08-15-openapi-v1-bot-first-addressing`.

- **2026-08-09** — **The engine-runtime groups serve shared bots and published
  stages.** The operator rule replaces the shared-bot refusal: a bot's owner
  and its member-level collaborators operate it — public personal bots,
  collaborated bots, and a service bot's verify/online runtimes included —
  via two optional query parameters (`owner_id`, defaulting to the caller;
  `stage`, defaulting to `draft`) whose defaults keep every prior request
  byte-for-byte identical. A non-operator is answered identically to an
  absent bot; a dead stage is `409` `"No live runtime at the requested
  stage"`; the 501 now means only "bot type not served". `draft_device`
  became `stage` in the relay (required, no default), the stage→binding rule
  lives once in `core/engine_runtime/stage.py` (shared with the connection
  service, aligned with cron's retained-verify rule), and `sharing.py` /
  `BotFacts.is_shared` retired. See **Operating shared bots and published
  stages** above and `specs/2026-08-09-openapi-v1-access-expansion/`.
  Board moved: sessions row note. Follow-ups filed as #906–#911.

- **2026-08-09 (retroactive, recording PR #880, merged 2026-08-07)** — **Draft
  service bots were served across the public surface, draft device always.**
  Landed without a spec directory or a changelog entry; recorded here so the
  doc's history is whole. It widened the Track C gate from "private personal
  bots only" to "unshared personal bots + a service bot's unshared
  pre-publication draft", with every gated forward pinned `draft_device=True`
  — the state the 2026-08-09 access expansion above then replaced. Its
  docstrings' `publish_bot_id + "pub" + version` naming-scheme claim was
  factually wrong (the code writes `publish_bot_id = bot_id`; the real
  draft/published separation is binding storage — `ac_bots.binding_id` vs
  `ac_bot_publish.ext.binding.{verify,online}`) and was corrected by the
  access expansion.

- **2026-08-09** — **A `loadtest` component was added, and it is the surface's
  first WebSocket.** Two synthetic endpoints — `GET …/loadtest/hello` answering a
  constant, and `WEBSOCKET …/loadtest/ws/echo` echoing its input — so a load run
  can measure the shared path without a service call in the number. Both require
  `require_principal` like everything else here; neither is user-scoped, so the
  HTTP one joins the four operations with no user dimension. To make one
  dependency serve both planes, `require_principal` and `resolve_caller` now take
  an `HTTPConnection` rather than a `Request`, and a handshake with no verified
  caller is refused with close code `1008` instead of a `401` envelope it cannot
  carry — **HTTP behaviour is unchanged**. Because a WebSocket appears in no
  generated artifact, the socket's full contract is written out under
  **Endpoints per component** above rather than published; the gateway serves it
  through a socket-only `bots-loadtest-ws` domain, and the tenant/access-log gap
  on the socket plane is recorded there too.

- **2026-08-09** — **The public surface now names its end user explicitly.** 56
  of the 65 operations take a required `user_id` query parameter instead of
  deriving the owner from the verified principal; naming another user is a
  `403`. Four operations with no user dimension (`check-name`, the three MCP
  catalogue reads) take none, and Bot Logs is untouched — its own `user_id`
  means the opposite thing. `bot_id` moved nowhere. Nothing about who may call
  what changed: this readies the contract for App-on-behalf-of, it does not
  admit it. See **Naming the end user** above and
  `specs/2026-08-08-openapi-v1-explicit-user-id/`.

- **2026-08-04** — **Skills Track B integration/release gate implementation and
  CI are complete, but Track B is not release-complete.** The served OpenAPI is
  now locked to exactly six Bot-owned Local Skill operations; obsolete
  `{bot_id}/skills` install/uninstall stubs were removed. Assembled real-guard
  tests prove another tenant cannot list, read, upload/replace, activate,
  deactivate, or delete the target tenant's Local Skill. The owner plus
  authorized collaborator pre-production lifecycle remains **PRE-PROD
  PENDING**. The executable acceptance, verification, and rollback checklist is
  `skills-track-b-preprod-acceptance.md`.

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
- **2026-08-19** — **Read-only Node OpenAPI added.** The frontend and Engine
  currently support only `GET /api/nodes`, so the public API adds exactly
  `GET /openapi/v1/bots/{bot_id}/nodes`, with the same owner/editor, grant and
  stage gates as the other runtime groups. It forwards `status`, `platform`,
  `limit` and `offset`, publishes only stable node fields, and does not invent
  register/unregister/status-write operations.
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
- **2026-08-10** — **Identity admission widened: an application may act for a
  user who authorized it** (#950). A registered application calling with its own
  credential and **no human on the wire** is now admitted on the operations
  placed in an admission group, and reaches exactly what the delegating user has
  authorized it for. Four things worth knowing:
  1. **The bound is live, not a snapshot.** Nothing about the delegator's
     authority is stored in the grant, so every request is re-adjudicated by the
     same gates that person faces. Removing them from a bot ends the
     application's access on its next request, with the grant row untouched and
     nothing to clean up. That property is what makes the widening safe, and it
     has a test of its own.
  2. **The end-user guard moved, again, and for the mirror-image reason.** It
     could not stay in `verify_principal_token`: that component is
     transport-agnostic and runs before routing, and admission is now a
     *per-operation* question. It sits in `require_principal`, which every public
     route already declares, and consults `admission.py`. The property that
     mattered survives — a route inherits the refusal by saying nothing — and
     gets stronger: an operation absent from the table is refused, so a new route
     is refused by *omission* rather than by remembering not to opt in.
  3. **`user_id` stopped being compared and started being authorized.** For a
     person it still must name themselves (403 otherwise). For an application
     there is no second id to compare with, so it is checked against the grant —
     which is the branch `require_user_id`'s docstring predicted when the
     parameter was introduced. No handler, schema or path changed.
  4. **Refusals stay indistinguishable.** An application reaching an operation it
     may not gets the same `401` an unauthenticated caller gets; one naming a bot
     it holds no grant for gets the same `404` a nonexistent bot gets, byte for
     byte. Anything finer is an enumeration oracle.
  `bot` / `access_key` callers remain refused outright — neither can be
  authorized against a delegation. SDD:
  `specs/2026-08-10-openapi-v1-app-only-caller/`.
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
  `/openapi/v1/bots/{bot_id}/skills/{…}`. `channels` was deleted rather than parked.
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
- **2026-08-05** — **`tenant` removed from the `user` principal; a user-only
  caller is an internal caller.** The public surface was answering `401` on every
  request: the gateway declared `UserPrincipal.tenant` as `str | None` and filled
  it from `authn.google.default_tenant`, a config key that appears nowhere in
  `configs/application.yaml`, so it signed `tenant: null` — and this side
  declared the field required, so the payload never parsed. The fix removes the
  field on both halves rather than making it required, because the field was
  asserting something no user credential proves.
  1. **A tenant is a property of the calling program, not of a person.** `app`,
     `bot` and `access_key` are each *registered* to a tenant, and their
     principals assert that registration. A Google token proves a `sub` and an
     email; the tenant that used to ride alongside it was a deployment default
     dressed up as an authenticated fact.
  2. **A set that asserts no tenant resolves to `DEFAULT_AVERNET_TENANT`.**
     `VerifiedCaller.tenant` reads only the machine principals, so a user-only
     caller — a first-party human on our own frontend — scopes to `teamclaw`,
     the same tenant every non-public path in this component already resolves
     to. `_reject_contradictory_tenant` likewise vets only what was claimed.
  3. **The internal-tenant guard got sharper, not weaker.** The fallback is
     decided *here*, from the absence of a claim; it is never a value the token
     supplied. A token that names `teamclaw` is still refused, and a `tenant`
     smuggled onto a `user` entry is dropped by the DTO rather than honoured —
     both pinned by tests. Nobody can talk their way into the internal tenant.

     _Superseded 2026-08-05:_ the first of those two — refusing a token that
     names `teamclaw` — was removed once a `teamclaw` tenant was registered on
     the gateway as a first-party path onto this surface. See
     `core/gateway_principal/README.md` § Change impact for what still holds the
     boundary. The second half stands: a `tenant` on a `user` entry is still
     dropped, so a caller cannot assert a scope the gateway did not sign.
  4. ⚠️ **What this leaves open.** `route_security` declares `user: required`
     and nothing else for the whole public surface, so *every* public request is
     now a user-only set and scopes to `teamclaw`. Nothing gates which Google
     account that is — `AuthPlugin.is_allowed` exists in the gateway SPI and no
     authn strategy calls it. Before this surface serves real data, either wire
     that whitelist up or have a route require an identity carrying a registered
     tenant. Tests that meant to exercise external-tenant isolation now mint
     `user` + `app` so they keep testing isolation instead of the internal
     default.

  Backend suite 10535 passed / 3 skipped; gateway suite green except the
  pre-existing markdown-formatting and live-server failures. Gateway half:
  `spi/authn/_models.py`, the `google` strategy (its `default_tenant` argument
  and DI wiring are gone), and a dated amendment in
  `src/gateway/docs/2026-07-21-auth-design.md` §4.6, whose original text made
  `tenant` mandatory on every principal.
- **2026-08-05** — **Every error this surface converts now leaves a log record.**
  `@envelope_errors` mapped a domain error to its status and fixed message and
  logged *nothing*, so a caller's report of a 404 or a 409 could not be traced to
  a raise site: the fixed-message rule keeps the diagnosis out of the response,
  and there was nowhere else it went. The decorator now emits one line per
  failure — exception type and its internal message, method, concrete path, route
  template, and the handler's own arguments — with the traceback attached.
  4xx logs at warning, 5xx at error; both carry the trace, because these errors
  are raised *inside* a handler, so the trace is a short chain of our own frames,
  not an ASGI stack. Routine unauthenticated traffic is unaffected: the auth
  seam raises in a dependency, which `app.py` still answers and logs without one.
  New `adapters/http/error_logging.py` owns the capture — lazy (a successful
  request pays nothing), bounded (strings, collections and nesting all capped;
  bytes reduced to a size), and redacting by *name* at any depth, with `Request`
  and `Headers` opaque by type since `Request` is a `Mapping` over its ASGI scope
  and walking it would log the raw `Authorization` header. Unmapped errors are
  still re-raised, with the arguments stashed on the request scope so `app.py`
  logs the same detail from further out; the `DomainError` handler additionally
  logs 4xx (one compact line, no traceback) where it previously logged nothing,
  and the public 422 handler now records which field failed validation — `loc` /
  `type` / `msg` only, never the caller's `input` value. **If your category adds
  a body field holding a credential, add its name substring to
  `_SENSITIVE_NAME_PARTS`.** No status code, response body, or envelope shape
  changed.

## Changelog

- **2026-08-20** — Added authenticated Bot catalog reads at
  `GET /openapi/v1/bots/catalog/search` and `/discover`. User and App
  principals see the same allowlisted public projection.
  `/search` maps its current request page to BCS `/v2/bots/search` with
  `tc_bot=true`, parses each `bot_uuid` as the tenant-scoped `(bot_id, entity_id)`
  join key, and exposes only the exact public Backend matches. Its `total` is
  the current page's joined count; BCS failures or invalid response shapes return
  `502000 / Catalog service unavailable`, with no Backend-only fallback.

- **2026-08-19 — Bot Editors CRUD.** The public surface now exposes
  `GET/POST /openapi/v1/bots/{bot_id}/editors`,
  `PATCH/DELETE /openapi/v1/bots/{bot_id}/editors/{editor_id}`, and
  `DELETE /openapi/v1/bots/{bot_id}/editors/me`. All five operations admit an
  application with a live grant on the addressed Bot; the App acts with the
  delegating user's current permission, re-adjudicated on every request. The
  acting user remains the required `user_id` query parameter, while a create body names its target as
  `editor_user_id`. Roles are the closed `admin | member` enum. Reads require
  Member access; mutations require Owner/Admin, and a non-owner Admin leaves
  through `/me` rather than deleting their own record through the admin route.
  Every `{editor_id}` mutation rebinds the record to the addressed Bot primary
  key, Bot id, Owner and environment before writing; mismatches are the same
  fixed 404 as absence. When a Bot carries a Team Space reference, adding or
  promoting an editor requires that user to remain a live member of the Space;
  unknown Space references fail closed. The Owner remains outside the editor
  table, so this contract intentionally permits zero or multiple collaborator
  Admins and does not couple membership changes to the draft edit lock.

- **2026-08-19 — Bot Workshop adopts the canonical Spaces contract.** Bot
  creation, local-workflow operations and `GET /openapi/v1/bots/all` now use a
  numeric Space primary key for `space_id` / `X-Space-Id`; `space_code` remains
  a separate stable external code. Inventory cards expose the same
  `space_id`, `space_code`, `space_name`, `space_type` shape as the Spaces API,
  replacing the provisional string `space_id`, `name`, `kind` shape. This is a
  coordinated breaking schema correction and the Gateway artifact is published
  through the compatibility gate with `--allow-breaking`. A Team Space view
  includes every supported cloud Bot assigned to that Space across Bot Owners;
  Space membership grants visibility only. Bot Owner/Editor relations still
  decide actions, Team Editors must remain live Space members, Personal Spaces
  allow Editors, and Bot Owners retain their Bot permissions after leaving a
  Team Space.

- **2026-08-19 — Render-screen configuration CRUD.** Added
  `GET/POST /openapi/v1/bots/{bot_id}/render-screens` and
  `PATCH/DELETE /openapi/v1/bots/{bot_id}/render-screens/{render_screen_id}`.
  The collection read returns the non-sensitive component-library name → UMD
  CDN URL mappings needed to render a Bot's side panels. An authenticated human
  may read an explicitly addressed Bot without being its Editor; an application
  may call every operation only while a live grant covers that Bot. Mutations
  require the delegating user's live effective Editor permission at `MEMBER` or
  above, including the Team Space membership recheck. Every public record id is bound
  back to the addressed Bot id, Owner and environment before update or delete;
  mismatches use the same fixed 404 as absence. Request bodies are strict and
  accept only HTTP(S) CDN URLs.
