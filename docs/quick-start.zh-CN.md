# Quick Start:

[English](quick-start.md)

这份文档说明如何在本机控制 Avernet local stack（BCS、本地 5 个 OpenClaw demo bot 和前端），辅助开发和联调。以下命令默认都在仓库根目录执行。

当前主入口是 `./singlebox/singlebox.sh`；`./singlebox/standalone.sh` 只作为兼容 wrapper 保留，不作为主入口讲解。

如果这是你第一次看 Avernet，建议先读 [README.zh-CN.md](../README.zh-CN.md)。
如果只想看工具依赖，请看 [dependencies.zh-CN.md](dependencies.zh-CN.md)。
如果想用 Docker 从源码构建并启动，请看 [docker.zh-CN.md](docker.zh-CN.md)。

## 1. 选择启动方式

| 入口 | 适合谁 | 做什么 |
| --- | --- | --- |
| `./singlebox/singlebox.sh` | 日常本机开发、首次试跑用户 | 使用仓库内隔离 runtime 启动 BAAS、backend、BCS、5 个 OpenClaw demo bot、demo bot 和前端。 |
| `./singlebox/singlebox.sh --standalone` | 兼容旧文档或旧脚本 | 默认隔离 singlebox 模式的显式别名。 |
| `./singlebox/singlebox.sh check` | 只想先做预检的用户 | 检查所需工具、源码目录和端口；不安装、不构建、不启动、不停止进程。 |
| `./singlebox/singlebox.sh install-tools` | 希望脚本辅助安装依赖的用户 | 检查并安装缺失工具。可能写入用户目录或调用本机包管理器，执行前请确认可以接受。 |

当前 `all` 组会启动 BAAS、backend、BCSFuse、BCS、5 个本地 OpenClaw demo bot、demo bot 和 frontend。`start bcs` 只启动 BCS；需要 BCS 和 5 个 demo bot 时使用 `start bcs_bots`。`bcs_frontend` 使用默认的 `legacy` 前端，其他前端请参考 [前端启动说明](singlebox-nextgen-local.md)。

## 2. 运行目录隔离

`singlebox.sh` 只保留一种支持模式：隔离 singlebox 模式。它默认不会把 5bot profile、workspace 和插件链接写入本机默认 OpenClaw 目录。

| 维度 | 路径 |
| --- | --- |
| BCS runtime | `singlebox/.dependencies/standalone/bcs_data`、`singlebox/.dependencies/standalone/bcs-config` |
| 5bot profile | `.standalone-openclaw/profiles/<bot-profile>` |
| 5bot workspace | `.standalone-openclaw/workspaces/<bot-profile>` |
| BCN plugin link | `.standalone-openclaw/extensions/openclaw-channel-bcn` |
| 主要日志 | `singlebox/.dependencies/logs/`、`singlebox/.dependencies/standalone/`、`.standalone-openclaw/logs/` |

默认 5bot 本地栈里，`<bot-profile-source>` 是 `ceo`、`product-manager`、
`engineering`、`verification` 或 `customer-service`。

默认端口同一时间只能被一个 singlebox stack 使用，包括 `21000`、`8000` 和 `30001` 到 `30041`。

## 3. 可选：本地配置

默认不需要创建 `singlebox/.env.local`。只有需要改端口、mock 用户、模型配置或镜像源时，再复制模板：

```bash
test -f singlebox/.env.local || cp singlebox/.env.example singlebox/.env.local
# 编辑 singlebox/.env.local
```

`singlebox/.env.local` 只在本机生效，已被 git 忽略，不要提交。`singlebox.sh` 主入口会自动读取它；仓库根目录的 `.env.local` 不是本教程使用的本机配置文件。如果同一次命令里传入 `--bcs-port` 或 `--frontend-port`，以命令行参数为准。`--local` 已移除，隔离运行目录已是默认行为。

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
./singlebox/singlebox.sh check
```

`check` 默认检查当前 `all` 组的服务预检项，也可以指定 `check bcs_frontend` 或 `check bots`。它不会安装依赖、构建代码、启动服务或停止进程，但会初始化部分本机运行目录。

如果预检失败，可以按 [dependencies.zh-CN.md](dependencies.zh-CN.md) 手动安装缺失项。

也可以让脚本辅助安装工具：

```bash
./singlebox/singlebox.sh install-tools
```

`install-tools` 可能安装 Node.js、uv、OpenClaw、Rust/Cargo、protobuf/protoc，并写入用户目录或调用本机包管理器。当前脚本会在安装 OpenClaw、Rust/Cargo、protobuf/protoc 前询问确认；Node.js 缺失或版本过低时会通过 nvm 安装 Node.js 22，uv 缺失时会尝试通过 `pip` 或官方安装脚本安装。

运行 `singlebox.sh` 时也会安装仓库级 pre-push hook，即设置 `core.hooksPath=.githooks`。如果某次命令需要跳过 hook 安装，可以设置 `OCB_SKIP_GIT_HOOKS=1`。

预检通过后，启动默认隔离路径：

```bash
./singlebox/singlebox.sh
```

首次启动会安装前端依赖、构建 BCS / bcs-cli / bcs-admin、构建并链接 BCN 插件，然后启动 BAAS、backend、BCS、5 个 OpenClaw demo bot、demo bot 和前端。完成后访问：

```text
http://127.0.0.1:8000/
```

如果修改过 `FRONTEND_PORT`，或启动时传入了 `--frontend-port/-fp`，请访问对应端口。

## 5. 启动默认隔离路径

BCS runtime、OpenClaw profile、workspace 和插件 link 默认都放在仓库内隔离目录：

```bash
./singlebox/singlebox.sh check
./singlebox/singlebox.sh
```

`--standalone` 仍可作为默认模式的显式兼容写法。默认路径不写入真实 `~/.openclaw`。

## 6. 可选：模型配置

启动 Bot 或完整栈时，脚本会询问模型配置模式。可以在 `singlebox/.env.local` 中设置 `SINGLEBOX_MODEL_CONFIG_MODE`，跳过菜单：

| 模式 | 行为 |
| --- | --- |
| `mock` | 使用本地 mock 模型服务的固定格式回复，不需要真实 API key。 |
| `manual` | 要求下面三项 `OPENCLAW_OPENAI_*` 均为非空值，缺项直接报错，不会自动回退。 |
| `home` | 经确认后从 `~/.openclaw/openclaw.json` 导入模型字段。 |

希望 Bot 真实回复时，可以在 `singlebox/.env.local` 中取消注释并填写：

```dotenv
SINGLEBOX_MODEL_CONFIG_MODE=manual
OPENCLAW_OPENAI_BASE_URL=https://your-model-service.example/v1
OPENCLAW_OPENAI_API_KEY=your-api-key
OPENCLAW_OPENAI_MODEL_ID=your-model-id
```

使用 `home` 时，可通过 `OPENCLAW_MODEL_CONFIG_SOURCE` 指定其他只读 JSON 来源；非交互导入需要显式设置 `SINGLEBOX_MODEL_CONFIG_HOME_CONFIRMED=1`。脚本不会修改来源文件。未设置模式的非交互启动使用 `mock`，不会自动读取 home 配置。

仅启动 BCS / frontend 不会显示此菜单；真实 BCS Judge 调用需要启动环境中有完整模型参数。不要提交 API key、本地生成的 `openclaw.json`、日志或 runtime 数据。

## 7. 启动后验证

先读取当前端口。没有 `singlebox/.env.local` 时，BCS 默认是 `21000`，前端默认是 `8000`。如果启动时通过命令行传了端口，请在下面手动设成同样的值。

```bash
if [ -f singlebox/.env.local ]; then
  set -a
  . ./singlebox/.env.local
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
./apps/bcs/target/debug/bcs-cli --url "${BCS_HTTP_URL}" list
```

成功后你应该看到：

- `/health` 返回成功响应。
- `bcs-cli list` 输出 `Bots in network (...)`。
- 列表中能看到 CEO、产品经理、研发、验证、客服。
- 前端可以访问 `http://127.0.0.1:${FRONTEND_PORT}/`。

查看整体状态：

```bash
./singlebox/singlebox.sh status
```

查看隔离路径的状态：

```bash
./singlebox/singlebox.sh status
```

## 8. 常用操作

停止默认隔离路径：

```bash
./singlebox/singlebox.sh stop
```

重启默认隔离路径：

```bash
./singlebox/singlebox.sh restart
```

清理 BCS 中间状态：

```bash
./singlebox/singlebox.sh clean bcs
```

`clean bcs` 只停止 BCS，并删除其 SQLite 数据库和生成的运行配置；不会停止 Bot，也不会删除 Bot 身份、工作区或插件链接。如需停止 Bot，请先单独执行 `stop bots`。普通 `start` / `restart` 保留 `bcs.db*` 和 Bot 工作区。

## 9. 常见问题

### BCS 没启动

先看隔离 stack 日志：

```bash
tail -n 100 singlebox/.dependencies/standalone/bcs_bots_stack.log
tail -n 100 singlebox/.dependencies/logs/bcs.log
```

常见原因：

- Rust/Cargo、`protoc`、OpenClaw 或 `jq` 未安装。
- BCS、bcs-cli 或 bcs-admin 没有构建成功。
- 默认 `21000` 端口，或你通过 `BCS_PORT` 指定的端口，被别的进程占用。
- OpenClaw profile 已存在但和当前端口、workspace、BCS URL 或插件路径不匹配。

### BCN 插件没生效

确认插件构建产物和 symlink：

```bash
test -f apps/bcs/crates/plugins/openclaw-channel-bcn/dist/esm/index.js
test -L .standalone-openclaw/extensions/openclaw-channel-bcn
```

如果插件产物不存在，重新执行：

```bash
./singlebox/singlebox.sh setup bots
```

### Bot 没有全部接入

先看 5bot stack 日志，再看对应 profile 下的 `.bcs/session.json` 是否生成。

隔离路径：

```bash
tail -n 100 singlebox/.dependencies/standalone/bcs_bots_stack.log
test -f .standalone-openclaw/profiles/ceo/.bcs/session.json
```

如果只是希望验证连接和 onboard，选择 `mock` 即可；如果希望 Bot 真实回复，请按上面的“模型配置”选择 `manual` 或 `home`。

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

如果 BCS 或前端端口被占用，可以在 `singlebox/.env.local` 中设置：

```bash
BCS_PORT=<可用的 BCS 端口>
FRONTEND_PORT=<可用的前端端口>
```

也可以在启动时显式传入：

```bash
./singlebox/singlebox.sh --bcs-port <可用的 BCS 端口> --frontend-port <可用的前端端口>
```

如果是 5bot 端口被占用，可以在当前 shell 或 `singlebox/.env.local` 中启用自动选择：

```bash
BCS_BOT_PORT_AUTO=1
```

## 10. 这不是生产部署指南

本指南是给个人开发者跑通 BCS + OpenClaw 接入的本地路径。

它以 debug 模式启动 BCS，鉴权走 mock，并基于 `apps/bcs/configs/bcs-config-local.toml` 生成本地运行配置，适合第一次跑通和本地联调，不适合作为生产部署参考。
