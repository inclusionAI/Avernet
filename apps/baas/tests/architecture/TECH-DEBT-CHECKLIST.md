# Architecture Tech-Debt Checklist

**Generated**: 2026-07-09  
**Source**: `just test-arch` (69 passed, 40 warnings) + `RULES-MANIFEST.md`  
**Severity Ratings**: 🔴 Critical / 🟡 Warning / 🔵 Guideline

---

## 1. 🔴 Oversized Source Files (35 files exceed 500 lines)

Rule 9 guideline — monolithic files that need splitting.

| File | Lines | Primary Risk |
|------|-------|-------------|
| `core/service/publish_manage/_publish_service.py` | 4,503 | Maintainability, single responsibility violation |
| `core/service/device_manage/_device_service.py` | 2,327 | Same |
| `core/service/paas/_local_paas_service.py` | 2,202 | Same |
| `core/service/paas/_facade.py` | 1,918 | Same |
| `core/service/bot_manage/_bot_management_service.py` | 1,582 | Same |
| `core/repository/device_binding/_orm_repository.py` | 1,508 | Same |
| `plugins/sandbox/arca/local_proc/_process_manager.py` | 1,113 | Same |
| `adapters/web/routers/paas_service/paas_facade_router.py` | 1,091 | Same |
| `plugins/sandbox/docker/real/_real_docker_sandbox_plugin.py` | 1,086 | Same |
| `core/service/health_check/bot/_service.py` | 991 | Same |
| `core/service/bot_run/_async_chat_client.py` | 974 | Same |
| `plugins/sandbox/k8s/real/_real_k8s_sandbox.py` | 970 | Same |
| `core/service/bot_run/_baas_service.py` | 966 | Same |
| `core/service/paas/desktop/_connection_manager.py` | 946 | Same |
| `core/service/paas/_k8s_paas_service.py` | 926 | Same |
| `core/service/paas/_arca_paas_service.py` | 872 | Same |
| `core/service/bot_manage/_bot_service.py` | 731 | Same |
| `core/repository/device/_orm_repository.py` | 694 | Same |
| `core/service/health_check/paas/_arca_paas_health_provider.py` | 674 | Same |
| `bootstrap/_core_services.py` | 646 | Same |
| `core/service/paas/_factory.py` | 640 | Same |
| `core/service/bot_run/_runner.py` | 634 | Same |
| `core/service/distributed_lock/_service.py` | 629 | Same |
| `core/repository/bot/_orm_repository.py` | 585 | Same |
| `adapters/web/websocket/local_management_ws.py` | 580 | Same |
| `core/service/bot_run/_claw_service.py` | 573 | Same |
| `core/service/api_gateway/_key_service.py` | 570 | Same |
| `adapters/web/routers/bot_service/publish_router.py` | 544 | Same |
| `adapters/web/routers/bot_service/management_router.py` | 542 | Same |
| `core/repository/publish_record/_orm_repository.py` | 531 | Same |
| (5 more files) | | |

## 2. 🔴 Fat Functions (234 functions exceed 50 lines)

Rule 9 guideline — functions that need extracting into smaller helpers/services.

### Top-10 worst offenders:

| File | Function | Lines |
|------|----------|-------|
| `core/service/device_manage/_device_service.py` | `update_device()` | 559 |
| `core/service/device_manage/_device_service.py` | `start_device()` | 517 |
| `core/service/publish_manage/_publish_service.py` | `create_publish()` | 398 |
| `core/service/publish_manage/_publish_service.py` | `execute_stage()` | 331 |
| `adapters/web/websocket/local_management_ws.py` | `local_management_websocket()` | 311 |
| `core/service/publish_manage/_publish_service.py` | `_execute_scale_batch()` | 270 |
| `core/service/publish_manage/_publish_service.py` | `_create_device_records_for_publish()` | 265 |
| `core/service/publish_manage/_publish_service.py` | `_check_stage_advancement()` | 233 |
| `core/service/publish_manage/_publish_service.py` | `complete_publish()` | 229 |
| `core/service/health_check/bot/_service.py` | `check_alive_by_bot()` | 207 |

> **Note**: `_publish_service.py` alone accounts for 20+ of the top 50 fat functions — strongest refactoring candidate in the codebase.

## 3. 🔴 Inappropriate Intimacy / Excessive Coupling (40 classes)

Classes calling ≥15 foreign methods on ≥5 distinct receivers.

### Top offenders (by foreign call count):

| Class | Foreign Calls / Receivers | Hot Paths |
|-------|--------------------------|-----------|
| `DefaultPublishService` | 386 calls / 31 receivers | logger×175, record_repo×45, device_repo×33 |
| `DefaultDeviceService` | 148 calls / 9 receivers | logger×92, repo×28 |
| `LocalPaasService` | 116 calls / 14 receivers | result×43, logger×39 |
| `OrmDeviceBindingRepository` | 114 calls / 13 receivers | log×64, q×14 |
| `DefaultBotManagementService` | 92 calls / 13 receivers | logger×62, device_repo×8 |
| `LocalProcessManager` | 81 calls / 23 receivers | logger×29, proc×7 |
| `OrmDeviceRepository` | 78 calls / 10 receivers | log×45, sa_func×9 |
| `ConnectionManager` | 71 calls / 11 receivers | logger×54 |
| `BotHealthCheckerService` | 67 calls / 10 receivers | logger×28, binding×11 |
| `AsyncChatClient` | 66 calls / 11 receivers | logger×29, payload×16 |

## 4. 🔴 Feature Envy (80+ classes logged)

Classes calling 5+ methods on the same foreign object — sign that functionality should move to the foreign class or be mediated.

**Key DI container entries** (bootstrap is expected to call providers):
- `_container.py::ApplicationContainer` → `providers` (12 calls)
- `_core_repository.py::CoreRepositoryContainer` → `providers` (22 calls)
- `_core_services.py::CoreServiceContainer` → `providers` (105 calls)

**Worst service-level feature envy**:
- `DefaultPublishService` → `logger` (175 calls)
- `DefaultDeviceService` → `logger` (92 calls)
- `OrmDeviceBindingRepository` → `log` (64 calls)
- `ConnectionManager` → `logger` (54 calls)

## 5. 🔴 `get_container()` Leakage Outside Bootstrap (6 sites)

Direct import of `secbaas.bootstrap.get_container` from non-bootstrap code — breaks DI inversion principle.

| File | Line | Module |
|------|------|--------|
| `core/repository/ws_relay_session/_factory.py` | 11 | core |
| `core/service/paas/_local_paas_service.py` | 772 | core |
| `core/service/paas/_local_paas_service.py` | 1979 | core |
| `core/utils/callback_utils.py` | 31 | core |
| `plugins/sandbox/utils/arca_utils.py` | 92 | plugins |
| `plugins/sandbox/utils/arca_utils.py` | 121 | plugins |

**Fix**: Inject via constructor DI instead of lazy `get_container()`.

## 6. 🟡 Environment Branching Leakage (125 call-sites in core/api/spi)

Rule 14 violation — env checks (`get_current_env()`, `is_dev()`, `get_local_ip()`) should be confined to `bootstrap/` and `adapters/`.

### Distribution by module:

| Module | Call-Sites |
|--------|-----------|
| `core/repository/bot_qpm/_orm_repository.py` | 4 |
| `core/repository/bot_run_queue/_orm_repository.py` | 6 |
| `core/repository/bot_session/_orm_repository.py` | 10 |
| `core/repository/distributed_lock/_orm_repository.py` | 1 |
| `core/repository/ws_relay_session/_orm_repository.py` | 4 |
| `core/service/auth_service/_auth_service.py` | 1 |
| `core/service/bot_manage/_bot_management_service.py` | 15 |
| `core/service/bot_manage/_bot_service.py` | 9 |
| `core/service/bot_run/_async_session_client.py` | 1 (is_dev) |
| `core/service/bot_run/_baas_service.py` | 1 |
| `core/service/bot_run/_bot_websocket_client.py` | 1 (is_dev) |
| `core/service/bot_runtime/dispatcher/` | 5 |
| `core/service/bot_session/_session_service.py` | 1 |
| `core/service/config_manage/_system_config_service.py` | 5 |
| `core/service/device_manage/_device_service.py` | 6 |
| `core/service/device_manage/_start_hook_dispatcher.py` | 1 |
| `core/service/paas/_facade.py` | 1 |
| `core/service/paas/_factory.py` | 4 |
| `core/service/paas/_local_paas_service.py` | 1 |
| `core/service/paas/desktop/_connection_manager.py` | 1 |
| `core/service/paas/desktop/_utils.py` | 1 (get_local_ip) |
| `core/service/publish_manage/_admin_service.py` | 1 |
| `core/service/publish_manage/_publish_service.py` | 33 |
| `core/service/tenant_manage/_tenant_manage_service.py` | 6 |
| `core/utils/env_utils.py` | 1 (is_dev) |
| `core/utils/proxypass_utils.py` | 1 |

## 7. 🟡 `aiohttp` / `fastapi` Leakage into Core (4 remaining files)

Rule 5/7 — core should not import transport frameworks.

| File | Framework |
|------|-----------|
| `core/service/bot_run/_async_session_client.py` | aiohttp |
| `core/service/bot_run/_baas_service.py` | aiohttp |
| `core/service/bot_run/_claw_service.py` | aiohttp |
| `core/service/bcn/uplink/_uplink_client.py` | aiohttp |

**Fix**: Abstract HTTP/WS clients behind an SPI.

## 8. 🟡 Silent Exception Handling (31 `except` blocks without logging or re-raise)

Rule — bare `except Exception` without `logger.exception()` or `raise` risks silent failures.

| File | Lines |
|------|-------|
| `core/repository/bot/_orm_repository.py` | 358 |
| `core/service/api_gateway/_key_gen.py` | 57 |
| `core/service/bot_run/_async_chat_client.py` | 927 |
| `core/service/bot_run/_async_chat_client_pool.py` | 334 |
| `core/service/bot_run/_executor.py` | 425 |
| `core/service/bot_run/_queue_task_message_dispatcher.py` | 213 |
| `core/service/bot_run/_task_message_dispatcher.py` | 134 |
| `core/service/bot_run/_worker.py` | 303 |
| `core/service/health_check/paas/_docker_paas_health_provider.py` | 297 |
| `core/service/health_check/paas/_k8s_paas_health_provider.py` | 86, 166 |
| `core/service/health_check/paas/_poolab_paas_health_provider.py` | 142 |
| `core/service/paas/_k8s_paas_service.py` | 387 |
| `core/service/paas/desktop/worker_router/_uds_server.py` | 352 |
| `core/service/paas/desktop/worker_router/_worker_router.py` | 320 |
| `core/service/publish_manage/_publish_service.py` | 959 |
| `core/utils/secret_utils.py` | 89 |
| `plugins/bot_service/real/_plugin.py` | 124 |
| `plugins/crypto/real/_real_crypto.py` | 116 |
| `plugins/crypto/stub/_stub_crypto.py` | 101 |
| `plugins/sandbox/arca/local_proc/_process_manager.py` | 157, 992, 1110 |
| `plugins/sandbox/desktop/_real.py` | 69 |
| `plugins/sandbox/docker/real/_real_docker_sandbox_plugin.py` | 87, 111, 676, 802, 818, 869, 892 |

## 9. 🟡 Orphan SPI — `spi/bot` Has No Plugin Implementation

| SPI Module | Expected Plugin | Status |
|-----------|----------------|--------|
| `spi/bot/` (incl. `teclaw/TeClawBotPlugin`) | `plugins/bot/` | ❌ Missing |

**Fix**: Either implement `plugins/bot/teclaw/` or remove/merge the SPI if unused.

## 10. 🟡 Missing Protocol Conformance Test (1 file)

| Protocol File | Expected Test |
|--------------|--------------|
| `spi/runner/_protocols.py` | ❌ Missing |

**Fix**: Add a conformance test for `spi/runner/_protocols.py`.

## 11. 🟡 `import-linter` Not Configured

Lazy (function-body) imports are not caught by pytestarch tests — only module-level imports are detected.

**Recommendation**: Add `import-linter` to CI enforcement per Phase 2 of the refactor plan.

## 12. 🟡 Excessive Static Methods (3 classes)

Namespace code smell — static methods should be plain module-level functions.

| Class | Static Methods |
|-------|---------------|
| `LocalProcessManager` | 9 static methods |
| `PaasServiceFactory` | 5 static methods |
| `PaasServiceFacade` | 4 static methods |

## 13. 🟡 Adapter Thinness Violations (5 known)

Pre-existing debt — domain logic leaking into adapters.

| Violation | Location |
|-----------|----------|
| `asyncio.create_task()` orchestration | `adapters/web/websocket/local_management_ws.py:460,513,525` |
| Duplicate `_filter_headers` helper | `paas_facade_router.py` + `http_router.py` |
| Auth policy in adapter | `adapters/web/routers/open_api/dependencies.py` |
| Session state mutations in router | `routers/session_router.py` |

## 14. 🔵 CamelCase Function Names (2 methods)

PEP 8 violation.

| File | Method |
|------|--------|
| `spi/auth/_models.py:28` | `tenantId()` |
| `spi/auth/_models.py:32` | `staffId()` |

## 15. 🔵 `device_manage/__init__.py` Re-exports `get_current_env` in `__all__`

Env import leaks through a public API path — remove the re-export when safe.

---

## Summary

| Category | Count | Action Required |
|----------|-------|----------------|
| 🔴 **Critical** | 80+ items | Structural debt — needs planning, significant refactoring |
| 🟡 **Warning** | 10+ items | Medium effort — clear fix path, low risk |
| 🔵 **Guideline** | 2 items | Quick wins — easy fixes |

### Recommended prioritization:

1. **Quick wins** (🔵): Rename `tenantId`/`staffId` → snake_case. Remove `get_current_env` re-export.
2. **Medium** (🟡): Add `import-linter` config. Add conformance test for `spi/runner`. Fix silent except blocks. Handle orphan SPI.
3. **High-impact** (🔴): Replace `get_container()` lazy imports with constructor DI. Extract heavily coupled services (especially `DefaultPublishService` and `DefaultDeviceService`). Split oversized files.
4. **Long-term** (🔴): Abstract env branching behind SPI. Abstract aiohttp behind SPI. Eliminate infra/ directory entirely.