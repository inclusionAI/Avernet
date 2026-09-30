# core/

Business logic for the backend module, per re-architecture proposal §5.1/5.2.

```
core/
├── routers/        # HTTP / WebSocket endpoints  (← agentclaw/servers/web/routes/)
├── services/       # Business service implementations  (← agentclaw/services/<domain>/service.py)
└── dependencies/   # FastAPI dependency providers  (← agentclaw/servers/web/dependencies/)
```

`core` depends on `api/` (contracts) and `plugin_api/` (capability interfaces).
It must NOT import from `plugins/` directly — concrete implementations
are wired in by the application bootstrap based on runtime mode.

## Migration source map

| Target (core)               | Source (current)                                  |
|-----------------------------|---------------------------------------------------|
| `core/routers/devices.py`   | `agentclaw/servers/web/routes/devices.py`         |
| `core/routers/projects.py`  | `agentclaw/servers/web/routes/projects.py`        |
| `core/routers/expert_chats.py` | `agentclaw/servers/web/routes/expert_chats.py` |
| `core/routers/system_config.py` | `agentclaw/servers/web/routes/system_config.py` |
| `core/routers/whitelist.py` | `agentclaw/servers/web/routes/whitelist.py`       |
| `core/routers/user.py`      | `agentclaw/servers/web/routes/user.py`            |
| `core/routers/hrorg.py`     | `agentclaw/servers/web/routes/hrorg.py`           |
| `core/services/device.py`   | `agentclaw/services/device/service.py`            |
| `core/services/expert_chat.py` | `agentclaw/services/expert_chat/`              |
| `core/services/project.py`  | `agentclaw/services/project/`                     |
| `core/services/channel.py`  | `agentclaw/services/channel/`                     |
| `core/services/publish.py`  | `agentclaw/services/publish/`                     |
| `core/services/system_config.py` | `agentclaw/services/system_config/`          |
| `core/dependencies/auth.py` | `agentclaw/servers/web/dependencies/auth.py`      |
| `core/app.py` (last step)   | `agentclaw/servers/web/app.py`                    |
| **Skill Center (migrated)** | |
| `core/services/skill_parser.py` | `agentclaw/services/openclawserver/server/services/skill_service.py` (SkillParser) |
| `core/services/skill_cache.py` | `agentclaw/services/openclawserver/server/services/skill_service.py` (MarketCache) |
| `core/services/skill_service.py` | `agentclaw/services/openclawserver/server/services/skill_service.py` (SkillService) |
| `core/services/skill_set_service.py` | `agentclaw/services/openclawserver/server/services/skill_set_service.py` |
| `core/services/skill_auth_service.py` | `agentclaw/services/openclawserver/server/services/skill_auth_service.py` |
| `core/services/market_sync.py` | `agentclaw/services/market_sync/service.py` |
| `core/services/skill_scan.py` | `agentclaw/services/openclawserver/server/services/skill_scan_service.py` |
| `core/services/git_sync.py` | `agentclaw/services/git_sync_service.py` |
| `core/routers/skills.py` | `agentclaw/services/openclawserver/server/routers/skills.py` |
| `core/routers/skillsets.py` | `agentclaw/services/openclawserver/server/routers/skillsets.py` |
| `core/routers/skill_auth.py` | `agentclaw/services/openclawserver/server/routers/skill_auth.py` |
| `core/routers/skill_scan.py` | `agentclaw/services/openclawserver/server/routers/skill_scan.py` |
| `core/dependencies/skills.py` | `agentclaw/services/openclawserver/server/repositories/__init__.py` |
| `plugin_api/skill_repository.py` | (new Protocol interface) |
| `plugins/local/skill_repository.py` | `agentclaw/services/openclawserver/server/repositories/sqlite_*.py` |
| `plugins/prod/skill_repository.py` | `agentclaw/services/openclawserver/server/repositories/zdas_*.py` |

> Skeleton only for non-skill-center rows — no code has been moved. Old paths are still authoritative.

## TODO — incremental migration checklist

Each row is a self-contained PR: move the file, update importers, delete
the old path, run tests, tick the box. Claim a row by appending your
name + date in the Owner column.

### Routers

#### Skill Center (← `services/openclawserver/server/routers/`) — Done (2026-04-08)

- [x] `core/routers/skills.py`
- [x] `core/routers/skillsets.py`
- [x] `core/routers/skill_auth.py`
- [x] `core/routers/skill_scan.py`

#### Other (← `servers/web/routes/`)

- [ ] `core/routers/devices.py` — Owner:
- [ ] `core/routers/projects.py` — Owner:
- [ ] `core/routers/expert_chats.py` — Owner:
- [ ] `core/routers/system_config.py` — Owner:
- [ ] `core/routers/whitelist.py` — Owner:
- [ ] `core/routers/user.py` — Owner:
- [ ] `core/routers/hrorg.py` — Owner:

### Services

#### Skill Center (← `services/openclawserver/server/services/`) — Done (2026-04-08)

- [x] `core/services/skill_parser.py`
- [x] `core/services/skill_cache.py`
- [x] `core/services/skill_service.py`
- [x] `core/services/skill_set_service.py`
- [x] `core/services/skill_auth_service.py`
- [x] `core/services/git_sync.py`
- [x] `core/dependencies/skills.py` (repository + service DI)
- [x] `plugin_api/skill_repository.py` (Protocol interface)
- [x] `plugins/local/skill_repository.py` (SQLite impl)
- [x] `plugins/prod/skill_repository.py` (ZDAS impl)

#### Other (← `services/<domain>/`)

- [ ] `core/services/device.py` — also touches DeviceAccessor wiring. Owner:
- [ ] `core/services/expert_chat.py` — Owner:
- [ ] `core/services/project.py` — Owner:
- [ ] `core/services/channel.py` — Owner:
- [ ] `core/services/publish.py` — Owner:
- [ ] `core/services/system_config.py` — Owner:
- [ ] `core/services/antprocess.py` — verify whether this stays in core
      or becomes a plugin. Owner:
- [x] `core/services/skill_scan.py` — Done (2026-04-08)
- [x] `core/services/market_sync.py` — Done (2026-04-08)
- [ ] `core/services/hrorg.py` — Owner:
- [ ] `core/services/policy.py` — Owner:

### Dependencies (← `servers/web/dependencies/`)

- [ ] `core/dependencies/auth.py` — must consume `AuthPlugin` via the
      plugin factory (Phase C in `plugins/README.md`). Owner:

### App bootstrap (last step)

- [ ] `core/app.py` ← `servers/web/app.py` — switch `main.py` over,
      delete `servers/web/`. Owner:

### Out of scope here

- `servers/sofa/`, `servers/mcp/` — external RPC entry points; keep
  under `servers/` for now. Decide their final home in a separate
  proposal.
- `services/openclawserver/` — large submodule (gateway bootstrap +
  static assets); migrate as a follow-up after Phase A/B finish.
