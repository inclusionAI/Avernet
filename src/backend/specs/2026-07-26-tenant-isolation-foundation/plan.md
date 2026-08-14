# Plan: Tenant Isolation Foundation (Stage 1)

## Approach

A task-local tenant, established once per request and enforced once at the ORM
layer. A `ContextVar` carries the tenant for the lifetime of a request — the
same mechanism the community tracer already uses for its trace id
(`plugins/community/tracer.py:25`). A middleware sets it on the way in and
resets it in a `finally`, so it cannot survive the request or leak into the
next one. Enforcement has two active halves, both keyed on the same context and
both impossible to forget:

- **Reads / updates / deletes** — a `do_orm_execute` listener appends
  `with_loader_criteria(BotModel, avernet_tenant == get_current_avernet_tenant())`
  to every `SELECT`/`UPDATE`/`DELETE` touching `BotModel`.
- **Inserts** — carry no `WHERE` clause, so the read listener cannot apply. A
  `before_insert` mapper guard on `BotModel` instead *actively* stamps
  `avernet_tenant = get_current_avernet_tenant()` on every new row, and raises
  if a caller explicitly set a different tenant. This is deliberately **not** a
  passive column default: a default only fires when the field is unset, so it
  cannot stop a wrong value a future path (a clone, a bulk migration) carries in,
  and it is a convenience rather than an enforced guarantee. The active guard
  makes the insert side symmetric with the read side — one enforcement point,
  every insert path covered.

The column carries `server_default="teamclaw"` only to backfill existing rows on
the `ALTER TABLE` and as a safety net for any non-ORM insert; the context-aware
value always comes from the `before_insert` guard.

The column and every new symbol carry the `avernet_tenant` prefix deliberately:
the bare word `tenant` already denotes the poolab sandbox-allocator's tenant in
`core/service_bot/services/baas_service.py` (an unrelated concept), so the prefix
keeps a grep for our isolation key from tangling with it.

The listener is the load-bearing choice. `BotModel` is queried directly from
nine modules outside `BotRepository` (`core/bot_dormant/*`, `core/bot_chat/*`,
`utils/cleanup_utils.py`, `plugins/caller_identity_repository.py`, and joins in
`plugins/device_repository.py` / `plugins/skill_repository.py`). A
per-method filter in `BotRepository` — the obvious mirror of the existing
`_env()` helper — would leave every one of those as a leak path and would give
reviewers ~25 places to check instead of one. One listener covers all of them
and satisfies the spec's "I cannot leak data by forgetting to add a filter".

## Affected Components

- `src/agentclaw/community/utils/avernet_tenant.py` (new) — owns the
  `ContextVar`, the default tenant constant, and the scope/inherit helpers.
  Sits beside `utils/env_utils.py`, its exact analogue.
- `src/agentclaw/community/plugin_api/models.py` — `BotModel` gains
  `avernet_tenant`, and **both** guards (the `do_orm_execute` read guard and the
  `before_insert` write guard) are **defined here**, right after the model,
  registered at model import. Neither is a Plugin/seam: each needs exactly one
  body for every profile (corp/community/test/singlebox all enforce the identical
  rule), so they do not get a standalone file in the seam package. `models.py` is
  the correct home — it is the one concrete file in `plugin_api/` (the shared
  `BotModel` all plugin impls use), and the guards are welded to that model.
  Registered on the `Session` class / the mapped class, so they apply in every
  runtime, including the out-of-tree corp `DatabasePlugin` this repo does not
  contain.
- `src/agentclaw/community/adapters/http/middleware.py` — new
  `AvernetTenantMiddleware`, wired in `install_middleware`. A **pure ASGI**
  middleware (not `BaseHTTPMiddleware`): the tenant is a `ContextVar`, and a pure
  ASGI middleware sets/reset it in the same coroutine that awaits the downstream
  app — no child-task hop — so downstream visibility and a correct reset don't
  depend on Starlette's `BaseHTTPMiddleware` context-propagation behavior.
- `src/agentclaw/community/adapters/http/openapi_v1/dependencies.py` — the
  public-API tenant source: a single plain function `resolve_avernet_tenant`,
  sitting beside the existing `require_principal` stub and following its exact
  pattern. It is **not** a DI/profile seam — there is one gateway contract, so
  one implementation for every profile; today it returns the default tenant,
  and the auth workstream replaces this one body in place when the gateway
  forwards a real principal (no endpoint changes when it lands). "Single
  replaceable seam" in the spec means this drop-in point, not a per-profile
  binding.
- `src/agentclaw/community/plugins/bot_repository.py` — **unchanged.** Listed
  only to record it was checked: `insert` builds an ORM instance and flushes, so
  the `before_insert` write guard stamps the tenant. No explicit stamp is added
  at any call site — the guard covers them uniformly.

## Data Model Changes

One column on `ac_bots`. No new tables, no code-side migration.

```sql
ALTER TABLE ac_bots
  ADD COLUMN avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw'
    COMMENT 'data-isolation tenant; existing rows are the internal teamclaw tenant';
```

**No index in Stage 1** (decision 2026-07-27). Tenant-leading indexes on a
tenant-columned table are a **mandatory corp policy**, so this is a conscious
*deferral to the dedicated index-adding work* — not an exemption; it must be
done before this is considered complete for multi-tenant. It's deferred now
because, after inspecting `ac_bots`'s current indexes and every query method,
the column adds no value yet: with a single tenant `avernet_tenant`
has cardinality 1, so an index on it prunes nothing; and every current query
already leads with a more selective, already-indexed predicate (`owner_id`,
`bot_id`/`entity_id`, `status`, `binding_id`), against which the guard's
`avernet_tenant = 'teamclaw'` is a free residual filter — so a
`(avernet_tenant, env)` index would never be chosen. It only earns its keep once
(a) a second tenant makes the column selective and (b) a tenant-scoped list query
(no more-selective predicate) exists. Revisit then — and prefer *prepending*
`avernet_tenant` to the hot composites (`idx_owner` → `(avernet_tenant, owner_id)`,
`idx_bot_id_entity_id` → `(avernet_tenant, bot_id, entity_id)`, `idx_entity` →
`(avernet_tenant, entity_id)`, and the search index) so mandatory tenant-scoping
composes with the existing access paths, rather than a standalone
`(avernet_tenant, env)` the queries can't fully use. Only those query-backing
composites need it — leave the low-cardinality (`idx_status`, `idx_is_delete`)
and unique-lookup (`idx_binding_id`) indexes alone. Because the corp naming
convention ties an index's name to its columns, a reshape can't alter columns in
place: **create the new (tenant-prepended, convention-named) index, then drop the
old one** — create-before-drop so no window is left without a usable index, and
mind online-DDL cost on the hot `ac_bots` table.

The fallback ("default") tenant is identified as **`teamclaw`** — the internal
product's own name (already used throughout the code, e.g.
`teamclaw_service_bot_publish`, `TeamClaw bot_id`). Two reasons over a generic
`"default"`: it is a meaningful identity for the one tenant that owns all
current data, and it avoids the bare literal `"default"`, which is already the
value of the *unrelated* poolab/device/mcp tenant concept in several files
(`core/bot_management/services/bot_service.py:100`,
`core/devices/services/device_service.py:1659`, …). This resolves the spec's
Open Question 3 (is `default` a safe identifier): we don't use it. `teamclaw`
must never be offered to an external registered tenant.

Adding the column with `NOT NULL DEFAULT 'teamclaw'` backfills every existing
row to `teamclaw` in place — on OceanBase/MySQL a `NOT NULL` column with a
`DEFAULT` populates all pre-existing rows, and SQLite behaves the same (moot
locally, where the table is created fresh from the model). No separate `UPDATE`
is needed, and that backfill is exactly what keeps every current internal API
response unchanged.

Local and singlebox runtimes need no DDL: `Base.metadata.create_all` builds the
table from the model (`plugins/local/database.py:180`).

Per the spec, no migration file is checked in — the DDL above is the
authoritative statement of the required shape.

## API / Interface Changes

No HTTP surface changes. No route is added, no handler is wired, no response
body gains a field (`avernet_tenant` is deliberately **not** added to
`BotModel.to_dict()` — see Risks).

New internal interfaces:

```python
# utils/avernet_tenant.py
DEFAULT_AVERNET_TENANT: Final[str] = "teamclaw"  # internal tenant; owns all current data

def get_current_avernet_tenant() -> str: ...                    # never None
@contextmanager
def avernet_tenant_scope(tenant_id: str) -> Iterator[None]: ...  # set + guaranteed reset
def bind_current_avernet_tenant(fn: Callable[P, R]) -> Callable[P, R]: ...
```

```python
# adapters/http/openapi_v1/dependencies.py — beside require_principal, same pattern
def resolve_avernet_tenant(request: Request) -> str:
    """Public-API tenant source. Placeholder until the gateway forwards a
    verified principal; the auth workstream replaces this body in place."""
    return DEFAULT_AVERNET_TENANT
```

`get_current_avernet_tenant()` returns `DEFAULT_AVERNET_TENANT` outside any
request — a total function, not `str | None`, per the type contract in
`AGENTS.md:197`.

## Key Files & Functions

- `utils/avernet_tenant.py` (new) — module above. `bind_current_avernet_tenant`
  captures the tenant at call time and re-establishes it inside the callee;
  it is the fix for `threading.Thread`, which does not copy context vars.
- `plugin_api/models.py:58` — add after `caller_config_revision`:
  `avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")`.
  `server_default` (not a Python `default=`) so `create_all` emits the same
  `DEFAULT 'teamclaw'` prod's DDL applies, backfilling existing rows and covering
  any non-ORM insert. Context-aware stamping is the `before_insert` guard's job,
  below — not this column.
- `plugin_api/models.py` (after `BotModel`) — two guards registered at module
  import, both idempotent on a module-level flag:
  - `_avernet_tenant_read_guard` via `event.listens_for(Session,
    "do_orm_execute")` — filters `SELECT`/`UPDATE`/`DELETE` (honors
    `include_aliases`; skips statements carrying
    `{"skip_avernet_tenant_guard": True}`, used only by the guard's own tests and
    the local bootstrap's table creation).
  - `_avernet_tenant_insert_guard` via `event.listens_for(BotModel,
    "before_insert")` — stamps `target.avernet_tenant =
    get_current_avernet_tenant()` when unset, and raises a
    `CrossTenantInsertError` when a different tenant was explicitly set. Covers
    every insert path (the read listener cannot, since an `INSERT` has no
    `WHERE`).
  Registered on the `Session` class / the mapped class, so both apply in every
  runtime, including the out-of-tree corp `DatabasePlugin`. Registration is
  idempotent on a module-level
  flag so a double import cannot double-register.
- `plugins/bot_repository.py:122` — **no change.** The `before_insert` guard
  stamps the tenant on every `BotModel` flush, so an explicit stamp here would be
  redundant (and would protect only this one call site). A test still asserts an
  insert carries the current tenant, and that a cross-tenant explicit insert is
  rejected — both exercising the guard, not this file.
- `adapters/http/middleware.py:204` — `AvernetTenantMiddleware` imports
  `resolve_avernet_tenant` directly (a plain function, not injected) and is
  added immediately after `UserContextMiddleware` (line 242) so it ends up
  *outside* it, and the auth plugin's own DB reads run under the request's
  tenant. `install_middleware` needs no new parameter and `app.py` needs no
  change — nothing is DI-bound.

`AvernetTenantMiddleware.dispatch` is four lines: pick
`resolve_avernet_tenant(request)` when `request.url.path` starts with
`/openapi/v1/`, otherwise `DEFAULT_AVERNET_TENANT`; enter `avernet_tenant_scope`;
`await call_next`. Starlette copies the context into the downstream task, which
is why the existing tracer middleware works the same way.

### Request-spawned work

Five in-request `threading.Thread` sites reach bot data and must be wrapped
with `bind_current_avernet_tenant`:

- `core/bot_management/services/bot_service.py:1618` — device allocation
- `core/bot_management/services/bot_service.py:2255` — cron workflow update
- `core/bot_management/services/bot_service.py:2371` — codefuse token refresh
- `core/service_bot/services/bot_publish_service.py:1163` — post-upgrade restart
- `core/bot_collaborator/services/collaborator_service.py:53` — sync/async bridge

`asyncio.create_task` sites need no change — task creation copies the context.

## Dependencies

None. SQLAlchemy 2.0 (`pyproject.toml:37`) already provides `do_orm_execute`
and `with_loader_criteria`.

## Risks & Mitigations

- **Risk:** `with_loader_criteria` is documented for ORM SELECT; the
  repository's writes go through `Query.update()` / `Query.delete()`
  (`plugins/bot_repository.py:411`, `:441`), and if the listener does not
  apply there, cross-tenant writes stay possible.
  **Mitigation:** first task in the build is a spike that asserts this
  empirically. If it does not hold, add an explicit `_avernet_tenant()` filter
  to the four write methods — bounded, and the read path still needs only the
  listener.

- **Risk:** the guard silently filters a query some internal caller expects to
  be unfiltered — e.g. `utils/cleanup_utils.py:152` sweeping deleted rows, or
  the `plugins/skill_repository.py:238` join.
  **Mitigation:** all of those run under the default tenant today, so behavior
  is unchanged now. The existing internal suite passing unmodified is the
  acceptance criterion that proves it. The real exposure is deferred to the
  stage where a second tenant holds data, and is recorded as such.

- **Risk:** naming collision. The bare word `tenant` (and `tenant_id`) already
  mean the poolab sandbox allocator's tenant in
  `core/service_bot/services/baas_service.py:1355` — an entirely unrelated
  concept.
  **Mitigation:** our column and every new symbol are prefixed `avernet_tenant`,
  so a grep never conflates the two, and the new module's docstring names the
  baas concept explicitly to warn future readers. The two never meet in one file.

- **Risk:** `avernet_tenant` leaking into API responses via `to_dict()` would
  change every current internal response body.
  **Mitigation:** deliberately omit it from `to_dict()`
  (`plugin_api/models.py:60`). A test asserts the returned key set is unchanged.

- **Risk:** the listener is registered at import of `plugin_api/models.py`; a
  test that imports the model twice could double-register.
  **Mitigation:** registration guards on a module-level flag, so a re-import is
  a no-op.

## Alternatives Considered

- **Per-method filter in `BotRepository`, mirroring `_env()`.** Explicit and
  in keeping with local style, but leaves the nine direct-query modules
  unprotected and gives no single enforcement point. Rejected.
- **Row-level security in the database.** The strongest option, but OceanBase
  support and the out-of-band DDL process make it a much larger change than
  Stage 1, and it would not cover the SQLite local runtime.
- **Setting the tenant in a FastAPI dependency rather than middleware.** Works
  for endpoints, but leaves middleware-level DB access (the auth plugin) and
  non-endpoint code outside the scope, and makes the reset path harder to
  reason about.
- **A DI-bound `AvernetTenantResolver` protocol with a `tenancy_module.py`
  binding, instead of a plain function.** Rejected as ceremony with nothing to
  switch: there is one gateway contract, so one implementation for every
  profile — a DI seam only pays off when N implementations coexist. It would
  also diverge from `require_principal`, the sibling auth seam for this same
  gateway boundary, which is a plain module-level function replaced in place.
  The plain `resolve_avernet_tenant` satisfies the spec's "single replaceable
  seam" (you replace its body) without the binding.
- **`contextvars.copy_context()` around thread spawns** instead of a
  tenant-only helper. One line shorter, but it also carries the trace id into
  background threads — a behavior change outside this spec's scope.
- **`BaseHTTPMiddleware` for the tenant middleware** (matching the tracer). It
  works on the current Starlette, but `BaseHTTPMiddleware` runs the downstream
  app in a child anyio task, so `ContextVar` set/reset landing in the right
  context has been version-dependent. A pure ASGI middleware sidesteps the whole
  class of issue, so it is used instead.

## Rollout

Ordering is a hard constraint: **the `ALTER TABLE` must be applied before the
code that reads the column is deployed.** A `SELECT` naming a column that does
not exist fails outright, so a code-first deploy takes every bot read down.
The column's `NOT NULL DEFAULT 'teamclaw'` makes the reverse order safe — the
DDL is inert against the currently-deployed code.

No feature flag. With every row and every request on the default tenant, the
change is behavior-preserving by construction; a flag would add a second code
path with nothing to switch between.

Repository convention checks a reference DDL file into `core/<module>/sql/`
(see `core/caller_identity/sql/2026_07_13_caller_identity.sql`), but **the
decision (user, 2026-07-26) is not to check one in**: the schema change is
always applied out-of-band on the platform, so the `ALTER TABLE` above is the
authoritative record and no migration file lands in the repo.

## Test Strategy

Unit:
- `avernet_tenant` context — default outside a request; set/reset; nesting;
  reset still happens when the body raises.
- `avernet_tenant_guard` — a bot inserted under tenant A is invisible to tenant
  B through every read on the protocol: `get_by_id`, `get_by_id_and_owner`,
  `list_by_owner`, `count_by_owner`, `exists_by_bot_name`, `search_bots`.
- Writes — `update_by_owner` and `soft_delete_by_owner` against another
  tenant's bot return `None` / `False`, indistinguishable from a missing row,
  and leave the row untouched.
- Inserts (`before_insert` guard) — a bot inserted under tenant B is stamped
  `avernet_tenant == "B"` with no explicit stamp at the call site; constructing
  a `BotModel` with an explicit tenant different from the current context and
  flushing raises `CrossTenantInsertError`.
- Non-repository access — a bare `session.query(BotModel).all()` is filtered,
  proving the guard reaches the direct-query modules.
- `bind_current_avernet_tenant` — a spawned thread observes the spawning tenant.
- `to_dict()` key set is unchanged.

Integration:
- Two sequential requests through the ASGI app: the second sees the default
  tenant. Same again where the first returns 500.
- The existing internal API suite runs **unmodified**. Any edit needed there is
  a defect in this change, not a test to update.

The cross-tenant read test is the one the spec requires to fail before the
change and pass after; it is written first and its red run is recorded.

Gates: `src/backend` changes trigger backend SAST, unit tests, changed-line
coverage, and singlebox coverage (`AGENTS.md:131`). No new coverage module is
needed — `utils/` and `plugin_api/` are outside the per-module Core
denominators in `scripts/ci/singlebox_coverage_modules.yaml`, and
`bot_management` is registered there without thresholds.
