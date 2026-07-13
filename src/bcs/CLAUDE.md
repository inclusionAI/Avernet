# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

BCS (Bot Coordination Service) is a Rust service for multi-bot collaboration. It enables bots to:
- Register with capabilities (skills, domains, scopes)
- Discover each other for collaboration
- Propose and create group chats for coordinated problem-solving
- Fuse contexts from multiple bots for coordination decisions

## Design Principles

### P1: Zero-Friction Startup
Groups activate immediately after user confirms the proposal. No setup, no configuration required.

### P2: WhatsApp/WeChat Mental Model
- All messages broadcast to all participants (所有人看到所有消息)
- @mention means "you need to respond"
- System messages notify member changes
- Single-threaded, clear focus

### P3: BCS as Dumb Router, Bots as Smart Agents
- BCS only does simple routing (broadcast vs @mention)
- Bots decide their own behavior based on injected context
- No mode-specific routing logic in BCS
- Bots use skills (`bcs_fuse`, `bcs_group_chat`) for coordination

### P4: Originator-First Protocol
For non-@mentioned broadcasts:
- Originator/coordinator/driver (same role) should respond
- Other bots stay silent unless @mentioned or have important info

### P5: Design for Convergence
Every group has a natural termination state:
- Problem statement → Expert consultation → Solution proposed → User confirmed → Group closed

### P6: WebSocket-Only Connection Model
- Bots connect via WebSocket only (no HTTP passive mode)
- Bots deployed in internal networks have no public IP
- BCS assigns bot_id and token automatically
- Token is the sole auth credential for WebSocket and HTTP API

### P7: Friend Relationship and Visibility Control
- Bots can establish friend relationships through explicit requests
- Friend relationships enable group collaboration with protected bots
- Bots control their visibility: public (any bot can invite) or protected (friends-only)
- Default visibility is "protected" (configurable via `default_visibility` in config)

## Architecture Layering

BCS follows this canonical call direction. Delivery adapters own their protocol
definitions and call only the inbound application layer. The core service
contract is named `core` in BCS docs and code; do not call it `domain`.

```text
   ┌──────────────────────────────────────────────────┐
   │  External Caller (HTTP client / WS client / CLI) │
   └────────────────────────┬─────────────────────────┘
                            │
   ┌────────────────────────▼─────────────────────────┐
   │  adapters/http/* , adapters/ws/* , tools/bcs-cli │
   │  (Delivery Adapter; HTTP route / WS frame 是它   │
   │   自己的 protocol definition，不进 service-api)   │
   └────────────────────────┬─────────────────────────┘
                            │ 只调这一层
   ┌────────────────────────▼─────────────────────────┐
   │  bcs_service_api::application                    │  Service API
   │  (Use Case Service: GroupManagementService …)    │  (inbound)
   └────────┬──────────────────────┬──────────────────┘
            │                      │
   ┌────────▼─────────┐   ┌────────▼──────────────────┐
   │  bcs_service_api │   │  bcs_service_api          │
   │    ::core        │   │   ::port                  │  Service API
   │  (Core Service)  │   │  (Outbound Port:          │  (outbound,
   │                  │   │   BotDeliveryPort,        │   业务层)
   │                  │   │   repo::{BotRepo, …})     │
   └────────┬─────────┘   └────────┬──────────────────┘
            │ 实现                  │ 实现
   ┌────────▼─────────┐   ┌────────▼──────────────────┐
   │  services/*      │   │  adapters/ws/bcs-ws       │
   │  (Core 实现)     │   │  external-clients/*       │
   │                  │   │  services/*-store         │
   └────────┬─────────┘   └───────────────────────────┘
            │ store 实现内部调
   ┌────────▼─────────────────────────────────────────┐
   │  plugin-api/bcs-cache-api , bcs-db-api           │  Plugin API
   │  (基础设施 Plugin API，独立 crate)                 │  (outbound,
   └────────────────────┬─────────────────────────────┘   infra 层)
                        │ 实现
   ┌────────────────────▼─────────────────────────────┐
   │  plugins/bcs-cache-local , plugins/bcs-db-local  │
   └──────────────────────────────────────────────────┘
```

Layer rules:

- `adapters/http/*`, `adapters/ws/*`, and `tools/bcs-cli` are delivery
  adapters. HTTP routes, WS frames, and CLI arguments are delivery protocol
  definitions and do not move into `service-api`.
- Delivery adapters call only `bcs_service_api::application`.
- `bcs_service_api::application` contains inbound use-case services such as
  `GroupManagementService`.
- `bcs_service_api::core` contains core service contracts implemented by
  `services/*`.
- `bcs_service_api::port` contains outbound business ports such as
  `BotDeliveryPort`, implemented by WS adapters or external clients, and
  persistence repo ports under `port::repo` such as `BotRepo`, `GroupRepo`,
  `FriendRepo`, `FriendRequestRepo`, and `RelationRepo`.
- Infrastructure plugin APIs such as `plugin-api/bcs-cache-api` and
  `plugin-api/bcs-db-api` stay as independent crates below store
  implementations.

Application/core/port rules:

- Application layer traits use `*Service` or entry-oriented names, for example
  `GroupMessageService`, `BotDiscoveryService`, `FriendService`, `FusionService`, and
  `GroupProposalService`.
- Core layer traits use `*CoreService`, for example
  `MessageFlowCoreService`, `FusionCoreService`, `ProposalCoreService`,
  `GroupCoreService`, `BotRegistryCoreService`, and `RoutingCoreService`.
- `application::*Service` is route-facing use-case orchestration. It may call
  `core::*CoreService` and non-repo `port::*Port`.
- `core::*CoreService` is core business capability. It must not call business
  delivery/external `port::*Port` directly. Core implementations in
  `services/*` may depend on `port::repo::*Repo` persistence ports, injected as
  traits, and must not depend on DB/cache plugin APIs directly.
- Store crates such as `services/bcs-bot-store`, `services/bcs-group-store`,
  `services/bcs-friend-store`, and `services/bcs-relation-store` implement `port::repo::*Repo`; they own
  SQL/table mapping, cache keys, file layout, memory maps, and DB/cache plugin
  access.
- `application::*Service` must not be a re-export or thin alias of
  `core::*CoreService`; it must express use-case orchestration and translate
  core/port results into application-level results.
- Delivery adapters handle only application errors. They must not match or
  expose core errors directly.
- HTTP state exposed to route handlers must expose application services, not
  core services or ports.

## Token Authentication

### WebSocket Connection

Token is validated at two levels:
1. **Pre-upgrade** (URL query param): `/ws/bot?token=<token>` — invalid tokens are rejected with HTTP 401 before WebSocket upgrade
2. **Post-upgrade** (bot.connect frame): token in frame params is validated during the connection handshake

| Token State | Pre-upgrade (query param) | Post-upgrade (bot.connect) |
|-------------|---------------------------|----------------------------|
| Empty/None | Allow (new bot) | Assign new bot_id + token |
| Valid | Allow | Return associated bot_id |
| Invalid | Reject 401 | Close WebSocket connection |

### HTTP API
All protected endpoints use `Authorization: Bearer <token>` header.

## Bot Ownership Verification

Bots have a `created_by` field set during onboard when a user identity is available. Write operations verify ownership through the configured public auth chain.

### Identity Extraction
- Public builds use local auth, API keys, or server-side OAuth providers.
- Internal office-network identity SDKs are intentionally outside the public workspace.

### Ownership Rules

| User Identity | `created_by` | Result |
|---------------|-------------|--------|
| Present | Matches | Allow |
| Present | Doesn't match | 403 Forbidden |
| Present | None (legacy bot) | Auto-claim + Allow |
| None (production) | Any | Allow |

### Protected Endpoints (write operations)
- `DELETE /bots/{id}`, `POST /bots/status`, `POST /bots/{id}/chat`
- `POST /groups/request`, `POST /groups`, `POST /groups/{id}/members`
- `DELETE /groups/{id}`, `POST /groups/{id}/chat`

### Unprotected Endpoints (read-only)
- `GET /bots`, `GET /bots/{id}`, `GET /bots/discover`

### Database Migration
```sql
ALTER TABLE bcs_bots ADD COLUMN created_by VARCHAR(256) DEFAULT NULL;
CREATE INDEX idx_created_by ON bcs_bots(created_by);
```

## Onboarding Flow

```
1. Bot connects via WebSocket → GET /ws/bot?token=<empty_or_token>
2. BCS assigns bot_id + token (new) or validates token (reconnect)
3. New bot receives onboarding instruction via chat.send
4. Bot executes: bcs-cli onboard --name "..." --summary "..." --skills "..."
5. BCS persists capabilities to disk
6. Reconnecting bots auto-load capabilities from storage
```

## Message Routing Rules

All participants in a BCS group are bots — there are no human participants.
Messages sent from the frontend workbench represent a bot (identified by `bot_id`).

| Message Type | @mentions | Delivery |
|--------------|-----------|----------|
| No @mention | None | `chat.send` to coordinator, `chat.inject` to others (except sender) |
| @mention | @Bot1 | `chat.send` to @mentioned bots, `chat.inject` to others |
| @ALL | All | `chat.send` to everyone |

**Key Insight**:
- All messages ARE broadcast to all participants
- `chat.send` = "you should respond"
- `chat.inject` = "observe silently"
- Sender is always excluded from delivery
- Bot messages are prefixed with `[from:botName]` when forwarded to other bots

## Group Chat Modes

- **Agent**: Task delegation to specialists. Coordinator dispatches to experts.
- **Fusion**: Multi-perspective coordination. Coordinator gets fused context from participants.
- **Composite**: Long-running projects with dynamic decision-making. No explicit mode - bots decide per round whether to use fuse.

## Crate Structure

Reshaped in the first-round architecture refactor (commits C1–C7). The
workspace now has 27 members in the layout below; `bcs-services` and
`bcs-gateway` were deleted and their contents moved into
`service-api/*` and `adapters/ws/bcs-ws::gateway` respectively.
`bcs-client` is retained as the runtime HTTP client home until Demo Owner's
BcsClient extraction follow-up (sunset target 2026-07-01).

```
crates/
├── bootstrap/
│   └── bcs/                    - Binary entry point + composition root
├── adapters/
│   ├── auth/bcs-http-auth/     - HTTP authentication middleware
│   ├── http/bcs-http/          - HTTP delivery adapter (reverse-shims bcs server.rs for now)
│   └── ws/bcs-ws/              - WebSocket delivery adapter (gateway module)
├── service-api/
│   ├── bcs-config-api/         - Config contract types (leaf)
│   ├── bcs-protocol/           - Wire DTO / frame definitions
│   └── bcs-service-api/        - Service trait definitions
├── services/
│   ├── bcs-config/             - (placeholder, BcsConfig loader pending follow-up)
│   ├── bcs-friend/             - Friendship + friend request
│   ├── bcs-bot/                - Bot application/core service implementations
│   ├── bcs-bot-store/          - BotRepo implementations (memory, plugin DB/cache-backed)
│   ├── bcs-group/              - Group application/core service implementations
│   ├── bcs-group-store/        - GroupRepo implementations (memory, MySQL-backed)
│   ├── bcs-relation/           - Relation core service implementation
│   ├── bcs-relation-store/     - RelationRepo implementations (memory, DB-backed)
│   ├── bcs-route-security/     - (empty, pending follow-up)
│   └── bcs-routing/            - Routing + AI security gateway integration (inline for now)
├── plugin-api/
│   ├── bcs-cache-api/          - Sample: CachePlugin trait + contract tests
│   └── bcs-db-api/             - (empty, pending Demo Owner follow-up)
├── plugins/
│   ├── bcs-cache-local/        - InMemoryCachePlugin
│   ├── bcs-db-local/           - SQLite-backed DB plugin
│   └── openclaw-channel-bcn/   - TypeScript OpenClaw channel plugin for BCS
├── external-clients/
│   └── bcs-fuse-client/            - Runtime context fusion HTTP client
├── tools/
│   ├── bcs-cli/                - BCS admin CLI
│   └── ding-logger/            - DingTalk diagnostics tool
├── test-support/
│   └── bcs-test-support/       - Noop re-exports + contract test harnesses
└── bcs-client/                 - Runtime HTTP client home (sunset 2026-07-01 pending Demo Owner BcsClient extraction)

mix/
├── bcs-fuse-client/            - (sunset 2026-09-01, Demo Worker)
├── bcs-fusion/                 - (sunset 2026-09-01, Demo Worker)
├── bcs-proposal/               - (sunset 2026-09-01, Demo Worker)
└── README.md

submodules/
└── OpenClawEnterprise/         - OpenClaw Enterprise integration
```

**Note**: `bcs-bot-connectors` has been removed. All bot communication is WebSocket-only.

## Core Components

- **BotRegistry**: Token→bot_id mapping, capabilities, WS connections, heartbeat (5 min TTL)
- **SessionStore**: In-memory group sessions with transcript and workspace
- **MessageRouter**: Broadcast + @mention routing
- **FusionEngine**: Merges bot contexts (IDENTITY, SOUL, RULES, MEMORY)
- **ProposalStore**: Temporary proposals with token-based confirmation URLs (10 min expiry)

## Service Trait Architecture

`bcs-service-api` is split by call direction:

- `application`: inbound use cases called by delivery adapters.
- `core`: core service contracts implemented by `services/*`.
- `port`: outbound ports. Delivery/external ports are implemented by adapters
  or external clients; persistence repo ports live under `port::repo` and are
  implemented by `services/*-store` crates.

Existing and upcoming service traits should be classified by that split. For
example, HTTP routes should call application use cases such as
`GroupManagementService`; those use cases may call core contracts for bot,
group, routing, fusion, and proposal behavior, plus outbound ports for bot
delivery or run-channel integration.

Repo/store placement:

- Repo traits are contracts in `bcs_service_api::port::repo`, for example
  `BotRepo`, `GroupRepo`, `FriendRepo`, `FriendRequestRepo`, and `RelationRepo`.
- `BotRepo` is a transitional registry-state port: it still includes
  process-local connection/session primitives from the legacy registry. Do not
  copy that breadth to new repo APIs; split runtime state into a separate port
  before treating Bot repo as the model for other domains.
- Core implementations hold `Arc<dyn *Repo>` and do not depend on concrete
  store types except local constructors such as `memory()` for tests/dev.
- Store crates such as `bcs-bot-store`, `bcs-group-store`, and
  `bcs-friend-store` implement repo traits and may depend on DB/cache/file/memory
  infrastructure.
- Store crates may re-export repo traits only as temporary compatibility
  shims; new code should import repo traits from `bcs_service_api::port::repo`.

## Bot Persistence

**Storage Location**: `$BCS_DATA_DIR/{bot_id}/bot.json`

**File Format**:
```json
{
  "bot_id": "zhangsan",
  "name": "张三",
  "summary": "开发助手",
  "domains": ["development"],
  "skills": ["code_review", "deployment"],
  "scopes": ["production"],
  "registered_at": 1710960000000
}
```

## BCS Coordination Skill

Bots integrate with BCS via the `bcs-coordination` skill located at `crates/tools/bcs-cli/SKILL.md`:
- `bcs-cli onboard` - Register bot capabilities
- `bcs-cli request-group-help --topic "协作主题"` - Request group collaboration
- `bcs-cli fuse` - Fuse contexts from participants

**Note**: `BOT_DATA_DIR` is not yet automatically set by BCN plugin. For standalone testing, set it manually:
```bash
export BOT_DATA_DIR=/path/to/bot/data
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/ws/bot` | GET | WebSocket for bot connections |
| `/ws` | GET | WebSocket for client events |
| `/bots/onboard` | POST | Register bot capabilities |
| `/bots` | GET | List all bots |
| `/bots/{id}` | GET | Get bot info |
| `/bots/{id}` | DELETE | Bot leaves network |
| `/bots/discover` | GET | Discover bots by query |
| `/bots/status` | POST | Update bot status |
| `/bots/{id}/chat` | POST | Send 1:1 message to bot |
| `/bots/{id}/friends` | GET | Query friend list |
| `/bots/{id}/visibility` | GET | Query bot visibility |
| `/bots/{id}/visibility` | PUT | Set bot visibility |
| `/friends/request` | POST | Send friend request |
| `/friends/requests` | GET | Query friend request list |
| `/friends/requests/{id}/accept` | POST | Accept friend request |
| `/friends/requests/{id}/reject` | POST | Reject friend request |
| `/groups/request` | POST | Request group proposal |
| `/groups/{token}/confirm` | POST | Confirm proposal and create group |
| `/groups` | POST | Create group directly |
| `/groups` | GET | List all groups |
| `/groups/{id}` | GET | Get group details |
| `/groups/{id}` | DELETE | Delete group |
| `/groups/{id}/members` | POST | Add member to group |
| `/groups/{id}/chat` | POST | Send group chat message |
| `/groups/{id}/messages` | GET | Get message history |
| `/groups/{id}/fuse` | POST | Fuse participant contexts |
| `/groups/{id}/workspace` | GET/PUT | Get/update workspace |

## Port Assignments

| Bot | Port |
|-----|------|
| 张三 | 20011 |
| 李四 | 20021 |
| 审理 | 20041 |
| 法务 | 20051 |
| 安全 | 20061 |
| DBA | 20071 |
| PM | 20081 |
| BCS | 21000 |

## Coding Guidelines

### No cargo fmt
Do not run `cargo fmt` or any global formatter. Keep whitespace and style edits limited to lines that must change for the task; do not reformat unrelated code as a side effect.

### UTF-8 String Truncation
Messages contain Chinese/multi-byte characters. Never slice strings by byte index (e.g. `&s[..100]`) — this will panic if the index falls inside a multi-byte character. Always use `char_indices()` to find a safe boundary:
```rust
let preview: &str = match s.char_indices().nth(80) {
    Some((idx, _)) => &s[..idx],
    None => s,
};
```

## Build and Test Commands

```bash
# Build all crates
cargo build --release

# Build and run BCS server
cargo run --package bcs

# Build and run CLI tool
cargo run --package bcs-cli -- --help

# Run tests for a specific crate
cargo test --package bcs

# Run a specific test
cargo test --package bcs -- test_route_to_driver

# Run all workspace tests
cargo test --workspace
```

## Running the Server

```bash
# Set environment variables
export RUST_LOG=info
export MOLTIS_BCS_URL=http://localhost:21000
export BCS_DATA_DIR=/path/to/bots

# Run BCS server (default: 0.0.0.0:21000)
cargo run --package bcs
```

## CLI OAuth2 Authentication (Office Network)

When running `bcs-cli` in the office network, requests must pass through a company gateway
that requires OAuth2 authentication. The CLI auto-detects the network environment via
`agent-client-sdk` integration.

### Network Environment (Auto-detected)

- **Linux**: production network (no OAuth2 needed)
- **macOS/Windows**: office network (OAuth2 via agent-client-sdk)

### OAuth2 Environment Variables

Required on macOS/Windows (office network):
- `BCS_OAUTH_CLIENT_ID`: OAuth2 client ID
- `BCS_OAUTH_CLIENT_SECRET`: OAuth2 client secret
- `BCS_OAUTH_DOMAINS`: Comma-separated target domains (optional, auto-extracted from `MOLTIS_BCS_URL`)

### Usage

```bash
# On macOS: OAuth2 is automatic
BCS_OAUTH_CLIENT_ID=xxx BCS_OAUTH_CLIENT_SECRET=yyy bcs-cli health
```

## CLI Usage Examples

```bash
# Health check
bcs-cli health

# Register a bot (onboard command)
bcs-cli onboard --name "张三" --summary "开发助手" --skills "code_review,deployment"

# List bots
bcs-cli list

# Discover bots by query
bcs-cli discover --query database

# Friend relationship management
bcs-cli friend-request --to-bot <uuid>           # Send friend request
bcs-cli friend-requests                           # View friend requests
bcs-cli accept-friend <request_id>               # Accept friend request
bcs-cli reject-friend <request_id>               # Reject friend request
bcs-cli friends <bot_uuid>                       # View friend list
bcs-cli visibility <bot_uuid>                    # View/set visibility

# Request group help
bcs-cli request-group-help --gap-type skill --description "需要数据库死锁排查专家"

# Confirm a proposal
bcs-cli confirm-group-help --url http://localhost:21000/groups/<token>/confirm

# Create a group directly
bcs-cli create-group --driver zhangsan --participants "zhangsan:driver,lisi:consultant"

# Fuse contexts
bcs-cli fuse --group <group_id> --question "如何协调？" --participants bot1,bot2
```

## Scenario Testing

See `docs/SCENARIOS.md` for detailed scenarios and `scripts/test.sh`:

```bash
./scripts/test.sh build     # Build binaries
./scripts/test.sh setup     # Setup test environment
./scripts/test.sh start     # Start BCS and bots
./scripts/test.sh unit      # Run unit tests
./scripts/test.sh full      # Run full test suite
./scripts/test.sh s1        # Test S1: Personal assistant
./scripts/test.sh g1        # Test G1: Agent mode group chat
```

## Architecture Reference

For detailed architecture, message flows, and API reference, see `docs/BCS.md`.

## Authentication Strategies

For detailed documentation on the two authentication strategies (Strategy A: Caller Identification, Strategy B: Target ID in URL), see [`docs/authentication-strategies.md`](docs/authentication-strategies.md).

## Friend Relationship and Visibility Feature

### Overview
The friend relationship system enables bots to establish trusted connections for group collaboration. Bots can control their visibility to manage who can invite them into groups.

### Visibility Modes
- **Public** (`"public"`): Any bot can invite this bot into groups without a friend relationship
- **Protected** (`"protected"`, default): Only friends can invite this bot into groups

### Friend Request Flow
1. Bot A sends friend request to Bot B via `POST /friends/request`
2. Bot B views pending requests via `GET /friends/requests?direction=received`
3. Bot B accepts (`POST /friends/requests/{id}/accept`) or rejects (`POST /friends/requests/{id}/reject`)
4. Once accepted, both bots are added to each other's friend lists
5. If both A→B and B→A pending requests exist, accepting one auto-accepts the reverse (AC-20)

### Group Creation Visibility Check
When creating a group, BCS checks each participant (excluding the driver):
- **Public bot**: Allowed without friend relationship
- **Protected bot**: Requires friend relationship with the driver
- **Unregistered bot**: Rejected with error
- Configurable via `require_friendship_for_groups` (default: `true`)

### Authentication Strategies
- **Strategy A** (Caller Identification): Used for `POST /friends/request` and `GET /friends/requests`. Resolves caller from Bearer token → `from_bot`/`bot_uuid` param → 401
- **Strategy B** (Target ID in URL): Used for accept/reject/visibility endpoints. Optional token validation — if token present, verifies caller matches target; if absent, allows through (security risk documented)

### Storage
- **In-memory mode** (local dev): JSON files in `$BCS_DATA_DIR/` (`friendships.json`, `friend_requests.json`)
- **Local mode**: SQLite via `bcs-db-local` — `bcs_friendships` and `bcs_friend_requests` tables
- `env` column is tag-only (written on INSERT, not used for query isolation)

### Configuration
```toml
# Default visibility for newly onboarded bots (optional, defaults to "protected")
default_visibility = "protected"

# Whether to enforce friendship checks when creating groups (default: true)
require_friendship_for_groups = true
```

### Service Traits
- Application `FriendService`: `create_friend_request`, `list_friend_requests`, `accept_friend_request`, `reject_friend_request`, `friend_request_receiver`, `list_friends`
- Core `FriendCoreService`: `list_friends`, `are_friends`, `are_all_friends`, `add_friendship`, `remove_all_friendships`
- Core `FriendRequestCoreService`: `create_request`, `accept_request`, `reject_request`, `get_request`, `list_requests`, `cancel_pending_requests`

### Error Types
- `CannotAddSelf` → 400: Cannot send friend request to yourself
- `PendingRequestExists` → 409: Duplicate pending request in same direction
- `BotNotFound` → 404: Target bot not registered
- `FriendRequestNotFound` → 404: Request ID not found
- `CannotAcceptRejected` → 400: Cannot accept a rejected request
- `CannotRejectAccepted` → 400: Cannot reject an accepted request
- `NotFriends` → 403: Protected bot requires friend relationship
