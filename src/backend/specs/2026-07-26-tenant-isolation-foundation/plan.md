# Plan: Tenant Isolation Foundation (Stage 1)

## Approach

A task-local tenant, established once per request and enforced once at the ORM
layer. A `ContextVar` carries the tenant for the lifetime of a request — the
same mechanism the community tracer already uses for its trace id
(`plugins/community/tracer.py:25`). A middleware sets it on the way in and
resets it in a `finally`, so it cannot survive the request or leak into the
next one. Enforcement is a single SQLAlchemy `do_orm_execute` listener that
appends `with_loader_criteria(BotModel, avernet_tenant == get_current_avernet_tenant())`
to every ORM statement touching `BotModel`; writes are stamped by an explicit
`avernet_tenant` column with a context-derived default, exactly as `env` is today
(`plugin_api/models.py:54`).

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
  `avernet_tenant`; installs the guard at import time.
- `src/agentclaw/community/plugin_api/avernet_tenant_guard.py` (new) — the
  `do_orm_execute` listener. Registered on the `Session` **class**, so it
  applies to every session in every runtime, including the out-of-tree corp
  `DatabasePlugin` this repo does not contain.
- `src/agentclaw/community/adapters/http/middleware.py` — new
  `AvernetTenantMiddleware`, wired in `install_middleware`.
- `src/agentclaw/community/adapters/http/openapi_v1/dependencies.py` — the
  public-API tenant seam, beside the existing `require_principal` stub.
- `src/agentclaw/community/di/modules/tenancy_module.py` (new) — binds the
  placeholder resolver.
- `src/agentclaw/community/plugins/bot_repository.py` — `insert` stamps
  `avernet_tenant` explicitly (parity with its explicit `env=get_current_env()`).

## Data Model Changes

One column on `ac_bots`. No new tables, no code-side migration.

```sql
ALTER TABLE ac_bots
  ADD COLUMN avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'default'
    COMMENT 'data-isolation tenant; existing rows are the default tenant',
  ADD KEY idx_bots_avernet_tenant_env (avernet_tenant, env) GLOBAL;
```

The `DEFAULT 'default'` is what makes every pre-existing row belong to the
default tenant without a backfill statement, which is the acceptance criterion
that keeps current internal API responses unchanged.

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
DEFAULT_AVERNET_TENANT: Final[str] = "default"

def get_current_avernet_tenant() -> str: ...                    # never None
@contextmanager
def avernet_tenant_scope(tenant_id: str) -> Iterator[None]: ...  # set + guaranteed reset
def bind_current_avernet_tenant(fn: Callable[P, R]) -> Callable[P, R]: ...
```

```python
# adapters/http/openapi_v1/dependencies.py
class AvernetTenantResolver(Protocol):
    def resolve(self, request: Request) -> str: ...

class DefaultAvernetTenantResolver:
    """Placeholder until the caller-identity verifier lands."""
    def resolve(self, request: Request) -> str:
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
  `avernet_tenant = Column(String(64), default=get_current_avernet_tenant, nullable=False)`.
  The callable default mirrors `env`'s `default=get_current_env` on line 54, so
  a bare `session.add(BotModel(...))` from any module is stamped correctly.
- `plugin_api/models.py` (bottom) — `install_bot_avernet_tenant_guard(BotModel)`.
  Called from `models.py` rather than a composition root so that any code path
  able to import the model is also guarded; taking the model as an argument
  keeps `avernet_tenant_guard.py` free of an import cycle.
- `plugin_api/avernet_tenant_guard.py` (new) — `event.listens_for(Session,
  "do_orm_execute")`; skips statements carrying an explicit
  `{"skip_avernet_tenant_guard": True}` execution option (used only by the
  guard's own tests and by the local bootstrap's table creation).
- `plugins/bot_repository.py:122` — add `avernet_tenant=get_current_avernet_tenant()`
  beside `env=get_current_env()`.
- `adapters/http/middleware.py:204` — `install_middleware` gains an
  `avernet_tenant_resolver` parameter; `AvernetTenantMiddleware` is added
  immediately after `UserContextMiddleware` (line 242) so it ends up *outside*
  it, and the auth plugin's own DB reads run under the request's tenant.
- `adapters/http/app.py:251` — pass `injector.get(AvernetTenantResolver)`.
- `di/container.py:117` — register `TenancyModule()` beside
  `CallerIdentityModule()`.

`AvernetTenantMiddleware.dispatch` is four lines: pick
`resolver.resolve(request)` when `request.url.path` starts with `/openapi/v1/`,
otherwise `DEFAULT_AVERNET_TENANT`; enter `avernet_tenant_scope`; `await
call_next`. Starlette copies the context into the downstream task, which is why
the existing tracer middleware works the same way.

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
  **Mitigation:** `install_bot_avernet_tenant_guard` is idempotent on a
  module-level flag.

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
- **A plain module-level `resolve_public_tenant()` function instead of a
  DI-bound protocol.** Fewer moving parts, but the composition root is where
  this codebase selects implementations, and the auth workstream needs a
  binding it can override without editing a shared function body.
- **`contextvars.copy_context()` around thread spawns** instead of a
  tenant-only helper. One line shorter, but it also carries the trace id into
  background threads — a behavior change outside this spec's scope.

## Rollout

Ordering is a hard constraint: **the `ALTER TABLE` must be applied before the
code that reads the column is deployed.** A `SELECT` naming a column that does
not exist fails outright, so a code-first deploy takes every bot read down.
The column's `NOT NULL DEFAULT 'default'` makes the reverse order safe — the
DDL is inert against the currently-deployed code.

No feature flag. With every row and every request on the default tenant, the
change is behavior-preserving by construction; a flag would add a second code
path with nothing to switch between.

Repository convention checks a reference DDL file into `core/<module>/sql/`
(see `core/caller_identity/sql/2026_07_13_caller_identity.sql`). The spec
explicitly decided against a checked-in migration file for this change, so the
DDL lives in this plan instead. Worth reconsidering before implementation.

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
