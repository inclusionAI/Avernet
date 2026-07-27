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
`src/backend/specs/2026-07-26-tenant-isolation-foundation/`
(`spec.md`, `plan.md`, `tasks.md` — these arrive with PR #456).

---

## The big picture (read this first)

**Goal:** implement the public `/openapi/v1` API, whose callers are **external
registered tenants**. Today it exists only as **route definitions with stub
handlers** under
`src/backend/src/agentclaw/community/adapters/http/openapi_v1/*`.

The catch: the internal `/api/...` surface and the public `/openapi/v1` surface
share the **same tables, repositories, and services**. So a public endpoint
that returned real data would — without isolation — read the *internal*
tenant's data. That's the problem this effort exists to prevent.

The work therefore splits into **two tracks**:

- **Track A — Tenant-isolation foundation.** Make every data category
  tenant-scoped *underneath both API surfaces*, before any public endpoint is
  wired. **Track A implements NO endpoint by design** — it's plumbing.
- **Track B — Public API implementation.** Wire the seven `/openapi/v1`
  category handlers (currently stubs) to the existing services. **This is where
  the endpoint/API code actually lands.** Each category depends on its data
  being isolated (Track A) first.

> ⚠️ **The one confusion to avoid:** "isolation Stage 1 is done" does **not**
> mean any API endpoint was implemented. Stage 1 is Track A only (the reusable
> mechanism + bot records). The API endpoints land in Track B, which has not
> started.

---

## Who's working on what

We split by **vertical slice**: each person owns a data category end-to-end —
its **Track A** isolation stage *and* its **Track B** endpoints. That way a
Track B category is never blocked on a Track A stage the *other* person owns.
(Your `mcp` example: `mcp` Track B depends on `mcp` Track A, so both go to one
owner.)

| Person | Owns (vertical slices) | Track A stages | Track B endpoint groups |
|---|---|---|---|
| **totalfrank** | bots, channels, mcp | 1 (bots ✅), 3 (channels), 5 (mcp) | bots, channels, mcp |
| **lucas-xzp** | resources, skills, routines, identity | 2 (resources), 4 (skills), 6 (routines) | resources, skills, routines, identity |

- **totalfrank** also owns the **reusable Track A mechanism** (built in Stage 1 /
  PR #456) — the pattern every other stage copies.
- **identity** (Track B only) has no Track A stage of its own: its data is a bot
  sub-resource, so it's already scoped by **bots isolation (Stage 1 ✅)**. It's
  assigned to **lucas-xzp** for balance; its one dependency is already satisfied,
  so this creates no cross-person block.
- Rough balance: totalfrank ≈ 2 remaining A stages + ~25 B endpoints; lucas-xzp ≈
  3 remaining A stages + ~24 B endpoints.

> **Shared gate:** anything touching bots (both people) waits on **PR #456**
> merging — a one-time gate, not an ongoing cross-person dependency. Once it
> merges, both owners are free to run their slices in parallel.

_See **Endpoints per component** below for exactly which endpoints each slice
must implement._

---

## Status board (update as work lands)

### Track A — Tenant-isolation foundation
| Stage | Scope (data) | Owner | State | Done-when |
|---|---|---|---|---|
| 1 | Bot records (`ac_bots` / `BotModel`) | totalfrank | ✅ DONE — **PR #456 (awaiting approval, not yet merged)** | PR #456 merges |
| 2 | Resources (`ac_resource`) | lucas-xzp | ⬜ TODO | column + guards + tests green; internal API unchanged |
| 3 | Channels (`ac_channel_config`) | totalfrank | ⬜ TODO | same |
| 4 | Skills (skill tables) | lucas-xzp | ⬜ TODO | same |
| 5 | MCP configuration | totalfrank | ⬜ TODO | same |
| 6 | Routines | lucas-xzp | ⬜ TODO | same |

> Stage 1 also builds the **reusable mechanism** (see below) that every later
> stage copies. It's the foundation, not just "bots."

### Track B — Public API implementation (where the endpoints land — NOT STARTED)
| Category | Owner | Router (stubs today) | State | Depends on |
|---|---|---|---|---|
| bots | totalfrank | `openapi_v1/bots/router.py` | ⬜ TODO | Track A stage 1 (PR #456) |
| channels | totalfrank | `openapi_v1/channels/router.py` | ⬜ TODO | Track A channels (totalfrank) |
| mcp | totalfrank | `openapi_v1/mcp/router.py` | ⬜ TODO | Track A mcp (totalfrank) |
| resources | lucas-xzp | `openapi_v1/resources/router.py` | ⬜ TODO | Track A resources (lucas-xzp) |
| skills | lucas-xzp | `openapi_v1/skills/router.py` | ⬜ TODO | Track A skills (lucas-xzp) |
| routines | lucas-xzp | `openapi_v1/routines/router.py` | ⬜ TODO | Track A routines (lucas-xzp) |
| identity | lucas-xzp | `openapi_v1/identity/router.py` | ⬜ TODO | bots isolation (Stage 1 ✅) |

### Cross-cutting (not per-stage)
| Item | State | Note |
|---|---|---|
| Real caller-identity verifier (auth workstream) | ⬜ TODO (other team) | swap `resolve_avernet_tenant` body to read the gateway principal's tenant; unblocks a real 2nd tenant |
| Tenant-leading indexes (F2, **MANDATORY** policy) | ⬜ TODO | before multi-tenant go-live |
| Background/scheduled work revisit | ⬜ TODO | before a 2nd tenant holds real data |

> **Sequencing decision — DECIDED 2026-07-27:** per-category **vertical slices**.
> Each owner isolates a category (Track A) then implements its endpoints
> (Track B) back-to-back, rather than finishing all of Track A before any
> Track B. This is precisely what keeps the two of us from blocking each other.

---

## Track A — the reusable mechanism (built in Stage 1, PR #456)

Category-agnostic; reuse as-is. These files arrive with PR #456:

- `utils/avernet_tenant.py` — per-request tenant carrier.
  `DEFAULT_AVERNET_TENANT = "teamclaw"` (internal tenant; owns all current
  data; **never hand it to an external tenant**). `get_current_avernet_tenant()`
  (total), `avernet_tenant_scope()` (set + guaranteed reset),
  `bind_current_avernet_tenant(fn)` (carry tenant into a raw
  `threading.Thread`/`ThreadPoolExecutor` target — `asyncio.to_thread`/
  `create_task` already copy context, so they need nothing).
- `plugin_api/models.py` — the **guard pattern** on `BotModel`:
  - `do_orm_execute` **read guard** on the `Session` class →
    `with_loader_criteria(Model, avernet_tenant == get_current_avernet_tenant(),
    include_aliases=True)`; skips column/relationship loads + a
    `skip_avernet_tenant_guard` option. Also constrains
    `Query.update()`/`Query.delete()`, so writes need no filter.
  - `before_insert` **insert guard** → stamp when unset, raise
    `CrossTenantInsertError` on an explicit conflicting tenant.
  - registered once, idempotent on `_AVERNET_TENANT_GUARDS_INSTALLED`.
- `adapters/http/middleware.py` — `AvernetTenantMiddleware`, a **pure ASGI**
  middleware (NOT `BaseHTTPMiddleware` — ContextVar robustness). Sets each
  request's tenant. **Covers every request already; Track A stage 2+ does not
  touch it.**
- `adapters/http/openapi_v1/dependencies.py` — `resolve_avernet_tenant(request)`:
  the single seam. Returns the default tenant today; the auth workstream swaps
  the body in place. Category-agnostic. _(Today this file holds only the
  `require_principal` stub; `resolve_avernet_tenant` lands with PR #456.)_

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

## Track B — implementing a category's endpoints (where the API lands)

Not started. Per category: replace the stub handlers in
`openapi_v1/<category>/router.py` with real implementations that call the
existing services, returning the standard `Envelope`/`Page` shapes from
`openapi_v1/contracts.py`, depending on `require_principal` /
`resolve_avernet_tenant` for identity + tenant. Because Track A already scopes
the underlying reads/writes, a correctly-written handler cannot leak across
tenants — and it runs under the request tenant the middleware set
automatically. Each category needs its own spec/plan/tasks (SDD) and its own
PR. (Per-category service-wiring details are scoped when that category's
session starts.)

---

## Endpoints per component (what each slice must implement)

The tables below are the **per-component endpoint checklists** for Track B —
who owns them, and exactly what lands. Source of truth is the **served router**
(`openapi_v1/<category>/router.py` — the stubs already carry these route
definitions); descriptions are cross-checked against the v1 contract overview
in **PR #363** (`docs/api-endpoints.zh-CN.md`, a Chinese endpoint reference by
totalfrank — still open/draft, being closed soon; kept here as reference).

> ⚠️ **Path divergence to reconcile.** The router stubs nest every non-`bots`
> group under `/openapi/v1/bots/...` (e.g. `/openapi/v1/bots/resources`,
> `/openapi/v1/bots/mcp`). PR #363's overview used **top-level** paths
> (`/openapi/v1/resources`, `/openapi/v1/mcp`, …). The **router is
> authoritative** for implementation — the paths below match it. Owners: if the
> top-level shape is the intended public surface, change the router `prefix`
> and update this section in the same PR.

All responses use the `Envelope[T]` / `Page[T]` shapes from
`openapi_v1/contracts.py` unless noted (binary streams bypass the envelope).

### 🟦 totalfrank — bots (13 endpoints) · `openapi_v1/bots/router.py`
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

### 🟦 totalfrank — channels (6 endpoints) · `openapi_v1/channels/router.py`
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

### 🟦 totalfrank — mcp (6 endpoints) · `openapi_v1/mcp/router.py`
Marketplace + tenants + the caller's unified per-server config.
| Method | Path | Purpose | Success |
|---|---|---|---|
| GET | `/openapi/v1/bots/mcp/servers` | List marketplace servers (`keyword`, paged) | `Envelope[Page[McpServer]]` |
| GET | `/openapi/v1/bots/mcp/tenants` | List MCP tenants | `Envelope[list[McpTenant]]` |
| GET | `/openapi/v1/bots/mcp/servers/{server_code}` | Server detail | `Envelope[McpServerDetail]` |
| GET | `/openapi/v1/bots/mcp/servers/{server_code}/permissions` | Caller's permission for a server | `Envelope[McpPermission]` |
| GET | `/openapi/v1/bots/mcp/servers/{server_code}/config` | Read caller's unified server config | `Envelope[McpConfig]` |
| PUT | `/openapi/v1/bots/mcp/servers/{server_code}/config` | Write config (pushed to devices) | `Envelope[McpConfig]` |

### 🟩 lucas-xzp — resources (9 endpoints) · `openapi_v1/resources/router.py`
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

### 🟩 lucas-xzp — skills (5 in stub + 2 proposed) · `openapi_v1/skills/router.py`
Catalog at `/openapi/v1/bots/skills`; a bot's installed skills are a bot
sub-resource.
| Method | Path | Purpose | Success |
|---|---|---|---|
| GET | `/openapi/v1/bots/skills` | Skill catalog (`keyword`, paged) | `Envelope[Page[Skill]]` |
| GET | `/openapi/v1/bots/skills/{skill_id}` | Skill detail | `Envelope[SkillDetail]` |
| GET | `/openapi/v1/bots/{bot_id}/skills` | List a bot's installed skills | `Envelope[list[BotSkill]]` |
| POST | `/openapi/v1/bots/{bot_id}/skills` | Install a skill on a bot (default enabled) | `201 Envelope[BotSkill]` |
| DELETE | `/openapi/v1/bots/{bot_id}/skills/{skill_id}` | Remove (unbind) a skill from a bot | `Envelope[Deleted]` |

**Proposed additions from PR #363 (★ — NOT in the router stubs yet; decide with
totalfrank before implementing):**
| Method | Path | Purpose | Success |
|---|---|---|---|
| POST ★ | `/openapi/v1/skills/upload` | Upload a custom skill (global, owned by caller) | `Envelope[Skill]` |
| PATCH ★ | `/openapi/v1/bots/{bot_id}/skills/{skill_id}` | Enable/disable an installed skill (`status`) | `Envelope[BotSkill]` |

### 🟩 lucas-xzp — routines (7 endpoints) · `openapi_v1/routines/router.py`
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

### 🟩 lucas-xzp — identity (3 endpoints) · `openapi_v1/identity/router.py`
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
2. Internal API unchanged throughout (no `to_dict()` leaks; internal suites
   unmodified).
3. **Track B:** the seven `/openapi/v1` categories' handlers implemented and
   tenant-safe, each with its own tests + PR.
4. F2 tenant-leading indexes in place (mandatory policy).
5. Background/scheduled work revisited for per-tenant correctness.
6. `resolve_avernet_tenant` wired to the real verifier (auth workstream) — the
   point at which a second tenant can safely hold real data.

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
- **Real caller-identity verifier.** Swap `resolve_avernet_tenant`'s body to
  return the gateway-forwarded principal's tenant. The seam is ready.
- **Background/scheduled work.** Resolves to the default tenant now (correct
  while all data is `teamclaw`); revisit before a 2nd tenant holds real data
  (scheduled scans, pollers, sync loops in skill_center / governance / dormant /
  device pollers).
- **Recommended:** an arch guard/lint flagging new raw `threading.Thread` /
  `ThreadPoolExecutor` in core so future in-request spawns can't silently drop
  the tenant.

---

## Gotchas learned in Stage 1 (save yourself the round-trips)

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
