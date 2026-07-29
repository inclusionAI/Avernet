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

---

## The big picture (read this first)

**Goal:** implement the public `/openapi/v1` API, whose callers are **external
registered tenants**. It lives under
`src/backend/src/agentclaw/community/adapters/http/openapi_v1/*`. The **bots**
category is implemented (PR #494); the other six are still **route definitions
with stub handlers**.

> 🔒 **The surface is not callable yet, by design.** `require_principal` is
> still a stub returning `None`, so every real request to `/openapi/v1/...`
> answers `401` — including the implemented bots endpoints. The real caller
> authenticator is a separate workstream, and the DoD gates the public surface
> on it. "bots is done" means the handlers, contracts and tests are done, not
> that an external tenant can call them.

The catch: the internal `/api/...` surface and the public `/openapi/v1` surface
share the **same tables, repositories, and services**. So a public endpoint
that returned real data would — without isolation — read the *internal*
tenant's data. That's the problem this effort exists to prevent.

The work therefore splits into **two tracks**:

- **Track A — Tenant-isolation foundation.** Make every data category
  tenant-scoped *underneath both API surfaces*, before any public endpoint is
  wired. **Track A implements NO endpoint by design** — it's plumbing.
- **Track B — Public API implementation.** Wire the seven `/openapi/v1`
  category handlers to the existing services. **This is where the endpoint/API
  code actually lands.** Each category depends on its data being isolated
  (Track A) first. **1 of 7 done: bots (PR #494).**

> ⚠️ **The one confusion to avoid:** "isolation Stage N is done" does **not**
> mean any API endpoint was implemented. A Track A stage is plumbing only (the
> reusable mechanism + that category's records). The API endpoints land in
> Track B — done for bots, still stubs for the other six.

---

## Who's working on what

We split by **vertical slice**: each person owns a data category end-to-end —
its **Track A** isolation stage *and* its **Track B** endpoints. That way a
Track B category is never blocked on a Track A stage the *other* person owns.
(Your `mcp` example: `mcp` Track B depends on `mcp` Track A, so both go to one
owner.)

| Person | Owns (vertical slices) | Track A stages | Track B endpoint groups |
|---|---|---|---|
| **totalfrank** | bots, mcp, channels, **skills** (shared) | 1 (bots ✅), 5 (mcp), 3 (channels), 4 (skills, shared) | bots, mcp, channels, skills (shared) |
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
| **P2 — second** | channels, identity | channels → totalfrank; identity → lucas-xzp |
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
| 2 | Resources (`ac_resource`) | lucas-xzp | P1 | ⬜ TODO | column + guards + tests green; internal API unchanged |
| 3 | Channels (`ac_channel_config`) | totalfrank | 🅳 **DEPRIORITIZED** | ⏸️ PARKED — scope intact, not cancelled | same, if picked back up |
| 4 | Skills (skill tables) | totalfrank + lucas-xzp | P3 | ⬜ TODO | same |
| 5 | MCP configuration (`ac_user_mcp_config` + `ac_bot_mcp_call_config`) | totalfrank | P1 | ✅ DONE — **PR #564** | PR #564 merges |
| 6 | Routines | lucas-xzp | P1 | ⬜ TODO | same |

> Stage 1 also builds the **reusable mechanism** (see below) that every later
> stage copies. It's the foundation, not just "bots."

### Track B — Public API implementation (where the endpoints land — 1 of 7 done)
_Ordered by priority tier._
| Category | Owner | Pri | Router | State | Depends on |
|---|---|---|---|---|---|
| bots | totalfrank | P1 | `openapi_v1/bots/router.py` | ✅ **DONE — PR #494 merged 2026-07-29** (13/13 endpoints) | ~~Track A stage 1~~ ✅ |
| mcp | totalfrank | P1 | `openapi_v1/mcp/router.py` *(stub)* | ⬜ TODO — **unblocked** | ~~Track A stage 5~~ ✅ (PR #564) |
| resources | lucas-xzp | P1 | `openapi_v1/resources/router.py` *(stub)* | ⬜ TODO | Track A resources (lucas-xzp) |
| routines | lucas-xzp | P1 | `openapi_v1/routines/router.py` *(stub)* | ⬜ TODO | Track A routines (lucas-xzp) |
| channels | totalfrank | 🅳 **DEPRIORITIZED** | `openapi_v1/channels/router.py` *(stub)* | ⏸️ PARKED — scope intact, not cancelled | Track A stage 3 (also parked) |
| identity | lucas-xzp | P2 | `openapi_v1/identity/router.py` *(stub)* | ⬜ TODO | bots isolation (Stage 1 ✅) |
| skills | totalfrank + lucas-xzp | P3 | `openapi_v1/skills/router.py` *(stub)* | ⬜ TODO | Track A skills (shared) |

### Cross-cutting (not per-stage)
| Item | State | Note |
|---|---|---|
| Real caller-identity verifier (auth workstream) | ⬜ TODO (other team) | swap `require_principal` + `resolve_avernet_tenant` bodies to read the gateway principal; **the whole public surface answers 401 until this lands** |
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

> **⏸️ Why channels are parked (2026-07-29).** The product does not need
> channels at this point, so they must stop presenting as the next thing to
> pick up. This is a **deprioritization, not a cancellation** — both rows keep
> their full scope and can be picked back up unchanged. If channels are ever
> actually cancelled, delete the rows rather than leaving them parked.

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
  the single seam. Returns the default tenant today; the auth workstream swaps
  the body in place. Category-agnostic. _(This file holds both stubs today —
  `require_principal` and `resolve_avernet_tenant`; the owner-side seam on top
  of the former is `openapi_v1/principal.py::caller_owner_id`.)_

All paths are under
`src/backend/src/agentclaw/community/`.

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
   `src/backend/specs/2026-07-27-openapi-v1-bots-track-b/` and
   `openapi_v1/bots/router.py` as the worked reference.

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

> ⚠️ **Path divergence — still open for the six stub groups.** The routers nest
> every non-`bots` group under `/openapi/v1/bots/...` (e.g.
> `/openapi/v1/bots/resources`, `/openapi/v1/bots/mcp`). PR #363's overview used
> **top-level** paths (`/openapi/v1/resources`, `/openapi/v1/mcp`, …). The
> **router is authoritative** for implementation — the paths below match it.
> Owners: if the top-level shape is the intended public surface, change the
> router `prefix` and update this section in the same PR. _(bots is unaffected:
> it is `/openapi/v1/bots` under either reading, and shipped that way in #494.)_
>
> **Mount order is load-bearing.** `build_public_router()` includes the six
> literal sub-groups **before** the bots group, so `/openapi/v1/bots/channels`
> resolves ahead of the `/openapi/v1/bots/{bot_id}` wildcard. Keep any new group
> in the `_SUBGROUPS` list, above the bots router.

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

### 🟦 totalfrank · P2 — channels (6 endpoints) · `openapi_v1/channels/router.py`
DingTalk (`dingding`) config CRUD + status toggle.
| Method | Path | Purpose | Success |
|---|---|---|---|
| GET | `/openapi/v1/bots/channels` | List channels (optional `bot_id`) | `Envelope[list[Channel]]` |
| POST | `/openapi/v1/bots/channels` | Create channel (starts inactive) | `201 Envelope[Channel]` |
| GET | `/openapi/v1/bots/channels/{channel_id}` | Get channel | `Envelope[Channel]` |
| PUT | `/openapi/v1/bots/channels/{channel_id}` | Full update | `Envelope[Channel]` |
| PATCH | `/openapi/v1/bots/channels/{channel_id}` | Toggle active/inactive | `Envelope[Channel]` |
| DELETE | `/openapi/v1/bots/channels/{channel_id}` | Delete | `Envelope[Deleted]` |

_Note: the stub returns `Envelope[list[Channel]]` for list (not `Page`); PR #363
showed `Page[Channel]`. Confirm which you want when you wire it._

### 🟦 totalfrank · P1 — mcp (6 endpoints) · `openapi_v1/mcp/router.py`
Marketplace + tenants + the caller's unified per-server config.
| Method | Path | Purpose | Success |
|---|---|---|---|
| GET | `/openapi/v1/bots/mcp/servers` | List marketplace servers (`keyword`, paged) | `Envelope[Page[McpServer]]` |
| GET | `/openapi/v1/bots/mcp/tenants` | List MCP tenants | `Envelope[list[McpTenant]]` |
| GET | `/openapi/v1/bots/mcp/servers/{server_code}` | Server detail | `Envelope[McpServerDetail]` |
| GET | `/openapi/v1/bots/mcp/servers/{server_code}/permissions` | Caller's permission for a server | `Envelope[McpPermission]` |
| GET | `/openapi/v1/bots/mcp/servers/{server_code}/config` | Read caller's unified server config | `Envelope[McpConfig]` |
| PUT | `/openapi/v1/bots/mcp/servers/{server_code}/config` | Write config (pushed to devices) | `Envelope[McpConfig]` |

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

_Note: the stub's upload is raw `octet-stream`; PR #363 described `multipart`.
Pick one when wiring._

### 🟪 totalfrank + lucas-xzp · P3 — skills, co-owned (7 endpoints: 5 in stub + 2 proposed ★) · `openapi_v1/skills/router.py`
Catalog at `/openapi/v1/bots/skills`; a bot's installed skills are a bot
sub-resource.

> **Co-owned — the trickiest category.** Skills has a three-layer lifecycle
> (global **upload** → per-bot **install** → per-bot **enable/disable**), two ★
> endpoints not yet ratified into the stubs, and an open question on whether the
> richer backend skill-set model gets promoted to a first-class concept. Because
> of that, **both** own it. Agree a shared sub-plan first — e.g. split
> catalog/upload vs. per-bot install/lifecycle — and give it its own SDD before
> writing code. Do it after your P1/P2 slices.

The **Status** column marks whether each endpoint is already in the router stub
(`in stub`) or a proposed addition from PR #363 (`★ proposed` — not in the stubs
yet; ratify with totalfrank before implementing).

| Method | Path | Purpose | Success | Status |
|---|---|---|---|---|
| GET | `/openapi/v1/bots/skills` | Skill catalog (`keyword`, paged) | `Envelope[Page[Skill]]` | in stub |
| GET | `/openapi/v1/bots/skills/{skill_id}` | Skill detail | `Envelope[SkillDetail]` | in stub |
| POST ★ | `/openapi/v1/skills/upload` | Upload a custom skill (global, owned by caller) | `Envelope[Skill]` | ★ proposed |
| GET | `/openapi/v1/bots/{bot_id}/skills` | List a bot's installed skills | `Envelope[list[BotSkill]]` | in stub |
| POST | `/openapi/v1/bots/{bot_id}/skills` | Install a skill on a bot (default enabled) | `201 Envelope[BotSkill]` | in stub |
| PATCH ★ | `/openapi/v1/bots/{bot_id}/skills/{skill_id}` | Enable/disable an installed skill (`status`) | `Envelope[BotSkill]` | ★ proposed |
| DELETE | `/openapi/v1/bots/{bot_id}/skills/{skill_id}` | Remove (unbind) a skill from a bot | `Envelope[Deleted]` | in stub |

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
| GET | `/openapi/v1/bots/identity/bot/{bot_id}` | List identity files + whether each exists | `Envelope[IdentityFileList]` |
| GET | `/openapi/v1/bots/identity/bot/{bot_id}/{file_type}` | Read one identity file | `Envelope[IdentityFile]` |
| PUT | `/openapi/v1/bots/identity/bot/{bot_id}/{file_type}` | Overwrite one identity file (`content`) | `Envelope[IdentityFileRef]` |

---

## Definition of done (whole `/openapi/v1` effort)

1. **Track A:** every data category (bots, resources, channels, skills, mcp,
   routines) carries `avernet_tenant` and is guarded, Stage-1 test shape green.
   — _1 of 6 (bots ✅)._
2. Internal API unchanged throughout (no `to_dict()` leaks; internal suites
   unmodified). — _holding: full `tests/community` green at #494 (9171 passed,
   3 skipped)._
3. **Track B:** the seven `/openapi/v1` categories' handlers implemented and
   tenant-safe, each with its own tests + PR. — _1 of 7 (bots ✅)._
4. F2 tenant-leading indexes in place (mandatory policy). — _⬜_
5. Background/scheduled work revisited for per-tenant correctness. — _⬜_
6. `require_principal` / `resolve_avernet_tenant` wired to the real verifier
   (auth workstream) — the point at which a second tenant can safely hold real
   data, and the point at which the public surface stops answering 401. — _⬜_
7. **Cross-tenant external identity settled ([#556](https://github.com/inclusionAI/Avernet/issues/556))** — Passport, auth
   relationships and BCN carry a tenant axis, so the BCN sync can be re-enabled
   on the public path. — _⬜ (added 2026-07-29; gates enabling multi-tenancy)._

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
- **Real caller-identity verifier.** Swap `require_principal`'s body to return
  the gateway-forwarded principal, and `resolve_avernet_tenant`'s to return its
  tenant. Both seams are ready, and `caller_owner_id` already accepts either a
  bare id string or an object/dict with `user_id`, so handlers don't change.
  **Until this lands the public surface answers 401 to everything.**
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
  blocking): **totalfrank** = bots, channels, mcp; **lucas-xzp** = resources,
  skills, routines, identity. Sequencing decision resolved → per-category
  vertical slices. Added **Endpoints per component** checklists (from the stub
  routers + PR #363), flagged the `/openapi/v1/bots/...` vs top-level path
  divergence and the two proposed ★ skills endpoints.
- **2026-07-27** — Added **priority tiers**: P1 = bots, mcp, resources, routines;
  P2 = channels, identity; P3 = skills. **Skills is now co-owned** by totalfrank
  + lucas-xzp (its Track A stage and its endpoints), being the most involved
  category. Priority columns added to both status boards; per-component headers
  tagged with tier.
- **2026-07-27** — Skills endpoints merged from two tables (5 in-stub + 2
  proposed) into a **single 7-row table with a Status column**, so the full
  surface reads as 7 at a glance instead of looking like 2.
- **2026-07-27** — **Track A Stage 1 merged (PR #456).** Bots carry
  `avernet_tenant`; the reusable mechanism (carrier, guards, middleware,
  `resolve_avernet_tenant` seam) is on `dev`. The shared bots gate is lifted.
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
