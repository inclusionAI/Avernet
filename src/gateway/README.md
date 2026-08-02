# gateway-community

teamclawgw community edition — open-source gateway skeleton.

## Architecture

The gateway uses **dependency injection** (via `dependency_injector`) to achieve a
pluggable architecture. Every swappable component follows the same pattern:

```
SPI Protocol  →  Stub/Bare Implementation  →  PluginContainer Selector
```

### Plugin Container

The composition root is `PluginContainer` (`bootstrap/plugins/_plugin_core.py`),
which exposes 6 **Selectors** that resolve the active implementation at runtime:

| Selector              | Config Key                   | Bare Default              | Purpose                                  |
|-----------------------|------------------------------|---------------------------|------------------------------------------|
| `database`            | `plugins.database.plugin_database` | `SQLITE_ORM`        | Database access (sync + async ORM)       |
| `forwarder`           | `plugins.forwarder`          | `bare`                    | Upstream HTTP request forwarding         |
| `schema_catalog`      | `plugins.schema_catalog`     | `bare`                    | Upstream schema discovery and caching    |
| `cache_plugin`        | `plugins.cache`              | `stub`                    | Distributed cache abstraction            |
| `app_token_validator` | `plugins.authn.app_token`    | `bare`                    | Application token validation             |
| `tenant_resolver`     | `plugins.authn.tenant`       | `bare`                    | Multi-tenant resolution                  |

Each Selector maps a config string to a `providers.Singleton` or `providers.Callable`.
Runtime configuration is read from a single `application.yaml`; plugin selectors use `user_config.plugins.*`, while authn chains, route security, and upstream routing live under `user_config` in the same file.

```text
application.yaml
    └── user_config: UserConfig
            └── plugins: PluginConfig
                    ├── forwarder: "bare"       → HttpxForwarder
                    ├── schema_catalog: "bare"  → FileSchemaCatalog
                    ├── cache: "stub"           → InMemoryCachePlugin
                    ├── authn.app_token: "bare"    → StubAppTokenValidator
                    ├── authn.tenant: "bare"       → StubTenantResolver
                    └── database.plugin_database: "SQLITE_ORM" → SqliteDatabasePlugin
```

### Bootstrap Flow

1. `get_container()` creates the `ApplicationContainer` singleton.
2. `_inject_enterprise_plugins()` checks for enterprise extensions and injects
   additional selector options if available.
3. `init_container_config()` loads defaults + `application.yaml` overrides into
   the container's `config` provider.
4. `initialize_services()` wires web adapters and resolves all plugin providers.
5. `shutdown_services()` tears down all providers on exit.

### Pre-Bootstrap Plugins

Some services are needed before the full DI container is available (tracing,
logging, the runner). These use Python **entry points** rather than the
PluginContainer Selector pattern:

| Plugin | Entry Point Group  | Bare Default                                      |
|--------|--------------------|---------------------------------------------------|
| Runner | `gateway.runner`   | `gateway.community.plugins.runner.bare:BareAppRunnerPlugin` |
| Logger | `gateway.logger`   | `gateway.community.plugins.logger.bare:BareLoggerPlugin`     |
| Tracer | `gateway.tracer`   | `gateway.community.plugins.tracer.bare:BareTracerPlugin`     |

### Enterprise Extension

Enterprise packages can register additional plugin implementations without
modifying community code. The `plugin_registry` module provides:

- `register_plugin_option(plugin_name, option_name, factory)` — register a new
  implementation for an existing Selector.
- `inject_into_plugin_container(container)` — inject all registered options
  into the active PluginContainer at bootstrap time.

This is called during `get_container()` before any providers are resolved.

### Adding a New Plugin

1. **Define the SPI protocol** — create a `Protocol` class in
   `spi/<domain>/_protocols.py`. This is the contract all implementations must
   satisfy.
2. **Create a bare/stub implementation** — in `plugins/<domain>/bare/`, create
   a class that satisfies the protocol with minimal open-source behavior.
3. **Add a Selector to PluginContainer** — in
   `bootstrap/plugins/_plugin_core.py`, add a new `providers.Selector` field
   keyed to the relevant config path, with at least one default option.
4. **Add the config schema** — extend `PluginConfig` (or a nested config) in
   `config/_models.py` with the new selector key and its allowed values.
5. **Register enterprise implementations** (optional) — use
   `plugin_registry.register_plugin_option()` to add production options.

### Directory Layout

```text
src/gateway/
├── configs/                    # Single application.yaml plus schema artifacts
├── docs/                       # Design notes (auth, delegated OAuth)
├── scripts/                    # CI and utility scripts
├── specs/                      # Architecture specifications
├── src/gateway/community/
│   ├── adapters/web/           # FastAPI (HTTP delivery)
│   ├── api/                    # Transport-agnostic service protocols
│   ├── bootstrap/              # Composition root (DI wiring)
│   │   └── plugins/            # PluginContainer definition
│   ├── config/                 # Configuration loading and models
│   ├── core/                   # Domain logic (transport-agnostic)
│   ├── plugins/                # Plugin implementations (bare/stub)
│   ├── spi/                    # Service Provider Interfaces (Protocols)
│   ├── main.py                 # CLI entry point
│   ├── plugin_accessor.py      # Legacy entry-point-based plugin loader
│   └── plugin_registry.py      # Enterprise plugin injection
└── tests/                      # All test suites
    ├── architecture/           # Architecture conformance tests
    ├── contracts/              # SPI contract tests
    ├── e2e/                    # End-to-end tests
    ├── integration/            # Integration tests
    └── unit/                   # Unit tests
```

## Quick Start

```bash
cd src/gateway
uv sync

# Run in bare mode (no enterprise dependencies)
python -m gateway.community.main --mode bare

# Or with a config file
python -m gateway.community.main --mode bare --config configs/application.yaml
```

The gateway starts on port 8888 by default and exposes:

- `http://127.0.0.1:8888/health` — liveness probe
- `http://127.0.0.1:8888/docs` — OpenAPI documentation (when enabled)
- `http://127.0.0.1:8888/api/test` — connectivity test endpoint

## CI & Testing

```bash
# Lint and format
just lint
just format

# Run unit tests
just test-ut

# Run full CI pipeline
just test mode=bare overlay=e2e-sqlite
```