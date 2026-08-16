# secbaas Module Conventions

## Python Import Rules

1. **Never import a private module (`_module`) from another package.** Private modules are internal to their package — only the `__init__.py` is the public face. For example:
   - ❌ `from secbaas.core.service.api_gateway._repository import APIKeyRecord` — in `api/__init__.py`
   - ✅ `from secbaas.core.service.api_gateway import APIKeyRecord` — imports via the public `__init__.py`

2. **Within the same package, use relative imports to refer to sibling private modules.** For example:
   - ✅ `from ._model import APIKeyRecord` — inside `api/api_gateway/`
   - ✅ `from ._repository import APIKeyRepository` — inside `core/service/api_gateway/`
   - ✅ `from ._protocols import APIKeyService` — inside `api/api_gateway/`

3. **Cross-package imports between private modules are allowed** when the importing file is itself a private module (not a public `__init__.py`). This is an internal implementation detail. For example:
   - ✅ `from secbaas.core.service.api_gateway._repository import APIKeyRecord` inside `api/api_gateway/_protocols.py` — OK because `_protocols.py` is private
   - ✅ `from secbaas.core.service.api_gateway._repository import APIKeyRecord` inside `api/api_gateway/_model.py` — OK because `_model.py` is private

4. **Public `__init__.py` files must only import from public package paths** or from their own package's private modules via relative imports.

## Logger Naming

Logger names directly become log file names (`{name}.log`). Prefer reusing an existing canonical name over adding a new one to reduce log-file fragmentation.

**Whitelist** — `ALLOWED_LOGGER_NAMES` in `tests/architecture/test_logger_usage.py`:

| Canonical Name | Directory Coverage |
|---|---|
| `core-service` | `core/service/**` (except bot_run, scheduler), `core/service/paas/**`, `core/service/health_check/paas/**`, `plugins/sandbox/k8s/real/_client_manager.py`, `adapters/web/websocket/**` |
| `orm` | `core/repository/**`, `core/database/_manager.py` |
| `router` | `adapters/web/routers/**` (except open_api, gateway, admin), `core/service/callback/**` |
| `router-open-api` | `routers/open_api/**` |
| `router-gateway` | `routers/gateway/**` |
| `router-admin` | `routers/admin/**` |
| `core-bot-run` | `core/service/bot_run/**`, `plugins/bot/engine_adapter/**` |
| `core-scheduler` | `core/service/scheduler/**` |
| `database` | `core/database/**`, `plugins/database/**` |
| `plugin-sandbox` | `plugins/sandbox/**` (all sandbox plugins) |
| `plugin-bot-service` | `plugins/bot_service/**` |
| `plugin-auth` | `plugins/auth/**` |
| `bootstrap` | `bootstrap/**` |
| `config` | `config/**` |
| `webserver` | `adapters/web/app.py` |

**Style rule**: names must match `^[a-z][a-z0-9-]*$` — lowercase, hyphens allowed, no underscores or uppercase. Adding a new name requires updating `ALLOWED_LOGGER_NAMES` in the architecture test.