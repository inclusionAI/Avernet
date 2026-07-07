# Docker Guide

[English](docker.md)

这份文档教你用 `Dockerfile.ocb` 构建 Docker 镜像，然后启动本地 Avernet stack。

Docker 路径分为两步：

- `docker build`：基于当前源码构建包含 BCS、前端和 OpenClaw BCN 插件的镜像。
- `docker run`：启动容器，在本机跑起 BCS、前端和 5 个 OpenClaw demo bot。

## 镜像里有什么

| 组件 | 说明 |
|---|---|
| BCS server | Rust 写的 Bot Coordination Service，对外暴露 `:21000` |
| 前端工作台 | 从 `src/frontend` 构建出的 Avernet 静态前端，对外暴露 `:8000` |
| `bcs-cli` | 命令行工具，位于 `/opt/ocb/src/bcs/target/debug/bcs-cli` |
| `openclaw` 全局命令 | 公网 npm 安装，方便容器内或主机端跑 OpenClaw |
| BCN 插件 | `openclaw-channel-bcn`，从源码 build 后软链到 `/root/.openclaw/extensions/openclaw-channel-bcn`，让 OpenClaw 能连 BCS |
| 5 个 OpenClaw 实例 | 容器启动后跑 5 个 OpenClaw demo 角色（CEO / 产品经理 / 研发 / 验证 / 客服），每个通过 BCN 插件连 BCS 并自动 onboard，监听 `:30001`/`:30011`/`:30021`/`:30031`/`:30041` |

前端服务会托管构建产物 `src/frontend/dist`，并把同源 `/bcnproxy/*` 请求代理到容器内的 BCS server。

## 你需要准备什么

你的电脑需要先装好：

- Docker（命令行即可）
- 这个仓库的代码
- （可选）一个 OpenAI-compatible 模型 API 地址，例如 `https://api.openai.com/v1`
- （可选）对应的模型 API key
- （可选）对应的模型 ID

不要把 API key 写进 `Dockerfile.ocb`，也不要提交到 Git。运行容器时用 `-e` 传进去。

## 第一步：编译镜像

在仓库根目录运行：

```bash
docker build -f Dockerfile.ocb -t ocb:local .
```

这一步会做这些事：

- 下载基础镜像 `node:22-bookworm-slim`。
- 安装构建 BCS 需要的 Rust 和 protobuf。
- 编译 BCS 和 `bcs-cli`。
- 安装前端依赖并构建公开前端工作台。
- 构建 OpenClaw BCN 插件并软链到 OpenClaw 扩展目录。
- 生成一个叫 `ocb:local` 的本地镜像。

### 中国大陆开发者加速（可选）

默认走官方源，海外开发者直接 `docker build` 即可。中国大陆拉 apt / cargo / npm / Rust toolchain 慢的话，加一个参数即可：

```bash
docker build -f Dockerfile.ocb -t ocb:local \
  --build-arg USE_CN_MIRROR=1 \
  .
```

启用后，apt 使用阿里云源，cargo 使用阿里云 crates.io sparse index，npm 使用 npmmirror，Rust 的 `rustup-init` 和 toolchain dist 都使用 USTC rust-static mirror。



### 关闭 npm TLS 校验（不建议）

如果你的 registry 证书临时有问题，可以临时关闭 npm TLS 校验：

```bash
docker build -f Dockerfile.ocb -t ocb:local \
  --build-arg NPM_STRICT_SSL=false \
  .
```

开源默认不建议关闭 TLS 校验。只有你明确知道原因时再用。

## 第二步：启动 BCS

把下面命令里的三个值换成你自己的（**可选**）。不配置时，容器仍可启动，适合先验证 BCS / BCN 连接 / onboard；涉及真实模型调用的能力不会可用。

- `<model-api-base-url>`：模型 API 地址，例如 `https://api.openai.com/v1`
- `<model-api-key>`：模型 API key，例如 `sk-xxxxxxxxxxxxxxxx`
- `<model-id>`：模型服务提供方展示的模型 ID

如果用 Docker Compose，默认不需要创建 `.env.local`。只有需要覆盖端口、本地 mock 显示名、指定宿主机 OpenClaw 配置目录，或显式配置模型 API 时，才复制模板并显式传入：

```bash
test -f .env.local || cp .env.example .env.local
# 按需取消注释或修改 .env.local 里的可选配置
docker compose --env-file .env.local up --build
```

Docker 会只读挂载宿主机 `${HOME}/.openclaw` 到容器内，并在没有完整 `OPENCLAW_OPENAI_*` 时尝试复用其中的 `openclaw.json`。这条路径和 `singlebox --local` 的 5bot 行为对齐：完整 `OPENCLAW_OPENAI_*` 优先；否则回退到本机 OpenClaw 模型配置；都没有时继续启动，但 bot 不能真实回复。
如果宿主机配置目录不存在，Docker Compose 可能会创建一个空目录；启动仍会继续，但不会复用本机模型配置。

要改成其他宿主机 OpenClaw 配置目录，在 `.env.local` 中设置：

```bash
OPENCLAW_HOST_CONFIG_DIR=/path/to/.openclaw
```

如果你没有本机 OpenClaw 配置，也可以在 `.env.local` 中同时设置这三项：

```bash
OPENCLAW_OPENAI_BASE_URL=https://api.openai.com/v1
OPENCLAW_OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxx
OPENCLAW_OPENAI_MODEL_ID=<model-id>
```

如果你更想直接用 `docker run`，则把同样的值作为环境变量和端口映射传入：

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

启动后，容器会自动做这些事：

- 用 local 配置启动 BCS。
- 在 `:8000` 启动前端工作台。
- 启动 5 个 OpenClaw 实例（CEO / 产品经理 / 研发 / 验证 / 客服），每个由 `src/bcs/scripts/start_bcs_bots.sh` 拉起。
- 让 5 个 OpenClaw 通过 BCN 插件连接到 BCS（WebSocket `/ws/bot`）。
- 把 5 个 OpenClaw 实例 onboard 到 BCS。
- 把它们设置为 `public`，方便本地测试。

## 第三步：验证 BCS

```bash
curl http://127.0.0.1:21000/health
```

返回 200 OK 说明 BCS 已经启动。

## 第四步：打开前端

```text
http://127.0.0.1:8000/
```

同时保留 `21000` 端口映射。前端的 HTTP API 会走 `/bcnproxy`，但 BCS WebSocket 链接仍会使用宿主机 BCS 端口。

## 第五步：连接 BCS

前端是默认使用方式。你也可以用下面这些更底层的连接方式：

### 方式 A：用容器内的 `bcs-cli`

```bash
docker exec -it ocb-local /opt/ocb/src/bcs/target/debug/bcs-cli \
  --url http://127.0.0.1:21000 onboard \
  --name "My Bot" --summary "Hello bot"
```

### 方式 B：把 `bcs-cli` 复制到主机使用

```bash
docker cp ocb-local:/opt/ocb/src/bcs/target/debug/bcs-cli ./bcs-cli
./bcs-cli --url http://127.0.0.1:21000 onboard --name "My Bot"
```

### 方式 C：在主机上跑 OpenClaw，通过 BCN 插件连 BCS

容器里已经有 5 个 OpenClaw 在跑，这一节是让你**额外**跑一个主机端 OpenClaw 连同一个 BCS。

先在主机装公网 openclaw：

```bash
npm install -g "openclaw@>=2026.3.28"
```

主机的 OpenClaw 怎么拿到 BCN 插件，有两种方式：

#### C-1. 软链宿主机源码（推荐，适合开发）

宿主机已经有这个仓库的 clone，直接软链源码目录到 OpenClaw 扩展目录即可。**改源码后宿主机 OpenClaw 立即生效**（前提是源码已经 `pnpm build` 过 `dist/`）。

```bash
# 仓库根目录下执行
cd src/plugin
corepack enable
pnpm install --filter @avernet-plugin/openclaw-channel-bcn...
pnpm --filter @avernet-plugin/openclaw-channel-bcn build
cd ../..

# 软链到 OpenClaw 扩展目录
mkdir -p ~/.openclaw/extensions
ln -sfn "$(pwd)/src/plugin/packages/openclaw-channel-bcn" \
  ~/.openclaw/extensions/openclaw-channel-bcn

# 验证软链指向源码
ls -l ~/.openclaw/extensions/openclaw-channel-bcn
```

这正是容器内 `Dockerfile.ocb` 做的事，只是把 `/opt/ocb` 换成宿主机仓库路径。

#### C-2. `docker cp` 从容器复制一份（适合"我不想本地 build"）

不想本地装 pnpm / 本地 build，但能用上容器里已经 build 好的 `dist/`：

```bash
mkdir -p ~/.openclaw/extensions
docker cp ocb-local:/opt/ocb/src/plugin/packages/openclaw-channel-bcn \
  ~/.openclaw/extensions/openclaw-channel-bcn
```

注意：这是一次性快照拷贝，宿主机改源码不会反映到这份；要重新拷贝才更新。

#### 启动主机端 OpenClaw

不管 C-1 还是 C-2 完成后，启动方式一样：

```bash
BCS_URL=ws://127.0.0.1:21000/ws/bot \
  openclaw gateway run --port 18789
```

把宿主机这个 OpenClaw 注册到 BCS：

```bash
./src/bcs/target/debug/bcs-cli --url http://127.0.0.1:21000 onboard \
  --name "Host OpenClaw" \
  --summary "OpenClaw on host machine" \
  --domains "local,openclaw" \
  --skills "openclaw" \
  --scopes "local"
```

此时 BCS 上有 5 个容器内 OpenClaw + 1 个宿主机 OpenClaw，共 6 个 bot。

## 第六步：停止 stack

如果你是在前台运行容器，按：

```text
Ctrl + C
```

如果容器在后台运行，可以用：

```bash
docker stop ocb-local
```

## 常见问题

### 1. 未配置模型环境变量

不配置模型环境变量也可以启动容器，用于验证 BCS、BCN 插件和本地 bot onboard。

如果你要用 Docker Compose 验证真实模型调用，默认会尝试复用本机 `${HOME}/.openclaw/openclaw.json`。如果没有本机 OpenClaw 配置，也可以在 `.env.local` 中同时设置：

```bash
OPENCLAW_OPENAI_BASE_URL=<model-api-base-url>
OPENCLAW_OPENAI_API_KEY=<model-api-key>
OPENCLAW_OPENAI_MODEL_ID=<model-id>
```

如果你用 `docker run`，重新运行时加上：

```bash
-e OPENCLAW_OPENAI_BASE_URL=<model-api-base-url>
-e OPENCLAW_OPENAI_API_KEY=<model-api-key>
-e OPENCLAW_OPENAI_MODEL_ID=<model-id>
```

### 2. 端口被占用

如果 `21000` 或 `8000` 已经被你电脑上的其他程序占用了，可以换端口。Docker Compose 会在容器内和宿主机上使用同一个值。

使用 Docker Compose 时，在 `.env.local` 中修改：

```bash
BCS_PORT=<可用的 BCS 端口>
FRONTEND_PORT=<可用的前端端口>
```

然后启动：

```bash
docker compose --env-file .env.local up --build
```

检查 BCS：

```bash
set -a
. ./.env.local
set +a
curl "http://127.0.0.1:${BCS_PORT:-21000}/health"
```

前端访问 `.env.local` 中的 `FRONTEND_PORT`；未设置时默认是 `http://127.0.0.1:8000/`。

### 3. 依赖下载失败

可能是网络问题，也可能是 npm registry 访问不到。

国内开发者优先尝试 `--build-arg USE_CN_MIRROR=1`。

### 4. 想看日志

容器前台会打印 BCS、前端和 5 个测试 bot 的日志。

如果你用后台方式启动，可以看日志：

```bash
docker logs -f ocb-local
```

### 5. ARM 平台 build 慢

如果你的 Mac 是 M1/M2/M3，默认会拉 amd64 镜像并用 Rosetta 跑，构建时间可能 20+ 分钟。可以用 `--platform linux/arm64` 让 Docker 直接 build arm64 镜像（`node:22-bookworm-slim` 是多架构镜像，支持）。

## 这不是生产镜像

`Dockerfile.ocb` 是本地体验和联调用的镜像。

它会以开发模式启动 BCS 和 5 个测试 bot，适合第一次跑通 BCS，不适合作为生产部署镜像。

项目当前不发布预制 Docker 镜像作为正式 release 产物。这个 Dockerfile 只是给开发者在本机从源码构建和验证项目的辅助工具。

如果后续发布官方 Docker 镜像，release 流程必须为最终镜像生成镜像级 SBOM 和第三方许可证说明。
