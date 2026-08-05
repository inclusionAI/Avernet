# Plan: Tenant Isolation — MCP Configuration (Stage 5)

## Approach

Stage 1 built the whole mechanism but welded it to one model: the read guard
names `BotModel` directly (`plugin_api/models.py:148`) and the insert guard is
registered against `BotModel` (`:182`). Stage 5 needs the same treatment on two
more models that live in two other modules, so the first move is to lift the
mechanism out of `plugin_api/models.py` into a model-agnostic registrar, then
register three models through it.

- **New:** `utils/avernet_tenant_guard.py` — `register_avernet_tenant_guard(model)`.
  It keeps a registry of guarded models, installs **one** `do_orm_execute`
  listener on `Session` (the first time it is called) that appends one
  `with_loader_criteria(model, model.avernet_tenant == get_current_avernet_tenant())`
  per registered model, and installs a `before_insert` stamp per model.
- **Unchanged behavior for bots.** `BotModel` keeps its column and its
  semantics; only the *registration* moves to the shared helper. The spec
  requires the mechanism's behavior for bot records to be unchanged, and the
  Stage 1 test suite is the check.
- Three models register: `BotModel`, `UserMCPConfig`, `BotMcpCallConfigModel`.

`with_loader_criteria` for an entity absent from a statement is a no-op, so
appending all three options to every ORM statement is correct; it is one option
per guarded model rather than one listener per model so there stays a single
enforcement point to review.

The reason this is enough for the whole `mcp` Track B surface: of the six
endpoints, four (`/servers`, `/tenants`, `/servers/{code}`,
`/servers/{code}/permissions`) are served by MCP Center and `MCPAuthPlugin` over
HTTP and touch no local table — `LOCAL`-mode servers come from
`configs/local-mcp-servers.yaml` via `LocalMCPRegistry`, a file, not a table
(`core/mcp/services/local_mcp_registry.py:18`). The two `config` endpoints reach
exactly one unguarded table, `ac_user_mcp_config`. See **Alternatives
Considered** for the two tables that were checked and deliberately left alone.

## Affected Components

- `src/agentclaw/community/utils/avernet_tenant_guard.py` (new) — the
  model-agnostic registrar and both guards. Sits beside `utils/avernet_tenant.py`,
  the carrier it reads. `utils/` is not a boundary-significant module
  (`tests/community/architecture/test_module_boundaries.py:61`), so it adds no
  README obligations of its own.
- `src/agentclaw/community/plugin_api/models.py:101-186` — the guard bodies move
  out; the file keeps `BotModel`'s column and gains a
  `register_avernet_tenant_guard(BotModel)` call. `CrossTenantInsertError` is
  **re-exported** here (see Risks).
- `src/agentclaw/community/core/models/mcp.py:56` — `UserMCPConfig` gains the
  column, its `UniqueConstraint` gains `avernet_tenant`, and it registers.
- `src/agentclaw/community/core/caller_identity/models.py:30` —
  `BotMcpCallConfigModel` gains the column and registers. Its unique key is
  **not** changed (see Data Model Changes).
- `src/agentclaw/community/plugins/user_mcp_config_repository.py` — **unchanged.**
  Listed to record it was checked: every method is ORM
  (`db.query(...).first()/.all()`, `db.add()`, `Query.update()`,
  `Query.delete()`), so both guards apply without a per-method filter.
- `src/agentclaw/community/plugins/caller_identity_repository.py` — **unchanged**,
  same reason; it is ORM throughout (`:93`, `:221`, `:279`, `:302`).
- `src/backend/docs/openapi-v1/README.md` + `README.zh-CN.md` — status board.

## Data Model Changes

Two columns on two tables, plus one unique-key replacement. No new tables, no
code-side migration file (same decision as Stage 1 — schema is applied
out-of-band, so the DDL below is the authoritative statement).

```sql
-- 1. MCP per-user configuration
ALTER TABLE ac_user_mcp_config
  ADD COLUMN avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw'
    COMMENT 'data-isolation tenant; existing rows are the internal teamclaw tenant';

-- 2. Replace the unique key so two tenants can hold the same (user, server).
--    Create-before-drop: the naming convention ties an index's name to its
--    columns, so the reshape cannot happen in place, and no window may be left
--    without the uniqueness guarantee.
ALTER TABLE ac_user_mcp_config
  ADD UNIQUE KEY uix_user_mcp_config_tenant
    (avernet_tenant, user_id, server_code, env) GLOBAL;
ALTER TABLE ac_user_mcp_config
  DROP INDEX uix_user_mcp_config;

-- 3. Bot per-server MCP call identity
ALTER TABLE ac_bot_mcp_call_config
  ADD COLUMN avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw'
    COMMENT 'data-isolation tenant; existing rows are the internal teamclaw tenant';
```

**The unique key is the part that makes this stage different from Stage 1.**
`ac_bots` carries no unique constraint, so Stage 1 was a pure column add.
`ac_user_mcp_config` carries `UNIQUE (user_id, server_code, env)`
(`core/models/mcp.py:79`), and the spec's acceptance criterion *"Two tenants can
each hold a configuration for the same user identifier and the same MCP
server"* is unsatisfiable while it stands — verified against the real model on
SQLite: the second insert fails with
`UNIQUE constraint failed: ac_user_mcp_config.user_id, ac_user_mcp_config.server_code, ac_user_mcp_config.env`.
Adding a *leading* column to a unique key only ever loosens it, so every
existing row stays valid and step 2 is safe to run against live data in either
order relative to step 1's backfill.

`ac_bot_mcp_call_config`'s key `(bot_pk, server_code, engine_type, env)` needs
**no** change: `bot_pk` is `ac_bots.id`, a global auto-increment primary key
(`core/caller_identity/sql/2026_07_13_caller_identity.sql`), so a row's tenant is
already functionally determined by `bot_pk` and no cross-tenant collision is
representable.

The model-side `UniqueConstraint` must change in lockstep
(`core/models/mcp.py:79`), or the local SQLite test database enforces the old key
and the two-tenant test cannot pass locally.

**No indexes in this stage**, on the same terms as Stage 1: tenant-leading
indexes are mandatory corp policy, deferred as an explicit cross-cutting item to
the dedicated index work, and worthless today at cardinality 1. When that work
happens, `uix_user_mcp_config_tenant` is already tenant-leading and needs
nothing further; `idx_bot_mcp_call_config_aggregate` would become
`(avernet_tenant, bot_pk, engine_type, env, call_type)`.

Local and singlebox runtimes need no DDL — `Base.metadata.create_all` builds
both tables from the models, and both models share the one registry
(`core/models/mcp.py:11` and `core/caller_identity/models.py:10` resolve to the
same `core.base.Base`).

## API / Interface Changes

No HTTP surface changes. No route added, no handler wired. `avernet_tenant` is
deliberately kept out of `UserMCPConfig.to_dict()` (`core/models/mcp.py:84`);
`BotMcpCallConfigModel` has no `to_dict()`.

```python
# utils/avernet_tenant_guard.py (new)
class CrossTenantInsertError(RuntimeError): ...

def register_avernet_tenant_guard(model: type) -> None:
    """Confine every SELECT/UPDATE/DELETE on ``model`` to the request's tenant
    and stamp it on every insert. Idempotent per model."""
```

`plugin_api/models.py` re-exports `CrossTenantInsertError` so
`tests/community/plugins/test_bot_tenant_guard.py:14` keeps importing it from
where it does today.

## Key Files & Functions

- `utils/avernet_tenant_guard.py` (new):
  - `_GUARDED: dict[type, None]` — registry, insertion-ordered.
  - `_read_guard(orm_execute_state)` — the body lifted verbatim from
    `plugin_api/models.py:124-151`, with the single `with_loader_criteria` call
    replaced by a loop over `_GUARDED`. Keeps the `is_column_load` /
    `is_relationship_load` skips and the `skip_avernet_tenant_guard` execution
    option (used by the Stage 1 tests at
    `tests/community/plugins/test_bot_tenant_guard.py:84`).
  - `_insert_guard(_mapper, _connection, target)` — body lifted from `:154-170`,
    generalized to read `target.avernet_tenant`; error message names
    `type(target).__name__` instead of the hardcoded `"bot"`.
  - `register_avernet_tenant_guard(model)` — installs the `Session`-level read
    listener on first call, then `event.listen(model, "before_insert", ...)`;
    both idempotent so a re-import cannot double-register.
- `plugin_api/models.py` — delete `:101-186`, keep `avernet_tenant` on
  `BotModel` (`:67`), add `register_avernet_tenant_guard(BotModel)` immediately
  after the class, and `from ...avernet_tenant_guard import CrossTenantInsertError`
  for the re-export.
- `core/models/mcp.py:56` — on `UserMCPConfig`:
  `avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")`,
  `UniqueConstraint("avernet_tenant", "user_id", "server_code", "env", name="uix_user_mcp_config_tenant")`,
  and `register_avernet_tenant_guard(UserMCPConfig)` after the class.
- `core/caller_identity/models.py:30` — same column on `BotMcpCallConfigModel`,
  `__table_args__` unchanged, `register_avernet_tenant_guard(BotMcpCallConfigModel)`
  after the class.
- `plugin_api/README.md` — add `agentclaw.community.utils.avernet_tenant_guard`
  to `internal_dependencies`. The declared
  `agentclaw.community.utils.avernet_tenant` does **not** cover it: the checker
  matches on `d` or `d + "."` (`test_module_boundaries.py:206`), so the sibling
  module name needs its own line. `core/mcp/README.md` needs no change — the
  registration lives in `core/models/`, a legacy bucket outside the governed set.
- `docs/openapi-v1/README.md:108` / `README.zh-CN.md:95` — Stage 5 → ✅ DONE.
  `README.md:103,119` / `README.zh-CN.md:93,109` — channels Track A stage and
  Track B row → deprioritized, with the reason. Both editions also get a dated
  changelog line (`README.md:418`, `README.zh-CN.md:366`).

## Dependencies

None. SQLAlchemy 2.0 already provides everything used; no new package, no
version bump, no new internal service.

## Risks & Mitigations

- **Risk:** the unique-key swap is the one step that can fail against live data
  or be forgotten, and forgetting it produces a *silent* failure mode — the
  isolation looks correct, and a second tenant's first colliding write fails
  with a duplicate-key error nobody predicted.
  **Mitigation:** it is called out as its own DDL step with its own ordering
  note in Rollout, the model-side constraint changes in the same commit as the
  column, and the two-tenants-same-user test fails without it.

- **Risk:** lifting the guard out of `plugin_api/models.py` regresses bot
  isolation — the one thing the spec says must be unchanged.
  **Mitigation:** the whole Stage 1 tenant suite
  (`tests/community/plugins/test_bot_tenant_guard.py`,
  `test_bot_tenant_isolation.py`, `test_bot_tenant_raw_sql_and_threads.py`) runs
  unmodified. Any edit needed there is a defect in this change.

- **Risk:** `with_loader_criteria` for an entity absent from the statement is
  assumed to be a no-op; if it instead forced a join, every ORM query in the app
  would break.
  **Mitigation:** first task is a spike asserting it, mirroring how Stage 1
  spiked `Query.update()`/`delete()`. Cheap, and it gates everything else.

- **Risk:** three options are now attached to every ORM statement in the
  process, and they participate in the compiled-statement cache key.
  **Mitigation:** the count is bounded by the number of guarded models and the
  criteria are direct expressions, not lambdas (Stage 1 established that the
  lambda form is cached and would pin the first tenant — the same rule applies
  here and is the reason the loop builds expressions per call).

- **Risk:** `avernet_tenant` leaking into a response body via
  `UserMCPConfig.to_dict()` would change internal MCP responses.
  **Mitigation:** deliberately omitted; a test asserts the returned key set is
  unchanged. The router masks `api_key` and reads only named keys
  (`adapters/http/mcp/router.py:375-386`), so the surface is narrow.

- **Risk:** a non-ORM writer outside this repository inserts
  `ac_user_mcp_config` rows and bypasses the stamp (spec Open Question 3).
  **Mitigation:** `server_default='teamclaw'` catches it, which is the correct
  answer while all data is internal. Unresolved beyond that, and recorded as
  such — it determines whether that path can ever write for a second tenant.

## Alternatives Considered

- **Keep the guards in `plugin_api/models.py` and register the two new models
  from there.** Fewer files, but it would make `plugin_api` — declared as
  "Plugin Protocol declarations" — import two `core` model modules, inverting
  the dependency direction the boundary tests enforce. Rejected.
- **Register a separate `do_orm_execute` listener per model.** Equivalent in
  effect, but multiplies the enforcement points a reviewer must find, which is
  exactly what Stage 1's one-listener choice was for. Rejected.
- **Leave `ac_bot_mcp_call_config` out, treating it like `identity`.** Its
  aggregate reads (`caller_identity_repository.py:279`, `:302`) do query it by
  `bot_pk` alone with no `BotModel` in the statement, so Stage 1's guard genuinely
  does not reach them. But `bot_pk` is a global primary key and every call site
  sources it from a tenant-guarded bot lookup
  (`core/caller_identity/service.py:137,153,209`), so there is no reachable
  cross-tenant `bot_pk` and no collision analogous to two tenants both knowing
  user `12345`. Including it is defense in depth, not the closing of a live hole
  — kept because it is cheap and needs no key change, but the spec's stated
  rationale overstates the exposure.
- **Add a tenant column to `ac_entity_device_binding`.** The `PUT .../config`
  endpoint reaches it: `sync_mcp_detail_to_all_bots` resolves a device per bot
  (`core/mcp/services/sync_service.py:481`) via
  `DeviceContextResolver.resolve_for_bot`. Not needed — the query *selects* the
  binding but *joins through* `ac_bots`
  (`plugins/device_repository.py:232-248`), and `with_loader_criteria` applies to
  join clauses. Verified empirically: same query, foreign tenant returns `None`,
  owning tenant returns the row. Out of scope, recorded here so it is not
  re-opened.
- **Add a tenant column to `ac_skill_set_mcp`.** Skill-center-owned and reached
  only through the bot's skill sets; belongs to Stage 4, as the spec says.

## Rollout

Ordering, hardest constraint first:

1. **`ALTER TABLE ... ADD COLUMN` (both tables) before the code deploy.** A
   `SELECT` naming a column that does not exist fails outright, so a code-first
   deploy takes MCP config reads down. `NOT NULL DEFAULT 'teamclaw'` backfills
   every existing row in place and makes the DDL inert against currently-deployed
   code, so DDL-first is safe.
2. **The unique-key swap before a second tenant writes.** It is *not* required
   before the code deploy — with one tenant the old key and the new one accept
   exactly the same rows. It becomes load-bearing the moment a second tenant
   holds MCP configuration, which is also the moment
   `resolve_avernet_tenant` starts returning something other than the default.
   Create-before-drop, so uniqueness is never unenforced.

No feature flag: with every row and every request on the default tenant the
change is behavior-preserving by construction, and a flag would add a second
code path with nothing to switch between.

No migration file is checked in. The DDL above is the authoritative record, and
it must be handed to whoever applies out-of-band schema changes together with
the two ordering notes.

## Test Strategy

Unit — `tests/community/plugins/test_user_mcp_config_tenant_isolation.py` (new):
- A config written under tenant A is invisible to tenant B through every read on
  the protocol: `get_by_id`, `get_by_user_and_server_code`, `list_by_user`.
- `update` and `delete` against another tenant's config return `None` / `False`
  — indistinguishable from a missing row — and leave the row untouched.
- `create` stamps the current tenant with no explicit stamp at the call site;
  an explicit conflicting tenant raises `CrossTenantInsertError`.
- **Two tenants each hold a config for the same `(user_id, server_code, env)`**
  and neither can see or displace the other's. This is the test that fails
  without the unique-key change, and it is the spec's headline criterion.
- `to_dict()` key set unchanged.

Unit — `tests/community/plugins/test_bot_mcp_call_config_tenant_isolation.py` (new):
- The two aggregate reads (`list_draft_call_types`, and the call-type rollup)
  return only the current tenant's rows.
- `replace_draft_call_type` stamps the tenant; a cross-tenant explicit insert
  raises.

Unit — guard generalization:
- A registered model with no rows in the statement does not perturb an unrelated
  query (the spike, promoted to a regression test).
- The Stage 1 suite runs **unmodified** — the check that bot behavior did not
  move.

Integration:
- `GET`/`POST /mcp/user/config` through the internal router return byte-identical
  bodies to today under the default tenant.
- The existing internal API suite runs unmodified. Any edit needed there is a
  defect in this change, not a test to update.

Each cross-tenant test is written first and its red run recorded, per the spec's
"fail without this change and pass with it".

Gates: `src/backend` changes trigger backend SAST, unit tests, changed-line
coverage, and singlebox coverage. `mcp` sits in `pending_modules` with no
thresholds (`scripts/ci/singlebox_coverage_modules.yaml:273`); `utils/`,
`plugin_api/` and `core/models/` are outside the per-module Core denominators,
so no coverage manifest change is needed.
