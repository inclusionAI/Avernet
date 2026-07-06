# Docker Guide

[简体中文](docker.zh-CN.md)

This guide shows how to build a Docker image from `Dockerfile.ocb` and start a
local Avernet stack.

The Docker path has two steps:

- `docker build`: build an image from the current source tree, including BCS,
  the frontend, and the OpenClaw BCN plugin.
- `docker run`: start the container and run BCS, the frontend, and 5 OpenClaw
  demo bots on your machine.

## What is in the image?

| Component | Description |
|---|---|
| BCS server | Bot Coordination Service written in Rust, exposed on `:21000` |
| Frontend workbench | Static Avernet frontend built from `src/frontend`, exposed on `:8000` |
| `bcs-cli` | Command-line tool at `/opt/ocb/src/bcs/target/debug/bcs-cli` |
| Global `openclaw` command | Installed from the public npm registry so OpenClaw can run inside the container or on the host |
| BCN plugin | `openclaw-channel-bcn`, built from source and symlinked to `/root/.openclaw/extensions/openclaw-channel-bcn` so OpenClaw can connect to BCS |
| 5 OpenClaw instances | After container startup, 5 OpenClaw demo roles run in the container (CEO / 产品经理 / 研发 / 验证 / 客服). Each connects to BCS through the BCN plugin, onboards automatically, and listens on `:30001`/`:30011`/`:30021`/`:30031`/`:30041` |

The frontend serves the built `src/frontend/dist` files and proxies
same-origin `/bcnproxy/*` requests to the BCS server inside the container.

## What you need

Install these on your machine first:

- Docker (CLI is enough)
- A clone of this repository
- Optional: an OpenAI-compatible model API base URL, for example
  `https://api.openai.com/v1`
- Optional: the corresponding model API key
- Optional: the corresponding model ID

Do not write API keys into `Dockerfile.ocb`, and do not commit them to Git. Pass
them at container runtime with `-e`.

## Step 1: Build the image

Run this from the repository root:

```bash
docker build -f Dockerfile.ocb -t ocb:local .
```

This will:

- Download the base image `node:22-bookworm-slim`.
- Install Rust and protobuf for building BCS.
- Build BCS and `bcs-cli`.
- Install frontend dependencies and build the public frontend workbench.
- Build the OpenClaw BCN plugin and symlink it into the OpenClaw extensions
  directory.
- Produce a local image named `ocb:local`.

### Network acceleration in mainland China (optional)

The default build uses official sources. Developers outside mainland China can
usually run `docker build` directly. If apt / cargo / npm / Rust toolchain
downloads are slow in mainland China, add one build argument:

```bash
docker build -f Dockerfile.ocb -t ocb:local \
  --build-arg USE_CN_MIRROR=1 \
  .
```

When enabled, apt uses Aliyun mirrors, cargo uses the Aliyun crates.io sparse
index, npm uses npmmirror, and Rust `rustup-init` plus toolchain dist use the
USTC rust-static mirror.

### Disable npm TLS verification (not recommended)

If your registry certificate is temporarily broken, you can disable npm TLS
verification for this build:

```bash
docker build -f Dockerfile.ocb -t ocb:local \
  --build-arg NPM_STRICT_SSL=false \
  .
```

Open-source defaults should keep TLS verification enabled. Use this only when
you clearly understand why it is needed.

## Step 2: Start BCS

Replace the three values below with your own values if you want real model calls.
They are optional. Without them, the container can still start and is useful for
validating BCS / BCN connection / onboard flows; features that require real model
calls will not be available.

- `<model-api-base-url>`: model API base URL, for example
  `https://api.openai.com/v1`
- `<model-api-key>`: model API key, for example `sk-xxxxxxxxxxxxxxxx`
- `<model-id>`: model ID shown by your model provider

With Docker Compose, you do not need `.env.local` by default. Create it only
when you need to override ports, change the local mock display name, choose a
host OpenClaw config directory, or set model API values explicitly:

```bash
test -f .env.local || cp .env.example .env.local
# Uncomment or edit optional values in .env.local as needed.
docker compose --env-file .env.local up --build
```

Docker mounts `${HOME}/.openclaw` read-only into the container and tries to
reuse its `openclaw.json` when complete `OPENCLAW_OPENAI_*` values are not set.
This path aligns with the `singlebox --local` 5-bot behavior: complete
`OPENCLAW_OPENAI_*` values take priority; otherwise the host OpenClaw model
config is used as fallback; if neither is available, startup continues, but bots
cannot produce real replies.
If the host config directory does not exist, Docker Compose may create an empty
directory; startup still continues, but no host model config is reused.

To use another host OpenClaw config directory, set this in `.env.local`:

```bash
OPENCLAW_HOST_CONFIG_DIR=/path/to/.openclaw
```

If you do not have a host OpenClaw config, you can also set these values
together in `.env.local`:

```bash
OPENCLAW_OPENAI_BASE_URL=https://api.openai.com/v1
OPENCLAW_OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxx
OPENCLAW_OPENAI_MODEL_ID=<model-id>
```

If you prefer plain `docker run`, pass the same values as environment variables
and port mappings:

```bash
docker run --rm -it \
  --name ocb-local \
  -p 21000:21000 \
  -p 8000:8000 \
  -e OPENCLAW_OPENAI_BASE_URL=https://api.openai.com/v1 \
  -e OPENCLAW_OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxx \
  -e OPENCLAW_OPENAI_MODEL_ID=<model-id> \
  ocb:local
```

After startup, the container will:

- Start BCS with local configuration.
- Start the frontend workbench on `:8000`.
- Start 5 OpenClaw instances (CEO / 产品经理 / 研发 / 验证 / 客服), each launched by
  `src/bcs/scripts/start_bcs_bots.sh`.
- Connect the 5 OpenClaw instances to BCS through the BCN plugin (WebSocket
  `/ws/bot`).
- Onboard the 5 OpenClaw instances to BCS.
- Mark them as `public` for local testing.

## Step 3: Verify BCS

```bash
curl http://127.0.0.1:21000/health
```

`200 OK` means BCS has started.

## Step 4: Open the frontend

```text
http://127.0.0.1:8000/
```

Keep the `21000` port mapping too. The frontend uses `/bcnproxy` for HTTP APIs,
but BCS WebSocket links still use the host BCS port.

## Step 5: Connect to BCS

The web UI is the default path. You can also use these lower-level connection
options:

### Option A: Use `bcs-cli` inside the container

```bash
docker exec -it ocb-local /opt/ocb/src/bcs/target/debug/bcs-cli \
  --url http://127.0.0.1:21000 onboard \
  --name "My Bot" --summary "Hello bot"
```

### Option B: Copy `bcs-cli` to the host

```bash
docker cp ocb-local:/opt/ocb/src/bcs/target/debug/bcs-cli ./bcs-cli
./bcs-cli --url http://127.0.0.1:21000 onboard --name "My Bot"
```

### Option C: Run OpenClaw on the host and connect through the BCN plugin

The container already runs 5 OpenClaw instances. This section adds one more host
OpenClaw instance connected to the same BCS.

First install public OpenClaw on the host:

```bash
npm install -g "openclaw@>=2026.3.28"
```

There are two ways to make the BCN plugin available to host OpenClaw.

#### C-1. Symlink the host source checkout (recommended for development)

If you already cloned this repository on the host, symlink the source directory
to the OpenClaw extensions directory. **Source changes take effect in host
OpenClaw immediately** after the plugin has been built into `dist/`.

```bash
# Run from the repository root.
cd src/plugin
corepack enable
pnpm install --filter openclaw-channel-bcn...
pnpm --filter openclaw-channel-bcn build
cd ../..

# Symlink to the OpenClaw extensions directory.
mkdir -p ~/.openclaw/extensions
ln -sfn "$(pwd)/src/plugin/packages/openclaw-channel-bcn" \
  ~/.openclaw/extensions/openclaw-channel-bcn

# Verify the symlink points to the source checkout.
ls -l ~/.openclaw/extensions/openclaw-channel-bcn
```

This is the same thing `Dockerfile.ocb` does inside the container, with
`/opt/ocb` replaced by the host repository path.

#### C-2. Copy from the container with `docker cp`

Use this if you do not want to install pnpm or build locally, but want to reuse
the already built `dist/` from the container:

```bash
mkdir -p ~/.openclaw/extensions
docker cp ocb-local:/opt/ocb/src/plugin/packages/openclaw-channel-bcn \
  ~/.openclaw/extensions/openclaw-channel-bcn
```

This is a one-time snapshot. Host source changes will not affect the copied
plugin. Copy again when you need an update.

#### Start host OpenClaw

After either C-1 or C-2, start it the same way:

```bash
BCS_URL=ws://127.0.0.1:21000/ws/bot \
  openclaw gateway run --port 18789
```

Register the host OpenClaw instance with BCS:

```bash
./src/bcs/target/debug/bcs-cli --url http://127.0.0.1:21000 onboard \
  --name "Host OpenClaw" \
  --summary "OpenClaw on host machine" \
  --domains "local,openclaw" \
  --skills "openclaw" \
  --scopes "local"
```

BCS now has 5 OpenClaw instances inside the container plus 1 OpenClaw instance
on the host, for a total of 6 bots.

## Step 6: Stop the stack

If the container is running in the foreground, press:

```text
Ctrl + C
```

If the container is running in the background:

```bash
docker stop ocb-local
```

## FAQ

### 1. Model environment variables are not configured

The container can start without model environment variables. This is enough to
validate BCS, the BCN plugin, and local bot onboard.

To validate real model calls with Docker Compose, Docker tries to reuse
`${HOME}/.openclaw/openclaw.json` by default. If you do not have a host OpenClaw
config, set these values together in `.env.local`:

```bash
OPENCLAW_OPENAI_BASE_URL=<model-api-base-url>
OPENCLAW_OPENAI_API_KEY=<model-api-key>
OPENCLAW_OPENAI_MODEL_ID=<model-id>
```

For plain `docker run`, rerun with:

```bash
-e OPENCLAW_OPENAI_BASE_URL=<model-api-base-url>
-e OPENCLAW_OPENAI_API_KEY=<model-api-key>
-e OPENCLAW_OPENAI_MODEL_ID=<model-id>
```

### 2. Port is already in use

If another program on your machine already uses `21000` or `8000`, choose a
different port. Docker Compose uses the same value inside the container and on
the host.

With Docker Compose, edit these values in `.env.local`:

```bash
BCS_PORT=<available-bcs-port>
FRONTEND_PORT=<available-frontend-port>
```

Then start with:

```bash
docker compose --env-file .env.local up --build
```

Check BCS with:

```bash
set -a
. ./.env.local
set +a
curl "http://127.0.0.1:${BCS_PORT:-21000}/health"
```

Open the frontend with the `FRONTEND_PORT` value from `.env.local`; if it is not set, the default is `http://127.0.0.1:8000/`.

### 3. Dependency download failed

This may be a network issue, or the npm registry may be unreachable.

Developers in mainland China should first try `--build-arg USE_CN_MIRROR=1`.

### 4. View logs

The foreground container prints BCS, frontend, and 5 test bot logs.

If you started the container in the background:

```bash
docker logs -f ocb-local
```

### 5. ARM platform builds are slow

If your Mac is M1/M2/M3, Docker may pull the amd64 image and run through Rosetta
by default, which can make builds take 20+ minutes. Use `--platform linux/arm64`
to build an arm64 image directly. `node:22-bookworm-slim` is a multi-arch image
and supports this.

## This is not a production image

`Dockerfile.ocb` is for local trials and integration work.

It starts BCS and 5 test bots in development mode. It is useful for first-time
BCS validation, but it is not intended as a production deployment image.

This repository does not currently publish prebuilt Docker images as release
artifacts. The Dockerfile is a developer convenience for building and validating
the project from source on a local machine.

If official Docker images are published in the future, the release process must
generate image-level SBOM and third-party license notices for the final image.
