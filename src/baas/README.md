# secbaas-community

**SecBaaS Community Edition** — the open-source, single-box runtime of the TeamClaw AI bot platform.

This package provides the core server that manages AI bot lifecycles: creation, sandbox provisioning, runtime scheduling, session management, API gateway, billing control, and third-party integrations.

## Quick Start

```bash
# Install dependencies
uv sync

# Run in bare mode (standalone, no sidecar)
python src/secbaas/community/main.py --config configs/ --mode bare

# Run in SOFA mode (with MOSN/Layotto sidecar)
python src/secbaas/community/main.py --config configs/ --mode sofa
```

## Architecture

```
src/secbaas/
├── api/            # HTTP API layer — route definitions & request models
│   ├── api_gateway/    # API key management & access control
│   ├── auth/           # Authentication endpoints
│   ├── bcn/            # BCN downlink protocol
│   ├── bot_manage/     # Bot CRUD
│   ├── bot_qpm/        # Bot QPM configuration
│   ├── bot_runtime/    # Bot runtime status
│   ├── config_manage/  # System configuration API
│   ├── device_manage/  # Device lifecycle API
│   ├── health_check/   # Health check endpoints
│   ├── open_api/       # Public REST API
│   ├── paas/           # Sandbox PaaS API
│   ├── publish_manage/ # Bot publishing API
│   ├── sse/            # Server-sent events
│   ├── template_manage/# Bot template API
│   └── tenant_manage/  # Multi-tenant management
├── adapters/       # Transport adapters (FastAPI web app)
├── bootstrap/      # DI container wiring, cron jobs, startup
├── config/         # Config loader & models
├── core/           # Domain logic — services, repositories, DB
│   ├── service/        # Business services (23 domains)
│   ├── repository/     # Data access layer
│   ├── database/       # Database adapters
│   └── utils/          # Shared utilities
├── logger/         # Structured logging
├── plugins/        # Pluggable backends (auth, crypto, cache, DB, sandbox, scheduler, etc.)
├── spi/            # Service Provider Interface — abstract plugin contracts
└── main.py         # Unified entry point
```

### Layers

| Layer | Responsibility |
|---|---|
| **SPI** (`spi/`) | Abstract interfaces / contracts for each plugin domain |
| **Plugins** (`plugins/`) | Concrete implementations selected via config (stub/real) |
| **Core** (`core/`) | Domain services, repositories, and database access |
| **API** (`api/`) | Request/response models, validation, protocol definitions |
| **Adapters** (`adapters/`) | FastAPI app, middleware, DI wiring, WSGI server |
| **Bootstrap** (`bootstrap/`) | Composition root — assembles containers, starts services |

### Plugin System

The runtime uses an SPI-based plugin model. Each capability (auth, crypto, cache, database, sandbox, scheduler, etc.) has:

1. An **interface** in `spi/` (the contract)
2. One or more **implementations** in `plugins/` (e.g., `stub` for local dev, `real` for production)
3. A **config-driven switch** in `application.yaml` to select which implementation to use

### Runtime Modes

- **Bare mode** — standalone FastAPI server, no sidecar dependency. Ideal for local development and single-box deployments.
- **SOFA mode** — runs behind MOSN/Layotto sidecar for service mesh integration. Requires the `secbaas.enterprise` package.

## Configuration

See [configs/application.yaml](configs/application.yaml) for the full configuration reference. Environment-specific overlays are in `configs/overlays/`.

Key configuration sections:

| Section | Description |
|---|---|
| `module_config.web` | HTTP server settings (port, workers) |
| `user_config.plugins` | Plugin backend selection (stub/real) |
| `user_config.plugins.sandbox` | Sandbox provider configs (Arca, K8s, Docker, etc.) |
| `user_config.bot_service` | Bot runtime proxy settings |
| `user_config.bot_run_queue` | Task queue worker config |
| `user_config.device_ttl_timer` | Device TTL renewal schedule |
| `user_config.bot_runner` | Concurrency limits per bot |

## Development

```bash
# Lint & format
just lint
just format

# Unit tests
just test-ut

# Integration tests (requires SQLite)
just test-it

# Architecture tests
just test-arch

# E2E tests (requires running app)
just test-e2e

# Full CI pipeline
just test
```

`just test` enforces the changed-line coverage gate against
`git merge-base HEAD origin/dev`, matching GitHub CI. Set
`AVERNET_LOCAL_TEST_BASE=<ref>` to override the base, or
`AVERNET_LOCAL_TEST_NO_FETCH=1` to skip the auto-fetch. Use `just test-no-cov`
for quick feedback without the coverage gate.

### Environment Variables

| Variable | Purpose |
|---|---|
| `SERVER_ENV` | Deployment environment (dev, prepub, prod) |
| `SOFAPY_CONFIG_OVERLAY` | Additional YAML overlay path |
| `DEPLOY_ENV` | Custom deploy-env probe variable |

## Project Conventions

- **Python**: 3.12+, strict static typing (mypy + pyright)
- **Imports**: Private modules (`_module`) never imported cross-package from public `__init__.py` files — see [src/secbaas/CLAUDE.md](src/secbaas/CLAUDE.md)
- **Tests**: pytest with markers for `unit`, `integration`, `e2e`, `architecture`
- **Config**: YAML-based with env-specific overlays