# BCS Bridge

`bcs-bridge` connects a local engine (Claude Code, Codex or QwenWork) to BCS as
a Bot. All three are members of the BCS workspace (`apps/bcs`) and follow its shared
version and lint configuration. They live under `apps/bcs/crates/tools/bcs-bridge/`:

```text
bcs-bridge/        # grouping directory
├── core/          # bcs-bridge-core
├── app/           # bcs-bridge-app
└── cli/           # bcs-bridge-cli (binary name: bcs-bridge)
``` It registers the Bot with a Provider-scoped registration token, then serves
BCS work either as a **gateway** (BCS delivers Provider 2.0 webhooks to the
bridge's `/webhook`) or as a **plugin** (the bridge dials the BCS Bot WebSocket
`/ws/bot`). Each turn invokes an engine adapter and streams its
output back to BCS as chat, thinking, tool and interaction (HITL) events.

## Crates

| Crate | Kind | Responsibility |
| --- | --- | --- |
| `bcs-bridge-core` | library | Runtime: Provider 2.0 webhook and Bot WebSocket adapters, runs, durable sessions (SQLite), HITL interactions, the engine plugin interface and the built-in engine drivers |
| `bcs-bridge-qwenwork` | library | Native desktop adapter: local WeCom-compatible WS, HTTP Hooks and text/tool translation; registered by the CLI composition root |
| `bcs-bridge-app` | library | CLI application: `register`/`start`/`status`/`stop`, configuration, instance management, registration and the application plugin interfaces |
| `bcs-bridge-cli` | binary | The open-source distribution: `bcs-bridge-app` with its default plugins; installs the `bcs-bridge` command |

The only BCS dependency is the `bcs-protocol` contract crate.

## Usage

```bash
cargo install --locked --manifest-path apps/bcs/Cargo.toml -p bcs-bridge-cli

# Gateway: BCS delivers work to https://<public-host>/webhook, forwarded to --listen.
export BCS_BRIDGE_PROVIDER_TOKENS='{"<provider-id>":"<webhook-token>"}'
bcs-bridge register --token '<registration-token>' --bot-name 'My Bot' \
  --mode gateway --api-url https://<bcs-host>/<prefix> \
  --webhook-url https://<public-host>/webhook --provider-bot-ref my-bot
bcs-bridge start            # foreground; --daemon to run in the background

# Plugin (the default `--mode`, matching the BCS register API): the bridge connects
# to the BCS Bot WebSocket; no webhook or webhook token.
bcs-bridge register --token '<registration-token>' --bot-name 'My Bot' \
  --api-url https://<bcs-host>/<prefix> --upstream-url wss://<bcs-host>/ws/bot \
  --engine codex
bcs-bridge status
bcs-bridge stop
```

- The registration token comes from `GET /openapi/v1/collaboration/register/token`;
  the bridge reads its unverified v2 metadata only to select the Provider and
  check the requested mode. BCS verifies it on registration.
- `--provider-auth static-bearer` (default): gateway Bots need `--provider-bot-ref`;
  plugin Bots get a generated `bridge-<uuid>` reference when it is omitted.
- `--engine claude-code|codex` (default `claude-code`) looks up `claude` / `codex`
  on `PATH` unless `--engine-bin` is given; `--model` and `--cwd` (default
  `~/workspace`) are optional.
- Configuration is written atomically to `~/.bcn-bridge/bridge.toml` (0600), or to
  `--config` / `BRIDGE_CONFIG`. Gateway webhook tokens are never written to it;
  `start` reads them from `BCS_BRIDGE_PROVIDER_TOKENS` (a JSON object of Provider
  ID to Bearer token).
- One instance runs per user: `start`, `status` and `stop` share
  `~/.bcn-bridge/bridge.lock` and a private control socket.

Configuration written by `register` (gateway example):

```toml
provider_id = "prv_example"
mode = "gateway"
listen = "0.0.0.0:8322"
state_path = "bridge-state.sqlite3"

[[bot]]
provider_bot_ref = "my-bot"
bot_id = "bot_..."
token = "..."
engine = "claude-code"
engine_bin = "/usr/local/bin/claude"
cwd = "/home/me/workspace"
# model = "..."
# permission_mode = "..."      # engine-specific
# engine_options = { ... }     # engine-specific; claude-code/codex accept none

[registration]
api_url = "https://bcs.example.com/prefix"
```

## Extending: plugins

For `--engine qwenwork`, no executable or Python service is needed. Configure
the running desktop's channel and Hooks and add its `engine_options` as shown
in [the QwenWork engine guide](../qwenwork/README.md). Workspace, model and
permissions are owned by QwenWork; its desktop protocol/schema must be verified
before switching an existing Bot.

A distribution is a small binary that assembles the application with its own
plugins. Registering an id that already exists replaces the default.

```rust
use bcs_bridge_app::{BridgeApp, CliDefaults};

fn main() -> std::process::ExitCode {
    BridgeApp::builder()                  // open-source defaults
        .engine(MyEngineFactory)          // add or replace engines
        .profile(MyProfile)               // endpoint defaults
        .credentials(MyCredentials)       // gateway webhook credentials
        .identity(MyIdentity)             // Bot identity at registration
        .defaults(CliDefaults { about: "...", engine: "my-engine", model: None, provider_auth: "my-auth" })
        .build()
        .main()
}
```

| Interface | Crate | Default | Purpose |
| --- | --- | --- | --- |
| `engine::EngineFactory` | core | `claude-code`, `codex` | Create the engine for `engine = "<id>"`; validate `engine_options` |
| `profile::DeployProfile` | app | `ExplicitProfile` (no built-in endpoints) | Default registration API and Bot WebSocket endpoints |
| `credentials::CredentialSource` | app | `EnvCredentials` | Webhook Bearer token per Provider (gateway mode) |
| `identity::IdentityResolver` | app | `StaticBearer` | `provider_bot_ref` for `--provider-auth <id>`, with an optional preflight |

The built-in drivers can be reused for launchers that speak the same protocol:
`ClaudeCodeFactory::custom(id, default_bin, prefix_args)` and
`CodexAppServerFactory::custom(...)` insert `prefix_args` before the protocol
arguments.

### Engine contract

`Engine::run_turn` drives one turn and must:

- validate an engine-native session id with `is_valid_engine_session_id`, then
  await `SessionObserver::established` before continuing, so a failed turn can
  still be resumed;
- emit only engine-neutral events (`sse::chat_delta`, `agent_thinking`,
  `agent_tool`, `interaction_event`); terminal frames are derived from the
  returned `TurnOutcome` / `TurnError`;
- stop the engine and return `TurnError::Aborted` when `abort` fires or the
  event channel closes;
- optionally implement HITL by registering pending decisions with
  `TurnRequest::interactions` and awaiting their resolution.

`engine::cli::CliSession` (subprocess with line-oriented stdio) and
`engine::trace` are available to engine implementations.

`EngineFactory::requires_bin()` defaults to true. Adapters to an already-running
app return false: registration skips PATH lookup and omits `engine_bin`, and
the registry passes an empty build path. Explicit `engine_bin` is rejected for
such engines. Existing factories and configurations keep their behavior.

### Known limitations

- The Codex driver runs threads with `approvalPolicy: never`, a read-only sandbox
  and no network access, and rejects app-server requests instead of routing them
  to HITL interactions. Codex can read and explain code but cannot modify files.
- The engine id is stored with each persisted session: changing a Bot's engine
  starts new engine sessions.

## Development

```bash
# From the repository root (scope to the bridge crates; the full BCS
# workspace test run also covers them).
cargo test --locked --manifest-path apps/bcs/Cargo.toml \
  -p bcs-bridge-cli -p bcs-bridge-app -p bcs-bridge-core

# Local simulator: plays the BCS side against the bridge with mock engines.
cd apps/bcs/crates/tools/bcs-bridge/cli
python3 scripts/bcn_sim.py serve
python3 scripts/bcn_sim.py send "hello"      # in another terminal
```

Tests use mock engines, temporary directories and loopback listeners; no real
engine or BCS service is required. The two `engine::cli` pipe-capacity tests need
a kernel that permits `F_SETPIPE_SZ` of at least 8 KiB.
