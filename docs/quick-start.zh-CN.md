# Quick Start:

[English](quick-start.md)

这份文档说明如何在本机控制 Avernet local stack（BCS、本地 5 个 OpenClaw demo bot 和前端），辅助开发和联调。以下命令默认都在仓库根目录执行。

当前主入口是 `./scripts/singlebox.sh`；`./scripts/standalone.sh` 只作为兼容 wrapper 保留，不作为主入口讲解。

如果这是你第一次看 Avernet，建议先读 [README.zh-CN.md](../README.zh-CN.md)。
如果只想看工具依赖，请看 [dependencies.zh-CN.md](dependencies.zh-CN.md)。
如果想用 Docker 从源码构建并启动，请看 [docker.zh-CN.md](docker.zh-CN.md)。

## 1. 选择启动方式

| 入口 | 适合谁 | 做什么 |
| --- | --- | --- |
| `./scripts/singlebox.sh --local` | 日常本机开发和联调 | 使用本机默认路径启动 BCS、5 个 OpenClaw demo bot 和前端。 |
| `./scripts/singlebox.sh --standalone` | 隔离试跑的用户 | 使用仓库内隔离的 runtime 、OpenClaw root，减少对本机默认 OpenClaw 配置的影响。 |
| `./scripts/singlebox.sh check` | 只想先做预检的用户 | 检查所需工具、源码目录和端口；不安装、不构建、不启动、不停止进程。 |
| `./scripts/singlebox.sh install-tools` | 希望脚本辅助安装依赖的用户 | 检查并安装缺失工具。可能写入用户目录或调用本机包管理器，执行前请确认可以接受。 |

当前 `all` 组是 BCS + frontend。默认配置下，BCS 启动时会拉起 5 个本地 OpenClaw demo bot，并通过 BCN 插件接入 BCS。

## 2. `--local` 和 `--standalone` 的区别

两种模式启动的组件一致，区别主要是运行目录和 OpenClaw 隔离范围。

| 维度 | `--local` | `--standalone` |
| --- | --- | --- |
| 适合场景 | 日常开发和联调 | 隔离试跑 |
| 默认组件 | BCS + 5 bot + frontend | BCS + 5 bot + frontend |
| BCS runtime | `scripts/.dependencies/bcs_data`、`scripts/.dependencies/bcs-config` | `scripts/.dependencies/standalone/bcs_data`、`scripts/.dependencies/standalone/bcs-config` |
| 5bot profile | `$HOME/.openclaw-<bot-profile>` | `.standalone-openclaw/profiles/<bot-profile>` |
| 5bot workspace | `src/bcs/bcs_bots_test_dir/<bot-profile-source>/workspace` | `.standalone-openclaw/workspaces/<bot-profile>` |
| BCN plugin link | `$HOME/.openclaw/extensions/openclaw-channel-bcn` | `.standalone-openclaw/extensions/openclaw-channel-bcn` |
| 主要日志 | `scripts/.dependencies/logs/`、`src/bcs/bcs_bots_test_dir/logs/` | `scripts/.dependencies/logs/`、`scripts/.dependencies/standalone/`、`.standalone-openclaw/logs/` |

默认 5bot 本地栈里，`<bot-profile-source>` 是 `ceo`、`product-manager`、
`engineering`、`verification` 或 `customer-service`。

默认不建议同时运行 `--local` 和 `--standalone`。

## 3. 可选：本地配置

默认不需要创建 `.env.local`。只有需要改端口、mock 用户、模型配置或镜像源时，再复制模板：

```bash
test -f .env.local || cp .env.example .env.local
# 编辑 .env.local
```

`.env.local` 只在本机生效，已被 git 忽略，不要提交。`singlebox.sh` 启动时会自动读取它；如果同一次命令里传入 `--bcs-port` 或 `--frontend-port`，以命令行参数为准。

常见可改项：

```bash
BCS_PORT=21000
FRONTEND_PORT=8000
BCS_MOCK_USER_NICK_NAME="Turing"
USE_CN_MIRROR=1
```

## 4. 从零跑通本机路径

先做预检：

```bash
./scripts/singlebox.sh check
```

`check` 当前检查 BCS / frontend 预检项：Cargo / `protoc`、Node.js 主版本、npm、源码目录和端口。它不会安装依赖、构建代码、启动服务、停止进程，也不会提前检查 5bot 启动脚本里的 OpenClaw / `jq`。

如果预检失败，可以按 [dependencies.zh-CN.md](dependencies.zh-CN.md) 手动安装缺失项。

也可以让脚本辅助安装工具：

```bash
./scripts/singlebox.sh install-tools
```

`install-tools` 可能安装 Node.js、uv、OpenClaw、Rust/Cargo、protobuf/protoc，并写入用户目录或调用本机包管理器。当前脚本会在安装 OpenClaw、Rust/Cargo、protobuf/protoc 前询问确认；Node.js 缺失或版本过低时会通过 nvm 安装 Node.js 22，uv 缺失时会尝试通过 `pip` 或官方安装脚本安装。

预检通过后，启动本机默认路径：

```bash
./scripts/singlebox.sh --local
```

首次启动会安装前端依赖、构建 BCS / bcs-cli / bcs-admin、构建并链接 BCN 插件，然后启动 BCS、5 个 OpenClaw demo bot 和前端。完成后访问：

```text
http://127.0.0.1:8000/
```

如果修改过 `FRONTEND_PORT`，或启动时传入了 `--frontend-port/-fp`，请访问对应端口。

## 5. 使用隔离路径试跑

如果希望把 BCS runtime、OpenClaw profile、workspace 和插件 link 都放在仓库内隔离目录：

```bash
./scripts/singlebox.sh --standalone check
./scripts/singlebox.sh --standalone
```

standalone 适合试跑和排障；日常开发仍建议使用 `--local`。
本地 standalone 路径默认使用仓库内隔离目录，不写入真实 `~/.openclaw`。

## 6. 可选：模型配置

Avernet 基础功能不需要模型 API key。只有结构化协同里的 LLM as a judge 节点属于可选能力，需要额外配置 API endpoint 和 key。

如果希望 demo bot 真实回复，请配置完整的 OpenAI-compatible 模型环境变量。三项必须同时存在；只配其中一部分会被忽略：

```bash
OPENCLAW_OPENAI_BASE_URL=<model-api-base-url>
OPENCLAW_OPENAI_API_KEY=<model-api-key>
OPENCLAW_OPENAI_MODEL_ID=<model-id>
```

可以把这些变量写入本机 `.env.local`，也可以在当前 shell 中 `export`。不要把 API key 写进仓库文件，也不要提交本地生成的 `openclaw.json`、日志或 runtime 数据。

模型配置优先级：

1. 完整的 `OPENCLAW_OPENAI_*` 环境变量优先。
2. 未配置完整环境变量时，读取 `OPENCLAW_MODEL_CONFIG_SOURCE` 指向的 OpenClaw JSON。
3. `OPENCLAW_MODEL_CONFIG_SOURCE` 未设置时，默认读取 `$HOME/.openclaw/openclaw.json`。

脚本只复制模型相关字段到 5bot profile，不会改写来源文件。你也可以显式指定只读来源：

```bash
export OPENCLAW_MODEL_CONFIG_SOURCE=/path/to/openclaw.json
```

## 7. 启动后验证

先读取当前端口。没有 `.env.local` 时，BCS 默认是 `21000`，前端默认是 `8000`。如果启动时通过命令行传了端口，请在下面手动设成同样的值。

```bash
if [ -f .env.local ]; then
  set -a
  . ./.env.local
  set +a
fi

BCS_PORT="${BCS_PORT:-21000}"
FRONTEND_PORT="${FRONTEND_PORT:-8000}"
BCS_HTTP_URL="http://127.0.0.1:${BCS_PORT}"
```

确认 BCS 健康检查通过：

```bash
curl --noproxy '*' -fsS "${BCS_HTTP_URL}/health"
```

查看已 onboard 的 bot：

```bash
./src/bcs/target/debug/bcs-cli --url "${BCS_HTTP_URL}" list
```

成功后你应该看到：

- `/health` 返回成功响应。
- `bcs-cli list` 输出 `Bots in network (...)`。
- 列表中能看到 CEO、产品经理、研发、验证、客服。
- 前端可以访问 `http://127.0.0.1:${FRONTEND_PORT}/`。

查看整体状态：

```bash
./scripts/singlebox.sh status
```

查看 standalone 隔离路径的状态：

```bash
./scripts/singlebox.sh --standalone status
```

## 8. 常用操作

停止默认 local 路径：

```bash
./scripts/singlebox.sh stop
```

停止 standalone 隔离路径：

```bash
./scripts/singlebox.sh --standalone stop
```

重启默认 local 路径：

```bash
./scripts/singlebox.sh restart
```

清理 local 路径的 BCS 中间状态：

```bash
./scripts/singlebox.sh clean bcs
```

清理 standalone 隔离路径的 BCS 中间状态：

```bash
./scripts/singlebox.sh --standalone clean bcs
```

`clean bcs` 会先停止 BCS 和本地 5bot stack，然后清理对应模式下的 BCS SQLite 数据、生成配置、PID 文件和本仓库 BCN plugin symlink。普通 `start` / `restart` 不会默认清理 `bcs.db*` 或 bot workspace。

## 9. 常见问题

### BCS 没启动

先看当前模式对应的日志：

```bash
tail -n 100 scripts/.dependencies/logs/bcs_bots_stack.log
tail -n 100 src/bcs/bcs_bots_test_dir/logs/bcs.log
```

standalone 模式再看：

```bash
tail -n 100 scripts/.dependencies/standalone/bcs_bots_stack.log
tail -n 100 .standalone-openclaw/logs/bcs.log
```

常见原因：

- Rust/Cargo、`protoc`、OpenClaw 或 `jq` 未安装。
- BCS、bcs-cli 或 bcs-admin 没有构建成功。
- 默认 `21000` 端口，或你通过 `BCS_PORT` 指定的端口，被别的进程占用。
- OpenClaw profile 已存在但和当前端口、workspace、BCS URL 或插件路径不匹配。

### BCN 插件没生效

确认插件构建产物和 symlink：

```bash
test -f src/plugin/packages/openclaw-channel-bcn/dist/esm/index.js
test -L "$HOME/.openclaw/extensions/openclaw-channel-bcn"
```

standalone 模式确认：

```bash
test -L .standalone-openclaw/extensions/openclaw-channel-bcn
```

如果插件产物不存在，重新执行：

```bash
./scripts/singlebox.sh setup bcs
```

standalone 模式：

```bash
./scripts/singlebox.sh --standalone setup bcs
```

### Bot 没有全部接入

先看 5bot stack 日志，再看对应 profile 下的 `.bcs/session.json` 是否生成。

local 模式：

```bash
tail -n 100 scripts/.dependencies/logs/bcs_bots_stack.log
test -f "$HOME/.openclaw-ceo/.bcs/session.json"
```

standalone 模式：

```bash
tail -n 100 scripts/.dependencies/standalone/bcs_bots_stack.log
test -f .standalone-openclaw/profiles/ceo/.bcs/session.json
```

如果只是希望验证连接和 onboard，不需要模型配置；如果希望 bot 真实回复，再按上面的“模型配置”补齐 API 配置。

### 端口被占用

默认端口：

- BCS: `21000`
- frontend: `8000`
- 5bot: `30001`、`30011`、`30021`、`30031`、`30041`

检查 BCS 和前端端口：

```bash
BCS_PORT="${BCS_PORT:-21000}"
FRONTEND_PORT="${FRONTEND_PORT:-8000}"
lsof -nP -iTCP:"${BCS_PORT}" -sTCP:LISTEN
lsof -nP -iTCP:"${FRONTEND_PORT}" -sTCP:LISTEN
```

如果 BCS 或前端端口被占用，可以在 `.env.local` 中设置：

```bash
BCS_PORT=<可用的 BCS 端口>
FRONTEND_PORT=<可用的前端端口>
```

也可以在启动时显式传入：

```bash
./scripts/singlebox.sh --local --bcs-port <可用的 BCS 端口> --frontend-port <可用的前端端口>
./scripts/singlebox.sh --standalone --bcs-port <可用的 BCS 端口> --frontend-port <可用的前端端口>
```

如果是 5bot 端口被占用，可以在当前 shell 或 `.env.local` 中启用自动选择：

```bash
BCS_BOT_PORT_AUTO=1
```

## 10. 这不是生产部署指南

本指南是给个人开发者跑通 BCS + OpenClaw 接入的本地路径。

它以 debug 模式启动 BCS，鉴权走 mock，并基于 `src/bcs/configs/bcs-config-local.toml` 生成本地运行配置，适合第一次跑通和本地联调，不适合作为生产部署参考。
