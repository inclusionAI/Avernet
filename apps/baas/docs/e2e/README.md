# E2E Coverage Enhancement — Phase Plan

**Goal**: Raise E2E coverage to 90% for all 162 API-reachable + model + bootstrap files.
**Excluded**: 37 files unreachable via E2E (real plugins, desktop infra, WebSocket adapter, arca local_proc).
**Scope**: E2E-only. Stubs only. Test first, refactor later.

## Achievability Analysis

| Category | Count | Achievable? | Reason |
|----------|-------|-------------|--------|
| API-reachable (core + adapters) | 138 | ✅ Yes | Accessed via HTTP API + stub plugins |
| API models | 17 | ✅ Yes | Covered by router tests + validation |
| Bootstrap/config | 7 | ✅ Yes | App startup paths |
| Real plugins | 6 | ❌ No | Need Docker, K8s, real Arca infra |
| Desktop infra | 16 | ❌ No | Need local desktop sandbox process |
| WebSocket adapter | 1 | ❌ No | WS connection paths require client |
| Other (arca local_proc, oauth, teclaw stub, etc.) | 14 | ❌ No | Mixed — infrastructure or stub code |

**Realistic target**: 162/199 files (81%) can reach 90%. The remaining 37 require real infrastructure.

## Approach

- **Test first, refactor later** — build coverage to create a safety net, then split large files
- **Only create SPI/stub infrastructure when tests depend on it** — existing stubs cover most needs
- **No premature refactoring** — tech-debt from `tests/architecture/TECH-DEBT-CHECKLIST.md` gets addressed AFTER coverage

## How to Use This Plan

1. Read `docs/e2e/architecture.md` first — understand the test infrastructure
2. Start at `docs/e2e/wave-01-device-hooks.md` — Phase 1.1
3. Each phase includes: what to build, what files change, how to verify
4. Complete one phase, verify, then move to the next

## Waves at a Glance

| Wave | Domain | Phases | Files | New Groups | Infrastructure |
|------|--------|--------|-------|-----------|---------------|
| 1 | Device hooks + device manage | 4 | 4 | None | None |
| 2 | Publish manage | 5 | 5 | None | None |
| 3 | PaaS service layer | 5 | 5 | 1 (`paas_operations`) | None |
| 4 | Bot run engine | 7 | 13 | 2 (`bot_run_lifecycle`, `bot_run_concurrency`) | StubBotServicePlugin env vars |
| 5 | Health + repos + routers + services | 10 | ~120 | 1 + wire 2 existing | None |

## Tech-Debt (address AFTER coverage is built)

| Priority | File | Issue | After Wave |
|----------|------|-------|-----------|
| 1 | `_publish_service.py` (4,503L) | 13 fat functions, oversized | After Wave 2 |
| 2 | `_device_service.py` (2,327L) | 2 mega-functions (559L+517L) | After Wave 1 |
| 3 | `_local_paas_service.py` (2,202L) | `get_container()` leaks (2 sites) | After Wave 3 |
| 4 | 4 files with aiohttp in core | Transport leak (Section 7) | After Wave 4 |
| 5 | Remaining fat functions + coupling + env leaks | Sections 2,3,6,8 | Continuous |

## Artifacts

- `docs/e2e/architecture.md` — Test group pattern, stub enhancement guide, coverage exclusions
- `docs/e2e/wave-01-device-hooks.md` through `wave-05-health-check.md` — Phase plans with verification
- `scripts/lib/test-stages.sh` — E2E test stage functions (already modified)
- `scripts/app.sh` — App lifecycle + coverage collection (already modified)