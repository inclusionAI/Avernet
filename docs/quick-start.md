# Quick Start: BCS + OpenClaw Integration

[简体中文](quick-start.zh-CN.md)

This guide explains how to run Avernet's BCS, local 5-bot stack, and frontend
workbench on your machine. The recommended entry point is
`singlebox/singlebox.sh`; the old `singlebox/standalone.sh` is kept only as a
compatibility wrapper and is no longer the main path documented here.

If you want to skip local dependency installation and run the same path in a
container, see [docker.md](docker.md). If you only want the tool dependency
list, see [dependencies.md](dependencies.md).

## How to read this guide

If this is your first time with Avernet, start with [README.md](../README.md).
The README explains what Avernet is, what it can do, and which startup paths
are available. This Quick Start expands the local startup path into commands
you can run.

Common entry points:

| Entry | Best for | Purpose |
| --- | --- | --- |
| [README.md](../README.md) | First-time readers | Product positioning, capability status, recommended startup paths, and documentation navigation. |
| `./singlebox/singlebox.sh` | Daily local developers and first-time users | Starts BAAS, backend, BCS, the local 5-bot stack, demo bot, and frontend using repo-local isolated runtime paths. |
| `./singlebox/singlebox.sh --standalone` | Compatibility with older docs or scripts | Explicit alias for the default isolated singlebox mode. |
| `./singlebox/singlebox.sh install-tools` | Users who want script-assisted dependency installation | Interactively checks and installs missing tools, explaining write paths and impact before it writes. |
| `./singlebox/singlebox.sh check` | Users who only want a preflight | Checks dependencies, directories, and ports; except for initializing a few local runtime directories, it does not install, build, start, or stop processes. |

The current `all` group starts BAAS, backend, BCSFuse, BCS, the local 5-bot
stack, demo bot, and frontend. `start bcs` starts only BCS; use `start bcs_bots`
for BCS plus the 5 demo bots. `bcs_frontend` serves the default `legacy`
frontend; for other frontend variants, see [the frontend guide](singlebox-nextgen-local.md).

## Runtime isolation

`singlebox.sh` has one supported mode: the isolated singlebox mode. It avoids
writing the 5-bot profiles, workspaces, and plugin link into the default
OpenClaw home directory.

| Dimension | Path |
| --- | --- |
| BCS runtime | `singlebox/.dependencies/standalone/bcs_data`, `singlebox/.dependencies/standalone/bcs-config` |
| 5-bot profile | `.standalone-openclaw/profiles/<bot-profile>` |
| 5-bot workspace | `.standalone-openclaw/workspaces/<bot-profile>` |
| BCN plugin link | `.standalone-openclaw/extensions/openclaw-channel-bcn` |
| Main logs | `singlebox/.dependencies/logs/`, `singlebox/.dependencies/standalone/`, and `.standalone-openclaw/logs/` |

For the default local 5-bot stack, `<bot-profile-source>` is one of
`ceo`, `product-manager`, `engineering`, `verification`, or
`customer-service`.

Only one singlebox stack should listen on the default ports at a time:
`21000`, `8000`, and `30001` through `30041`.

## Optional local configuration

All commands in this guide run from the repository root. Copy the template only
when you need local overrides:

```bash
test -f singlebox/.env.local || cp singlebox/.env.example singlebox/.env.local
# Edit singlebox/.env.local; do not commit real credentials.
```

Singlebox's main entry point automatically loads `singlebox/.env.local`. The
repository-root `.env.local` is not the native configuration file used by this
guide. Command-line port options override the values loaded from this file.
`--local` has been removed; isolated runtime paths are now the default.

## Shortest path

If you want the script to check and install missing tools:

```bash
./singlebox/singlebox.sh install-tools
./singlebox/singlebox.sh
```

If you only want to preflight dependencies and ports, then decide how to install
missing tools yourself:

```bash
./singlebox/singlebox.sh check
```

After the preflight passes, start the default isolated path:

```bash
./singlebox/singlebox.sh
```

Frontend URL:

```text
http://127.0.0.1:8000/
```

If `FRONTEND_PORT` is set in `singlebox/.env.local`, or if startup uses
`--frontend-port/-fp`, open the corresponding port instead.

Default BCS URL:

```text
http://127.0.0.1:21000/
```

## Installing tools and running preflight

`install-tools` is an interactive installation guide. It may install Node.js,
uv, OpenClaw, Rust/Cargo, and protobuf/protoc, and may write to the user
directory or call the local package manager. It asks for confirmation before
installing OpenClaw, Rust/Cargo, and protobuf/protoc.

```bash
./singlebox/singlebox.sh install-tools
```

Running `singlebox.sh` also installs the repo-local pre-push hook by setting
`core.hooksPath=.githooks`. Set `OCB_SKIP_GIT_HOOKS=1` if you need to skip hook
installation for a one-off command.

`check` is the preflight command. It only checks dependencies, directories, and
ports; except for initializing a few local runtime directories, it does not
install, build, start, or stop processes:

```bash
./singlebox/singlebox.sh check
```

If you want to manage dependency versions completely by hand, follow
[dependencies.md](dependencies.md) to install Rust 1.91+, Cargo, `protoc`,
Node.js 22+, npm, and OpenClaw.

For mainland China network acceleration, set this in your local `singlebox/.env.local` or
current shell:

```bash
export USE_CN_MIRROR=1
```

This variable makes scripts prefer public mirror sources. Without it, scripts
use the default public sources.

## Model configuration

When starting bots or the full stack, Singlebox asks you to choose a model
configuration mode. Set `SINGLEBOX_MODEL_CONFIG_MODE` in `singlebox/.env.local`
to skip that menu:

| Mode | Behavior |
| --- | --- |
| `mock` | Uses fixed-format replies from a local mock model server; no real API key is needed. |
| `manual` | Requires all three `OPENCLAW_OPENAI_*` values below. Missing values cause an error, not a fallback. |
| `home` | Imports model fields from `~/.openclaw/openclaw.json` after confirmation. |

For real replies in `manual` mode, uncomment and replace these values in
`singlebox/.env.local`:

```dotenv
SINGLEBOX_MODEL_CONFIG_MODE=manual
OPENCLAW_OPENAI_BASE_URL=https://your-model-service.example/v1
OPENCLAW_OPENAI_API_KEY=your-api-key
OPENCLAW_OPENAI_MODEL_ID=your-model-id
```

For `home`, `OPENCLAW_MODEL_CONFIG_SOURCE` can select another read-only JSON
source. Unattended imports require `SINGLEBOX_MODEL_CONFIG_HOME_CONFIRMED=1`.
The source file is not modified. Noninteractive startup without an explicit
mode uses `mock`; it does not automatically import your home configuration.

Starting only BCS/frontend does not show this menu. Real BCS judge calls require
complete model settings in the launch environment. Never commit API keys,
generated `openclaw.json`, logs, or runtime data.

## Verification after startup

Read the local BCS port. Without `singlebox/.env.local`, the default is `21000`:

```bash
if [ -f singlebox/.env.local ]; then
  set -a
  . ./singlebox/.env.local
  set +a
fi
BCS_PORT="${BCS_PORT:-21000}"
BCS_HTTP_URL="${BCS_HTTP_URL:-http://127.0.0.1:${BCS_PORT}}"
```

Confirm that the BCS health check passes:

```bash
curl --noproxy '*' "${BCS_HTTP_URL}/health"
```

List connected bots:

```bash
./apps/bcs/target/debug/bcs-cli --url "${BCS_HTTP_URL}" list
```

After success, you should see:

- `/health` returns 200.
- `bcs-cli list` prints `Bots in network (...)`.
- The list includes the 5 local bots: CEO, 产品经理, 研发, 验证, and 客服.
- The frontend is reachable at `http://127.0.0.1:8000/`.

Check overall status:

```bash
./singlebox/singlebox.sh status
```

Check the isolated path status:

```bash
./singlebox/singlebox.sh status
```

## Common operations

Stop the default isolated path:

```bash
./singlebox/singlebox.sh stop
```

Restart:

```bash
./singlebox/singlebox.sh restart
```

Clean intermediate BCS state:

```bash
./singlebox/singlebox.sh clean bcs
```

`clean bcs` stops only BCS and removes its SQLite database and generated runtime
configuration. It does not stop bots or remove their identities, workspaces, or
plugin links. Stop bots separately first if needed. Normal `start` / `restart`
preserves `bcs.db*` and bot workspaces.

## Troubleshooting

### 1. BCS did not start

Start with the isolated stack logs:

```bash
tail -n 100 singlebox/.dependencies/standalone/bcs_bots_stack.log
tail -n 100 singlebox/.dependencies/logs/bcs.log
```

Common causes:

- Rust/Cargo or `protoc` is not installed.
- The BCS binary did not build successfully.
- The default `21000` port, or the port you set through `BCS_PORT`, is already
  occupied by another process.
- Model configuration is unavailable, so the 5 OpenClaw bots did not finish
  startup.

### 2. BCN plugin is not active

Check the plugin build output and symlink:

```bash
test -f apps/bcs/crates/plugins/openclaw-channel-bcn/dist/esm/index.js
test -L .standalone-openclaw/extensions/openclaw-channel-bcn
```

If the plugin build output does not exist, rerun:

```bash
./singlebox/singlebox.sh setup bots
```

### 3. Bots did not all connect

Check the 5-bot stack log first, then check whether `.bcs/session.json` exists
under the corresponding profile.

Check the isolated stack:

```bash
tail -n 100 singlebox/.dependencies/standalone/bcs_bots_stack.log
test -f .standalone-openclaw/profiles/ceo/.bcs/session.json
```

### 4. A port is occupied

Default ports:

- BCS: `21000`
- frontend: `8000`
- 5 bots: `30001`, `30011`, `30021`, `30031`, `30041`

Check a port:

```bash
BCS_PORT="${BCS_PORT:-21000}"
FRONTEND_PORT="${FRONTEND_PORT:-8000}"
lsof -nP -iTCP:"${BCS_PORT}" -sTCP:LISTEN
lsof -nP -iTCP:"${FRONTEND_PORT}" -sTCP:LISTEN
```

If the BCS or frontend port is occupied, set these values in `singlebox/.env.local`:

```bash
BCS_PORT=<available-bcs-port>
FRONTEND_PORT=<available-frontend-port>
```

You can also pass them explicitly at startup:

```bash
./singlebox/singlebox.sh --bcs-port <available-bcs-port> --frontend-port <available-frontend-port>
```

The default ports cannot be shared by two singlebox stacks. If another checkout
is already running, stop that stack first or choose different ports.

## This is not a production deployment guide

This guide is the shortest path for individual developers to run through BCS +
OpenClaw integration.

It starts BCS in debug mode, uses mock authentication, and generates local
runtime configuration from `apps/bcs/configs/bcs-config-local.toml`. It is
suitable for first-run validation and local integration, not as a production
deployment reference. Wait for the official deployment documentation for
production deployment.
