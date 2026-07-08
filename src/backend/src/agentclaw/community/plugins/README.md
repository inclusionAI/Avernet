# plugins/

Concrete implementations of the capability protocols defined in
`agentclaw/plugin_api/`. Selected at bootstrap based on runtime mode.

## Migration source map

| Plugin    | local impl source                                            | prod impl source                                       |
|-----------|--------------------------------------------------------------|--------------------------------------------------------|
| auth      | (anonymous / cookie — no current implementation)             | `agentclaw/infrastructure/auth.py`                     |
| cache     | (TODO — currently uses ZCache via MOSN even in local mode)   | `agentclaw/infrastructure/cache.py`                    |
| database  | `agentclaw/services/openclawserver/server/db.py` (SQLite)    | `agentclaw/infrastructure/database/connection.py`      |
| storage   | `agentclaw/infrastructure/local_fs_storage.py`               | `agentclaw/infrastructure/oss_storage.py`              |
| device    | `agentclaw/services/device/service_local.py`                 | `agentclaw/services/device/service_arca.py` + `agentclaw/infrastructure/arca_*.py` |

> Skeleton only — none of these have been moved yet. Files here contain
> empty class stubs that intentionally do **not** import the existing
> infrastructure modules, so importing this package never breaks the
> currently-running backend.

## Out of scope (handled elsewhere)

The following capabilities are intentionally **not** mirrored under
`plugins/` here, because they live in their own top-level modules:

| Capability | Where it lives                              |
|------------|---------------------------------------------|
| engine     | `ocb/src/engine/` (Adapter module)          |
| bcs        | `ocb/src/bcs/`   (Rust coordination service)|
| bcn        | engine-side BCN plugin (proposal §5.3)      |

The backend will only ever have *client-side* contracts for these in
`agentclaw/plugin_api/{engine,bcs,bcn}.py`; their concrete servers are
owned by the corresponding modules.

---

## TODO — incremental migration checklist

Each row below is a self-contained migration unit. Pick one, open a PR
that (a) moves the code, (b) updates importers, (c) deletes the old
file, (d) adds/updates tests, (e) ticks the box here.

**Convention:** when claiming a row, append your name + date in the
"Owner" column so others don't pick it up. Mark `[x]` only when the old
path has been deleted and CI is green.

### Phase A — leaf plugin_api (no cross-dependency)

- [ ] **storage / local** — move `infrastructure/local_fs_storage.py`
      → `plugins/local/storage.py`. Owner:
- [ ] **storage / prod** — move `infrastructure/oss_storage.py`
      → `plugins/prod/storage.py`. Owner:
- [x] **cache / prod** — move `infrastructure/cache.py`
      → `plugins/prod/cache.py` as `ZCachePlugin`.
- [x] **cache / local** — implement `MemoryCachePlugin` (in-process dict
      with TTL, removes the MOSN dependency in `--local` mode).
- [x] **auth / prod** — move `infrastructure/auth.py`
      → `plugins/prod/auth.py` as `BuserviceAuth`.
- [x] **auth / local** — implement `LocalAuth` (cookie-based identity).

### Phase B — database & device (touches many call sites)

- [ ] **database / prod** — move `infrastructure/database/connection.py`
      → `plugins/prod/database.py`, define a real `DatabasePlugin`
      protocol first. Owner:
- [ ] **database / local** — wrap `services/openclawserver/server/db.py`
      (SQLite init) into `plugins/local/database.py`. Owner:
- [ ] **device / local** — move `services/device/service_local.py`
      + `services/device/sqlite_*.py`
      → `plugins/local/device.py`. Owner:
- [ ] **device / prod** — move `services/device/service_arca.py`
      + `infrastructure/arca_client.py`
      + `infrastructure/arca_factory.py`
      → `plugins/prod/device.py`. Owner:
- [ ] **device / daas** — decide whether `services/device/service_daas.py`
      becomes a third impl or is folded into `prod/device.py`. Owner:

### Phase C — wiring & cleanup

- [ ] Add a plugin factory (e.g. `agentclaw/core/dependencies/plugin_api.py`)
      that picks `local` vs `prod` from `RUNTIME_MODE` /
      `LOCAL_DEV_MODE` and exposes them via FastAPI `Depends`. Owner:
- [ ] Switch `servers/web/dependencies/auth.py` to consume `AuthPlugin`
      via the factory; delete the old direct import. Owner:
- [ ] Delete `agentclaw/local/` once `NoAuth` + `MemoryCache` cover its
      responsibilities. Owner:
- [ ] Delete the now-empty `agentclaw/infrastructure/` once all six
      Phase A/B rows are checked. Owner:

### Phase D — protocol hardening (can run in parallel with A–C)

- [ ] Flesh out each `plugin_api/<name>.py` Protocol with real method
      signatures + Pydantic types. One PR per plugin so reviews stay
      small. Owner per plugin:
  - [x] auth
  - [x] cache
  - [ ] storage
  - [ ] database
  - [ ] devices
  - [ ] engine (client side only)
  - [ ] bcs (client side only)
  - [ ] bcn (client side only)
