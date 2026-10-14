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

Distributions can call `.registration_permission_mode("my-engine", "<mode>")`
on the application builder to write `permission_mode` into newly registered
Bots using that engine. Engines without an entry omit this field; existing
runtime configurations are unaffected. The open-source defaults have no entries.

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

### Distribution identity discovery

`IdentityResolver::registration_ref` receives the selected engine, canonical workspace
and optional `--provider-bot-ref`. The default preserves the explicit reference;
distributions can discover an existing local identity. Resolved references are
validated before `registration_client` prepares authentication for both identity
lookup and registration. `RegistrationClient::with_headers` applies the prepared
headers to both requests, with redirects disabled. Credentials stay in the
short-lived client and are not added to saved bridge configuration. Identity
verification remains the resolver's responsibility; discovery alone is not proof
of identity. Company-specific SDK implementations belong in their distribution.


## Named connection profiles

Use `--profile <name>` consistently with `register`, `init`, `deinit` and `start`.
Profiles keep configurations and relative state databases in separate
`~/.bcn-bridge/profiles/<name>/` directories. A profile name selects local
configuration, not an implicit service URL; supply the intended registration
API and upstream endpoints when registering. Names accept 1-64 ASCII letters,
digits, hyphens or underscores. `--config` remains supported but cannot be
combined with `--profile`.

Repeated registration of an existing profile verifies provider, engine and
endpoints and reuses the stored Bot without a network registration request.
Explicitly conflicting workspace/model options are rejected. New profiles may
reuse local engine options from a verified active instance of the same engine.
When QwenWork is stopped, saved regular TOML configs directly in ~/.bcn-bridge
and named profiles are searched (bounded to 128 entries per directory, excluding
backups and symlinks). Identical desktop settings are reused; conflicting
settings block registration and require starting the intended existing profile.
Only desktop settings cross environments,
without sharing its BCN identity or durable state. One user still runs one bridge:
stop the current instance before starting another profile. Status prints the
active profile; this is not a remote connectivity claim.

For intentional replacement, stop the bridge and use `register --replace-existing`.
The CLI fences concurrent startup, retains a private rollback configuration
beside the original. For the same Provider, API endpoint, mode, engine and Bot
reference (omitted reference keeps the saved one), local settings are updated
without registration; Bot ID, credentials, engine options and state are retained.
Changing the Provider, API endpoint or explicit Bot reference performs new
registration and selects fresh state. Changing mode or engine for the same Bot
is rejected. Failures leave the original local config intact.

Gateway webhook changes use the existing authenticated Provider PATCH API.
Supply `BRIDGE_PROVIDER_ADMIN_TOKEN` or `--provider-admin-token`; a registration
token or Bot token cannot authorize that change. This credential is never saved
in the configuration. Legacy gateway configurations without webhook metadata
also require this authenticated update to verify the endpoint. If a network or
response error leaves the remote update uncertain, verify its endpoint before
restarting; the original local configuration remains available.

Profile reuse also verifies an explicitly supplied `--provider-bot-ref`. Omit it
to retain the saved identity (plugin mode generates a reference on first registration).
Gateway registration saves `registration.webhook_url`; reuse rejects a changed
delivery URL, including its path or query. Legacy gateway configurations without
this metadata cannot be verified and require stopped, explicit replacement.

Explicit `--engine-bin` and gateway `--listen` values must also match the saved
profile. Omitting these options preserves the saved runtime settings, even when
they differ from CLI defaults. Executable paths retain symlink identity because
wrapper behavior can depend on the invoked name. Use explicit replacement to
change an executable or bind address.

Gateway replacement may omit `--webhook-url` when saved metadata belongs to the
same Provider, API endpoint, reference, engine and gateway mode. Local listener
updates retain that endpoint without administrator authentication or network calls.
New bindings still require an explicit webhook URL.
