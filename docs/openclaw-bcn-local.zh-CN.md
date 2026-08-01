# 从源码构建，将本地 OpenClaw 接入 BCN

这份文档说明如何从源代码构建，把本机 OpenClaw 通过 `openclaw-channel-bcn` 插件接入 Avernet的过程。

 `openclaw-channel-bcn` 是 Avernet 的 OpenClaw 插件。它在 OpenClaw 内注册 `bcs` channel，并通过 BCS WebSocket `/ws/bot` 建立长连接。

如果你只是想跑通 Avernet 本地体验，优先使用 [Quick Start](quick-start.zh-CN.md)：

```bash
./scripts/singlebox.sh
```

`singlebox.sh` 会自动构建 BCN 插件、启动 BCS、拉起 5 个本地 OpenClaw demo bot，并完成 onboard。本指南适合以下场景：

- 想把一个额外的本机 OpenClaw profile 接入同一个 BCS。
- 想理解 OpenClaw、BCN 插件、BCS WebSocket 和 `bcs-cli onboard` 之间的关系。
- 想手动调试 `openclaw-channel-bcn` 插件。

## 连接链路

```text
Local OpenClaw gateway
  -> openclaw-channel-bcn plugin
  -> ws://127.0.0.1:<BCS_PORT>/ws/bot
  -> BCS
  -> bcs-cli onboard
  -> bot appears in Avernet network
```

关键点：

- OpenClaw 需要加载 `openclaw-channel-bcn` 插件。
- 插件需要拿到 BCS WebSocket 地址，通常是 `ws://127.0.0.1:21000/ws/bot`。
- 插件连上 BCS 后会生成 `.bcs/session.json`，里面包含 bot token。
- `bcs-cli onboard` 使用这个 token 把 bot 的名称、能力和可见性注册到 BCS。

## 前置条件

先确保 BCS 在本机运行。最简单的方式是直接跑完整 local stack：

```bash
./scripts/singlebox.sh
```

如果你只想启动 BCS，不想同时启动默认 5 个 demo bot，可以先构建 BCS 和插件，再以 bare BCS 模式启动：

```bash
./scripts/singlebox.sh setup bcs
./scripts/singlebox.sh --no-bcs-auto-onboard start bcs
```

确认 BCS 健康检查通过：

```bash
if [ -f .env.local ]; then
  set -a
  . ./.env.local
  set +a
fi

BCS_PORT="${BCS_PORT:-21000}"
curl --noproxy '*' -fsS "http://127.0.0.1:${BCS_PORT}/health"
```

确认本机有 OpenClaw CLI：

```bash
openclaw --version
```

如果没有，请按 [Dependencies](dependencies.zh-CN.md) 安装。

## 方式一：使用 singlebox 自动接入

这是推荐路径。`singlebox.sh` 会自动完成以下动作：

1. 构建 `src/bcs/crates/plugins/openclaw-channel-bcn`。
2. 将插件软链到 OpenClaw extension 目录。
3. 为每个 demo bot 生成 OpenClaw profile。
4. 在 profile 中写入 `channels.bcs.bcsUrl`、bot 信息和插件加载路径。
5. 启动 OpenClaw gateway。
6. 等待插件生成 session token 后执行 `bcs-cli onboard`。

默认隔离模式会写入：

```text
.standalone-openclaw/profiles/<bot-profile>
.standalone-openclaw/extensions/openclaw-channel-bcn
.standalone-openclaw/workspaces/<bot-profile>
```

默认 5bot 本地栈里，`<bot-profile>` 对应 `scripts/5bots_profile/*`
下的人设目录名。

### 选择插件来源（source 还是 npm）

`scripts/singlebox.sh` 加载 `openclaw-channel-bcn` 插件有两种方式：

- **source**（默认）：从仓库内构建 `src/bcs/crates/plugins/openclaw-channel-bcn`。
- **npm**：通过 `openclaw plugins install` 安装 `@avernet-plugin/openclaw-channel-bcn`。

通过 flag 或环境变量选择（flag 优先级更高）：

```bash
# npm 模式
./scripts/singlebox.sh --bcn-plugin-source npm
BCN_PLUGIN_SOURCE=npm ./scripts/singlebox.sh

# 在 npm 模式下指定版本（默认：latest）
BCN_PLUGIN_SOURCE=npm BCN_PLUGIN_VERSION=1.0.15 ./scripts/singlebox.sh
```

## 方式二：手动接入一个本机 OpenClaw profile

下面示例使用仓库内隔离目录 `.openclaw-host-bcn/`，避免直接改写默认 `~/.openclaw/openclaw.json`。

### 1. 构建 BCN 插件

```bash
(
  cd src/bcs/crates/plugins/openclaw-channel-bcn
  npm install
  npm run build
)
```

确认插件产物存在：

```bash
test -f src/bcs/crates/plugins/openclaw-channel-bcn/dist/esm/index.js
```

Dockerfile 里的做法与此等价：在镜像构建阶段执行 `npm install`、`npm run build`，然后把插件目录软链到 OpenClaw extensions。

### 2. 让 OpenClaw 能加载插件

推荐同时做显式加载路径和 extension 软链。显式加载路径写在下面的 `openclaw.json` 中；软链与 Dockerfile 行为保持一致：

```bash
mkdir -p ~/.openclaw/extensions
ln -sfn "$(pwd)/src/bcs/crates/plugins/openclaw-channel-bcn" \
  ~/.openclaw/extensions/openclaw-channel-bcn
```

验证软链：

```bash
ls -l ~/.openclaw/extensions/openclaw-channel-bcn
```

### 3. 生成一个隔离 OpenClaw 配置

```bash
if [ -f .env.local ]; then
  set -a
  . ./.env.local
  set +a
fi

BCS_PORT="${BCS_PORT:-21000}"
OPENCLAW_GATEWAY_PORT="${OPENCLAW_GATEWAY_PORT:-18789}"
HOST_BOT_DIR="$(pwd)/.openclaw-host-bcn"
HOST_BOT_WORKSPACE="${HOST_BOT_DIR}/workspace"
PLUGIN_PATH="$(pwd)/src/bcs/crates/plugins/openclaw-channel-bcn"

mkdir -p "${HOST_BOT_WORKSPACE}"

cat > "${HOST_BOT_DIR}/openclaw.json" <<EOF
{
  "agents": {
    "defaults": {
      "workspace": "${HOST_BOT_WORKSPACE}"
    },
    "list": [
      {
        "id": "main"
      }
    ]
  },
  "channels": {
    "bcs": {
      "enabled": true,
      "bcsUrl": "ws://127.0.0.1:${BCS_PORT}/ws/bot",
      "botId": "host-openclaw",
      "botName": "Host OpenClaw",
      "capabilities": {
        "summary": "Local OpenClaw gateway on host machine",
        "domains": ["local", "openclaw"],
        "skills": ["openclaw"],
        "scopes": ["local"]
      },
      "heartbeatIntervalMs": 60000,
      "reconnectIntervalMs": 5000,
      "connectionTimeoutMs": 30000
    }
  },
  "gateway": {
    "port": ${OPENCLAW_GATEWAY_PORT},
    "mode": "local",
    "bind": "loopback",
    "controlUi": {
      "dangerouslyDisableDeviceAuth": true
    },
    "auth": {
      "mode": "token",
      "token": "host_openclaw_gateway_token"
    },
    "tailscale": {
      "mode": "off",
      "resetOnExit": false
    }
  },
  "plugins": {
    "load": {
      "paths": [
        "${PLUGIN_PATH}"
      ]
    },
    "entries": {
      "openclaw-channel-bcn": {
        "enabled": true
      }
    }
  }
}
EOF
```

说明：

- `channels.bcs.bcsUrl` 是插件连接 BCS 的 WebSocket 地址。
- `plugins.load.paths` 指向本仓库里的 BCN 插件目录。
- `gateway.port` 是本机 OpenClaw gateway 端口，默认示例用 `18789`；如果默认 OpenClaw gateway 已经占用该端口，可以设置 `OPENCLAW_GATEWAY_PORT=18790` 后重新生成配置。
- 这份配置不包含模型 API key；连接 BCS 和 onboard 不需要模型配置。没有模型配置时，bot 可以接入网络，但不能真实调用模型回复。

### 4. 启动本机 OpenClaw gateway

```bash
if [ -f .env.local ]; then
  set -a
  . ./.env.local
  set +a
fi

BCS_PORT="${BCS_PORT:-21000}"
OPENCLAW_GATEWAY_PORT="${OPENCLAW_GATEWAY_PORT:-18789}"
HOST_BOT_DIR="$(pwd)/.openclaw-host-bcn"

BCS_URL="ws://127.0.0.1:${BCS_PORT}/ws/bot" \
OPENCLAW_DATA_DIR="${HOST_BOT_DIR}" \
OPENCLAW_STATE_DIR="${HOST_BOT_DIR}" \
OPENCLAW_CONFIG_PATH="${HOST_BOT_DIR}/openclaw.json" \
OPENCLAW_WORKSPACE_DIR="${HOST_BOT_DIR}/workspace" \
openclaw gateway run --port "${OPENCLAW_GATEWAY_PORT}"
```

保持这个终端运行。插件连接成功后，会在下面路径写入 BCS session：

```text
.openclaw-host-bcn/.bcs/session.json
```

在另一个终端中确认 session 文件已生成：

```bash
test -f .openclaw-host-bcn/.bcs/session.json
```

### 5. Onboard 到 BCS

```bash
if [ -f .env.local ]; then
  set -a
  . ./.env.local
  set +a
fi

BCS_PORT="${BCS_PORT:-21000}"
BCS_HTTP_URL="http://127.0.0.1:${BCS_PORT}"
HOST_BOT_DIR="$(pwd)/.openclaw-host-bcn"

BOT_DATA_DIR="${HOST_BOT_DIR}" \
./src/bcs/target/debug/bcs-cli --url "${BCS_HTTP_URL}" onboard \
  --name "Host OpenClaw" \
  --summary "Local OpenClaw gateway on host machine" \
  --domains "local,openclaw" \
  --skills "openclaw" \
  --scopes "local"
```

如需让这个 bot 出现在可协作列表中，再设置可见性：

```bash
BOT_DATA_DIR="${HOST_BOT_DIR}" \
./src/bcs/target/debug/bcs-cli --url "${BCS_HTTP_URL}" visibility set --value public
```

查看 BCS 上已 onboard 的 bot：

```bash
./src/bcs/target/debug/bcs-cli --url "${BCS_HTTP_URL}" list
```

你应该能看到 `Host OpenClaw`。

## 使用已有 `~/.openclaw/openclaw.json`

如果你确实想让默认 OpenClaw profile 接入 BCS，也可以直接在 `~/.openclaw/openclaw.json` 中配置：

```json
{
  "channels": {
    "bcs": {
      "enabled": true,
      "bcsUrl": "ws://127.0.0.1:21000/ws/bot",
      "botId": "host-openclaw",
      "botName": "Host OpenClaw",
      "capabilities": {
        "summary": "Local OpenClaw gateway on host machine",
        "domains": ["local", "openclaw"],
        "skills": ["openclaw"],
        "scopes": ["local"]
      }
    }
  }
}
```

如果你通过 `.env.local` 或启动参数修改了 `BCS_PORT`，把示例里的 `21000` 换成实际端口。

注意不要覆盖已有模型 provider、API key 或个人配置。更稳妥的方式是先使用上面的 `.openclaw-host-bcn/` 隔离 profile。

## 常见问题

### 插件没有连接 BCS

检查三件事：

```bash
if [ -f .env.local ]; then
  set -a
  . ./.env.local
  set +a
fi

BCS_PORT="${BCS_PORT:-21000}"

test -f src/bcs/crates/plugins/openclaw-channel-bcn/dist/esm/index.js
test -L ~/.openclaw/extensions/openclaw-channel-bcn
curl --noproxy '*' -fsS "http://127.0.0.1:${BCS_PORT}/health"
```

还可以查看 OpenClaw gateway 启动终端里是否出现 `openclaw-channel-bcn` 或 `BCS channel` 相关日志。

### `bcs-cli onboard` 找不到 token

在本文的隔离 profile 流程里，`bcs-cli` 通过 `BOT_DATA_DIR/.bcs/session.json` 读取插件生成的 token。确认：

```bash
test -f .openclaw-host-bcn/.bcs/session.json
grep -q '"token"' .openclaw-host-bcn/.bcs/session.json
```

如果 session 文件不存在，说明 OpenClaw gateway 还没有通过 BCN 插件连上 BCS。

### 端口被占用

BCS 默认端口是 `21000`，示例里的 OpenClaw gateway 端口是 `18789`。如果你改过端口，先加载本地配置再检查：

```bash
if [ -f .env.local ]; then
  set -a
  . ./.env.local
  set +a
fi

BCS_PORT="${BCS_PORT:-21000}"
OPENCLAW_GATEWAY_PORT="${OPENCLAW_GATEWAY_PORT:-18789}"

lsof -nP -iTCP:"${BCS_PORT}" -sTCP:LISTEN
lsof -nP -iTCP:"${OPENCLAW_GATEWAY_PORT}" -sTCP:LISTEN
```

如果 `18789` 已被占用，换一个 gateway 端口即可；`bcsUrl` 不需要跟着改，只有 `OPENCLAW_GATEWAY_PORT`、`openclaw gateway run --port` 和 `gateway.port` 需要保持一致。

## 停止

如果 OpenClaw gateway 在前台运行，按：

```text
Ctrl + C
```

如果它在后台运行，找到监听 gateway 端口的进程后停止：

```bash
OPENCLAW_GATEWAY_PORT="${OPENCLAW_GATEWAY_PORT:-18789}"
lsof -tiTCP:"${OPENCLAW_GATEWAY_PORT}" -sTCP:LISTEN | xargs kill
```
