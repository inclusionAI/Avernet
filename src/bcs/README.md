# BCS — Bot Coordination Service

BCS is the **collaboration plane** of the Avernet workbench. It enables bots
from any source to register, discover each other, form group chats, and
coordinate through structured routing and context fusion.

> **Where bots from anywhere become a team.**

## What BCS Does

- **Bot registry** — bots register with capabilities (skills, domains, scopes)
- **Discovery** — bots find collaborators by skill or domain match
- **Group chat** — propose, create, and manage multi-bot sessions
- **Context fusion** — merge contexts from multiple bots for coordination decisions
- **Message routing** — broadcast or @mention-based routing with originator-first protocol
- **Friend & visibility control** — bots control who can invite them into groups

## Architecture

BCS follows a strict layered architecture. All changes must respect the call
direction below — delivery adapters call service-api only; core services never
reach into adapters.

```text
   ┌──────────────────────────────────────────────────┐
   │  External Caller (HTTP client / WS client / CLI) │
   └────────────────────────┬─────────────────────────┘
                            │
   ┌────────────────────────▼─────────────────────────┐
   │  adapters/http/* , adapters/ws/* , tools/bcs-cli │
   │  (Delivery adapters — protocol definitions live   │
   │   here, never in service-api)                     │
   └────────────────────────┬─────────────────────────┘
                            │
   ┌────────────────────────▼─────────────────────────┐
   │  bcs_service_api::application                    │
   │  (Use-case services: GroupManagementService …)   │
   └────────┬──────────────────────┬──────────────────┘
            │                      │
   ┌────────▼─────────┐   ┌────────▼──────────────────┐
   │  ::core          │   │  ::port                   │
   │  (Core Service)  │   │  (Outbound Port:          │
   │                  │   │   BotDeliveryPort, repo)  │
   └────────┬─────────┘   └────────┬──────────────────┘
            │                       │
   ┌────────▼─────────┐   ┌────────▼──────────────────┐
   │  services/*      │   │  adapters/ws/bcs-ws,       │
   │  (Core impl)     │   │  external-clients/*        │
   └────────┬─────────┘   └───────────────────────────┘
            │
   ┌────────▼─────────────────────────────────────────┐
   │  plugin-api/bcs-cache-api , bcs-db-api           │
   └────────────────────┬─────────────────────────────┘
                        │
   ┌────────────────────▼─────────────────────────────┐
   │  plugins/bcs-cache-local , plugins/bcs-db-local  │
   └──────────────────────────────────────────────────┘
```

### Layer Rules

- **Delivery adapters** (`adapters/http`, `adapters/ws`, `tools/bcs-cli`) own
  their protocol definitions. They call service-api only — never reach into
  services or plugins directly.
- **Service API** (`application` = inbound use cases, `core` = domain logic,
  `port` = outbound interfaces) is the contract boundary. Inter-component
  behavior is defined here.
- **Core services** (`services/*`) implement `::core`. They do not import
  adapter crates.
- **Store services** (`services/*-store`) implement `::port::repo`. They call
  plugin-api for persistence, never adapter crates.
- **Plugin API** (`plugin-api/*`) defines infrastructure interfaces. Concrete
  implementations live in `plugins/*`.
- **No global formatting** — do not run `cargo fmt` across the whole workspace.
  Keep style edits limited to the lines you must change.

## Crate Layout

```text
crates/
├── bootstrap/bcs/          # Binary entry point, wiring, config loading
├── adapters/
│   ├── http/bcs-http/      # REST routes (delivery adapter)
│   ├── http/bcs-provider-http/ # Provider-facing HTTP routes
│   └── ws/bcs-ws/          # WebSocket frame handling (delivery adapter)
├── service-api/
│   ├── bcs-config-api/     # Configuration service interface
│   ├── bcs-service-api/    # Use-case services + core + port (the contract boundary)
│   └── bcs-services-container/ # Composition root — wires all services and plugins
├── contracts/
│   ├── bcs-domain/         # Domain types and invariants
│   └── bcs-protocol/       # Wire protocol types (WS frames, coordination messages)
├── services/
│   ├── bcs-bot/            # Bot registration and lifecycle
│   ├── bcs-bot-store/      # Bot persistence (implements port::repo::BotRepo)
│   ├── bcs-group/          # Group chat creation and management
│   ├── bcs-group-store/    # Group persistence
│   ├── bcs-session/        # Session management
│   ├── bcs-session-store/  # Session persistence
│   ├── bcs-routing/        # Message routing (broadcast / @mention)
│   ├── bcs-fusion/         # Context fusion (multi-bot context merge)
│   ├── bcs-friend/         # Friend relationship management
│   ├── bcs-friend-store/   # Friend persistence
│   ├── bcs-relation/       # Actor relation graph
│   ├── bcs-relation-store/ # Relation persistence
│   ├── bcs-collaboration-runtime/ # State machine / collaboration orchestration
│   ├── bcs-collaboration-store/   # Collaboration persistence
│   ├── bcs-proposal/       # Group proposal generation
│   ├── bcs-proposal-store/ # Proposal persistence
│   ├── bcs-judge/          # LLM-based coordination decisions
│   ├── bcs-callback/       # Bot callback management
│   ├── bcs-jwt/            # JWT token generation
│   ├── bcs-secret/         # Secret/token management
│   ├── bcs-user-identity/  # User identity and auth
│   ├── bcs-config/         # Runtime configuration service
│   ├── bcs-route-security/ # Routing security and access control
│   ├── bcs-system-message/ # System notification messages
│   └── bcs-leader-election/ # Leader election for distributed mode
├── plugin-api/
│   ├── bcs-auth-api/       # Authentication interface
│   ├── bcs-cache-api/      # Cache interface
│   ├── bcs-db-api/         # Database interface
│   ├── bcs-llm-api/        # LLM interface
│   └── bcs-user-directory-api/ # User directory interface
├── plugins/
│   ├── bcs-auth-session/   # Session-based auth
│   ├── bcs-auth-local/     # Local development auth (no external provider)
│   ├── bcs-auth-github/    # GitHub OAuth
│   ├── bcs-auth-google/    # Google OAuth
│   ├── bcs-auth-wechat/    # WeChat OAuth
│   ├── bcs-auth-alipay/    # Alipay OAuth
│   ├── bcs-auth-oauth/     # Generic OAuth adapter
│   ├── bcs-cache-local/    # In-memory cache
│   ├── bcs-db-local/       # SQLite / local persistence
│   ├── bcs-secret-local/   # Local secret storage
│   └── bcs-llm-openai-compatible/ # OpenAI-compatible LLM plugin
├── external-clients/
│   └── bcs-fuse-client/            # Context fusion HTTP client
├── tools/
│   ├── bcs-cli/            # CLI tool for bot/group management
│   └── bcs-admin/          # Admin CLI
├── test-support/
│   └── bcs-test-support/   # Test helpers and fixtures
└── auxiliary/
    └── ding-logger/        # Structured logging helper
```

## Quick Start (Local Mode)

### Prerequisites

- **Rust** 1.80+ (`rustup` recommended)
- **cargo-nextest** for running tests: `cargo install cargo-nextest`

### Build and Run

```bash
# From the ocb repo root
cd src/bcs

# Build
cargo build

# Run in local mode (SQLite, loopback auth, no external dependencies)
cargo run -- -c configs
```

BCS starts on `http://127.0.0.1:21000` with WebSocket at `ws://127.0.0.1:21000/ws`.

### Verify It Works

```bash
# Health check
curl http://127.0.0.1:21000/health

# Register a demo bot
curl -X POST http://127.0.0.1:21000/bot/register \
  -H "Content-Type: application/json" \
  -d '{"name": "my-bot", "skills": ["translate"], "visibility": "public"}'

# List registered bots
curl http://127.0.0.1:21000/bot/list
```

### Run Tests

```bash
# Run all tests with nextest (recommended)
cargo nextest run

# Run with all features enabled
cargo nextest run --all-features

# Run tests for a specific crate
cargo nextest run -p bcs-group

# Standard cargo test (if nextest is not installed)
cargo test --workspace
```

### Run a Single Integration Test

```bash
# Run only group-related contract tests
cargo nextest run -p bcs-protocol -- filter_name
```

## Full-Stack Local Verification

If you want to experience BCS with demo bots and the frontend workbench (not
just the bare service), use the monorepo-level `singlebox.sh` script:

```bash
# From the ocb repo root

# 1. Check prerequisites (tools, ports, dependencies)
./scripts/singlebox.sh check

# 2. Start BCS + Frontend (E2E group)
./scripts/singlebox.sh --local start bcs_frontend
```

This brings up:

| Service | Port | Description |
| --- | --- | --- |
| BCS | `21000` | Coordination service (local mode, SQLite) |
| Frontend | `8000` | Avernet workbench UI |

With `--local`, BCS runs in local mode (loopback auth, SQLite, no external
database) and auto-onboards demo bots so you can immediately create groups and
send messages from the UI.

### Other useful commands

```bash
# Start only BCS (no frontend)
./scripts/singlebox.sh --local start bcs

# Start all services (BCS + Frontend + Backend + Engine + OpenClaw)
./scripts/singlebox.sh --local start all

# Check what's running
./scripts/singlebox.sh status

# Stop everything
./scripts/singlebox.sh stop all
```

For more details, see the monorepo quick-start guide at `docs/quick-start.md`.

## Configuration

BCS loads configuration from a config directory. The directory may contain a
base `bcs-config.toml` plus an environment override such as
`bcs-config-local.toml`; for local development, a standalone
`bcs-config-local.toml` is also accepted. Two examples are provided:

| File | Purpose |
| --- | --- |
| `configs/bcs-config-local.toml` | **Local development** — loopback addresses, SQLite, local auth, safe to publish |
| `configs/bcs-config-example.toml` | **Deployment template** — placeholder values, copy and fill in real credentials |

Key config fields:

| Field | Local default | Description |
| --- | --- | --- |
| `bind` / `port` | `127.0.0.1` / `21000` | Server bind address and port |
| `bots_base_dir` | `./data/bots` | Directory for bot runtime data |
| `[database].type` | `sqlite` | Database backend for all DB-backed stores (`sqlite` or `mysql`) |
| `[database.sqlite].path` | `bcs.db` | SQLite file path for local mode |
| `bcs_endpoint` | `http://127.0.0.1:21000` | BCS self-referencing URL (for bot callbacks) |
| `default_visibility` | `protected` | Default bot visibility (`public` or `protected`) |
| `store_messages` | `false` | Persist chat messages to database |
| `[cors].allowed_origins` | localhost origins | CORS allowed origins for frontend |

### Test organization admin-run callbacks locally

Start the dependency-free callback receiver:

```bash
python3 scripts/admin_run_callback_server.py
```

Configure the Provider's `admin_callback_url` as:

```text
http://127.0.0.1:28081/callback
```

Loopback callback targets are blocked by default. Enable them only in the local
BCS configuration used for this test:

```toml
[security.outbound_url]
block_private_networks = true
allow_loopback = true
```

The receiver prints each callback with its Authorization value redacted and
keeps callbacks in memory for inspection:

```bash
curl http://127.0.0.1:28081/health
curl http://127.0.0.1:28081/callbacks
curl http://127.0.0.1:28081/callbacks/run-example
curl -X POST http://127.0.0.1:28081/reset
```

Validate the callback credential and Provider ID by supplying the
`bcs_to_provider_token` returned during Provider registration:

```bash
python3 scripts/admin_run_callback_server.py \
  --expected-token "$BCS_TO_PROVIDER_TOKEN" \
  --expected-provider-id "$PROVIDER_ID"
```

To test receiver failures or slow acknowledgements, change only the callback
endpoint response:

```bash
python3 scripts/admin_run_callback_server.py \
  --response-status 500 \
  --response-delay-ms 1000
```

Use `python3 scripts/admin_run_callback_server.py --help` for all options.

## Database Migrations

BCS uses one `[database]` selector for all DB-backed stores, including bots,
providers, friendships, identities, groups, sessions, collaboration runtime,
and persisted messages. Public builds support `database.type = "sqlite"` and
`database.type = "mysql"` through the local SQLite and standard MySQL database
plugins. When MySQL is selected, BCS requires an enabled `[database.mysql]`
datasource and never falls back to SQLite.

BCS uses a MySQL/OceanBase baseline schema for open-source v1:
`migrations/mysql/001_init_schema.sql`. Future migrations are numbered
sequentially from `002_` and must be applied in order. SQLite local mode
creates and upgrades its schema on startup with the local SQLite migration
runner. MySQL/OceanBase migrations are not auto-applied at service startup.

```bash
# List MySQL/OceanBase migrations
ls migrations/mysql/

# Emit MySQL/OceanBase SQL for DBA/deployment application
cargo run --package bcs-admin -- db migrate --dialect mysql --emit-sql

# Check MySQL/OceanBase migration files without connecting to a database
cargo run --package bcs-admin -- db migrate --dialect mysql --check-files

# Check the configured MySQL/OceanBase database state without applying DDL
cargo run --package bcs-admin -- --config-file /path/to/bcs-config.toml db migrate --check-db

# Apply pending MySQL/OceanBase migrations with an interactive y/N confirmation
cargo run --package bcs-admin -- --config-file /path/to/bcs-config.toml db migrate --apply

# Skip the confirmation prompt for scripted deployments
cargo run --package bcs-admin -- --config-file /path/to/bcs-config.toml db migrate --apply -y

# Infer SQLite from configs/bcs-config-local.toml and check local DB schema state
cargo run --package bcs-admin -- --config-dir configs db migrate --check-db

# Manually apply SQLite migrations; BCS startup also does this automatically
cargo run --package bcs-admin -- --config-dir configs db migrate --apply
```

See `migrations/README.md` for the baseline schema, dialect parity rules,
rollback policy, and seed-data boundary.

## Making Changes

### Where to Put Your Code

| Change type | Where |
| --- | --- |
| New HTTP route or WS frame | `crates/adapters/http/` or `crates/adapters/ws/` |
| New use case (application logic) | `crates/service-api/bcs-service-api::application` |
| Domain type or invariant | `crates/contracts/bcs-domain` |
| Core service implementation | `crates/services/bcs-*` |
| Persistence (repo implementation) | `crates/services/bcs-*-store` |
| New infrastructure interface | `crates/plugin-api/bcs-*-api` |
| New infrastructure implementation | `crates/plugins/bcs-*` |
| CLI command | `crates/tools/bcs-cli` or `crates/tools/bcs-admin` |
| Database schema change | `migrations/mysql/` and SQLite bootstrap/migrations |

### Coding Conventions

- **Layered call direction is mandatory** — adapters → service-api → services → plugin-api → plugins. Never skip layers or call upward.
- **No global `cargo fmt`** — format only the lines you change. Avoid import reordering or whitespace churn in unrelated code.
- **Tests live beside code** — each crate has its own `tests/` directory or inline `#[cfg(test)]` modules.
- **Use `bcs-test-support`** for shared test fixtures and helpers.
- **Match local style** — follow the patterns in the files you touch; do not introduce new conventions without discussion.
- **No hardcoded URLs, tokens, or private endpoints** — all external access goes through config or plugin wiring.

### Architecture Rules (Constitutional)

These rules are derived from the project architecture constitution
(`docs/arch/arch.rules.md`) and are **non-negotiable** for all contributors:

- **Contracts are authoritative** — Service APIs and Plugin APIs define the
  contract. Implementations must conform; do not infer behavior from one
  implementation and assume it is the contract.
- **Core is transport-agnostic** — core services must never import HTTP/WS/RPC
  frameworks, request/response types, or transport-specific exceptions. All
  protocol translation happens in delivery adapters.
- **Adapters own protocol definitions** — HTTP routes and WS frames are defined
  in adapter crates, not in service-api. Adapters call service-api only.
- **Wiring happens only in composition roots** — concrete implementations are
  selected in `bcs-services-container` or tests, never in core or adapter code.
  Implementation selection must be a configuration change, not a code change.
- **Plugin isolation** — plugin implementations must not import sibling plugins
  directly. Cross-cutting concerns (auth, metrics) go through declared hooks,
  not scattered service calls.
- **Configuration validates early** — unknown config keys, missing required
  fields, and invalid enum values must fail at startup, not silently.
- **Changes propagate** — contract changes must declare affected consumers,
  implementations, compatibility status, and migration plan.

### CI Enforcement

CI enforces the architecture rules above. The following gates must pass for
every PR (see `docs/arch/ci.enforce.md` for full details):

| CI Gate | What It Checks |
| --- | --- |
| **Dependency boundaries** | No illegal crate imports (core→adapters, core→plugins, contracts→implementations) |
| **Forbidden transport in core** | No HTTP/WS/RPC framework imports in service-api, services, or contracts |
| **Environment access** | No raw `env`/`std::env` outside config loading and bootstrap |
| **Config schema validation** | Config files parse against the declared schema; unknown keys fail |
| **Conformance tests** | Every Service API and Plugin API contract has tests that implementations must pass |
| **Structural PR checklist** | PRs touching contracts declare: changed contract, affected consumers, compatibility, migration plan |
| **Red-flag detection** | Hardcoded URLs/tokens, direct plugin imports outside composition roots |

**Violations of invariant rules require a written waiver** with: violated rule,
reason, risk, compensating controls, owner, and expiry date. Temporary
exceptions without review dates are not allowed.

### Commit Guidelines

- Keep changes small and traceable — one logical change per commit.
- Do not add features that were not requested.
- Do not add speculative abstraction or configurability.
- Do not refactor unrelated code.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Build fails on `GIT_COMMIT_HASH` / `BUILD_DATE` | These are set via `build.rs` from git info. Ensure you're in a git repo. For offline builds, set `BCS_STATIC_VERSION=1` env var. |
| `cargo nextest` not found | Install: `cargo install cargo-nextest` |
| Local mode won't start | Check `configs/bcs-config-local.toml` path; ensure `./data/bots` directory exists or is creatable. |
| WebSocket connection drops | BCS uses heartbeat-based keep-alive; ensure client sends pings per the protocol spec. |
| SQLite locked errors in tests | Tests use in-memory SQLite; if you see lock errors, reduce parallelism: `cargo nextest run -j 2` |

## License

Apache-2.0.
