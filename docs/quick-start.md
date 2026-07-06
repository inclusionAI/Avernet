# Quick Start: BCS + OpenClaw Integration

[简体中文](quick-start.zh-CN.md)

This guide explains how to run Avernet's BCS, local 5-bot stack, and frontend
workbench on your machine. The recommended entry point is
`scripts/singlebox.sh`; the old `scripts/standalone.sh` is kept only as a
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
| `./scripts/singlebox.sh --local` | Daily local developers | Starts BCS, the local 5-bot stack, and the frontend using the default local paths. |
| `./scripts/singlebox.sh --standalone` | Users who want an isolated trial run | Uses a repo-local isolated BCS runtime and OpenClaw root to avoid touching the default local OpenClaw configuration. |
| `./scripts/singlebox.sh install-tools` | Users who want script-assisted dependency installation | Interactively checks and installs missing tools, explaining write paths and impact before it writes. |
| `./scripts/singlebox.sh check` | Users who only want a preflight | Checks dependencies, directories, and ports; except for initializing a few local runtime directories, it does not install, build, start, or stop processes. |

The current `all` group defaults to BCS + frontend. When BCS starts, it brings
up 5 local OpenClaw bots and connects them to BCS through the BCN plugin.

## Difference between `--local` and `--standalone`

Both modes start the same components: BCS, the local 5-bot stack, and the
frontend workbench. The main differences are runtime directories and OpenClaw
isolation.

| Dimension | `--local` | `--standalone` |
| --- | --- | --- |
| Best for | Daily development and integration | Isolated trial runs that should not affect the default local OpenClaw configuration |
| Default components | BCS + 5 bots + frontend | BCS + 5 bots + frontend |
| BCS runtime | `scripts/.dependencies/bcs_data`, `scripts/.dependencies/bcs-config` | `scripts/.dependencies/standalone/bcs_data`, `scripts/.dependencies/standalone/bcs-config` |
| 5-bot profile | `$HOME/.openclaw-<bot-profile>` | `.standalone-openclaw/profiles/<bot-profile>` |
| 5-bot workspace | `src/bcs/bcs_bots_test_dir/<bot-profile-source>/workspace` | `.standalone-openclaw/workspaces/<bot-profile>` |
| BCN plugin link | `$HOME/.openclaw/extensions/openclaw-channel-bcn` | `.standalone-openclaw/extensions/openclaw-channel-bcn` |
| Main logs | `scripts/.dependencies/logs/` and `src/bcs/bcs_bots_test_dir/logs/` | `scripts/.dependencies/logs/`, `scripts/.dependencies/standalone/`, and `.standalone-openclaw/logs/` |

For the default local 5-bot stack, `<bot-profile-source>` is one of
`ceo`, `product-manager`, `engineering`, `verification`, or
`customer-service`.

The current flow does not make `--local` and `--standalone` two environments
that can run at the same time. By default, both reuse ports such as `21000`,
`8000`, and `30001` through `30041`.

## Shortest path

If you want the script to check and install missing tools:

```bash
./scripts/singlebox.sh install-tools
./scripts/singlebox.sh --local
```

If you only want to preflight dependencies and ports, then decide how to install
missing tools yourself:

```bash
./scripts/singlebox.sh check
```

After the preflight passes, start the default local path:

```bash
./scripts/singlebox.sh --local
```

If you want to use the repo-local isolated BCS runtime and OpenClaw root:

```bash
./scripts/singlebox.sh --standalone check
./scripts/singlebox.sh --standalone
```

Frontend URL:

```text
http://127.0.0.1:8000/
```

If `FRONTEND_PORT` is set in `.env.local`, or if startup uses
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
./scripts/singlebox.sh install-tools
```

`check` is the preflight command. It only checks dependencies, directories, and
ports; except for initializing a few local runtime directories, it does not
install, build, start, or stop processes:

```bash
./scripts/singlebox.sh check
```

If you want to manage dependency versions completely by hand, follow
[dependencies.md](dependencies.md) to install Rust 1.91+, Cargo, `protoc`,
Node.js 22+, npm, and OpenClaw.

For mainland China network acceleration, set this in your local `.env.local` or
current shell:

```bash
export USE_CN_MIRROR=1
```

This variable makes scripts prefer public mirror sources. Without it, scripts
use the default public sources.

## Model configuration

The local 5-bot stack first tries to copy model-related fields from the local
OpenClaw configuration into isolated profiles. The default source is:

```text
$HOME/.openclaw/openclaw.json
```

If you do not want to read the default OpenClaw configuration, explicitly set a
read-only source:

```bash
export OPENCLAW_MODEL_CONFIG_SOURCE=/path/to/openclaw.json
```

You can also explicitly pass OpenAI-compatible model settings:

```bash
export OPENCLAW_OPENAI_BASE_URL=<model-api-base-url>
export OPENCLAW_OPENAI_API_KEY=<model-api-key>
export OPENCLAW_OPENAI_MODEL_ID=<model-id>
```

Do not write API keys into repository files, and do not commit locally generated
`openclaw.json`, logs, or runtime data.

## Verification after startup

Read the local BCS port. Without `.env.local`, the default is `21000`:

```bash
if [ -f .env.local ]; then
  set -a
  . ./.env.local
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
./src/bcs/target/debug/bcs-cli --url "${BCS_HTTP_URL}" list
```

After success, you should see:

- `/health` returns 200.
- `bcs-cli list` prints `Bots in network (...)`.
- The list includes the 5 local bots: CEO, 产品经理, 研发, 验证, and 客服.
- The frontend is reachable at `http://127.0.0.1:8000/`.

Check overall status:

```bash
./scripts/singlebox.sh status
```

Check the standalone isolated path status:

```bash
./scripts/singlebox.sh --standalone status
```

## Common operations

Stop the default local path:

```bash
./scripts/singlebox.sh stop
```

Stop the standalone isolated path:

```bash
./scripts/singlebox.sh --standalone stop
```

Restart:

```bash
./scripts/singlebox.sh restart
```

Clean intermediate BCS state for the local path:

```bash
./scripts/singlebox.sh clean bcs
```

Clean intermediate BCS state for the standalone isolated path:

```bash
./scripts/singlebox.sh --standalone clean bcs
```

`clean bcs` first stops BCS and the local 5-bot stack, then removes the BCS
sqlite data, generated configuration, PID files, and this repository's BCN
plugin symlink for the selected mode. Normal `start` / `restart` does not clean
`bcs.db*` or bot workspaces by default.

## Troubleshooting

### 1. BCS did not start

Start with the logs for the current mode:

```bash
tail -n 100 scripts/.dependencies/logs/bcs_bots_stack.log
tail -n 100 src/bcs/bcs_bots_test_dir/logs/bcs.log
```

For standalone mode, also check:

```bash
tail -n 100 scripts/.dependencies/standalone/bcs_bots_stack.log
tail -n 100 .standalone-openclaw/logs/bcs.log
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
test -f src/plugin/packages/openclaw-channel-bcn/dist/esm/index.js
test -L "$HOME/.openclaw/extensions/openclaw-channel-bcn"
```

For standalone mode, check:

```bash
test -L .standalone-openclaw/extensions/openclaw-channel-bcn
```

If the plugin build output does not exist, rerun:

```bash
./scripts/singlebox.sh setup bcs
```

Or for standalone:

```bash
./scripts/singlebox.sh --standalone setup bcs
```

### 3. Bots did not all connect

Check the 5-bot stack log first, then check whether `.bcs/session.json` exists
under the corresponding profile.

Local mode:

```bash
tail -n 100 scripts/.dependencies/logs/bcs_bots_stack.log
test -f "$HOME/.openclaw-ceo/.bcs/session.json"
```

Standalone mode:

```bash
tail -n 100 scripts/.dependencies/standalone/bcs_bots_stack.log
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

If the BCS or frontend port is occupied, set these values in `.env.local`:

```bash
BCS_PORT=<available-bcs-port>
FRONTEND_PORT=<available-frontend-port>
```

You can also pass them explicitly at startup:

```bash
./scripts/singlebox.sh --local --bcs-port <available-bcs-port> --frontend-port <available-frontend-port>
./scripts/singlebox.sh --standalone --bcs-port <available-bcs-port> --frontend-port <available-frontend-port>
```

The current default does not support running `--local` and `--standalone` at
the same time. If you just ran the other mode, stop that mode first.

## This is not a production deployment guide

This guide is the shortest path for individual developers to run through BCS +
OpenClaw integration.

It starts BCS in debug mode, uses mock authentication, and generates local
runtime configuration from `src/bcs/configs/bcs-config-local.toml`. It is
suitable for first-run validation and local integration, not as a production
deployment reference. Wait for the official deployment documentation for
production deployment.
